#!/usr/bin/env python3
"""
hermes_reminder.py - Hermes Agent Lebensplan & Smart Reminder Module
Outer Heaven / Project Outer Heaven

Features:
1. Extraktion: Extrahiert Zeitpunkte und Vorhaben ("Ich habe morgen vor X, erinnere mich um 15 Uhr")
   aus natürlichsprachigen Chat-Nachrichten.
2. Persistenz: Speichert alle Termine persistent in `lebensplan.json`.
3. Background-Scheduling: Autonomer Background-Scheduler, der fällige Termine minütlich prüft.
4. Push-Dispatch: Bei Fälligkeit wird die bestehende Hermes Push-Pipeline ausgelöst (Telegram & Desktop).
5. Smart Speaker API (Xiaomi TTS): Optionale Sprachausgabe über konfigurierbaren HTTP-Post Webhook,
   abgesichert durch den Feature-Toggle SPEAKER_ENABLED=false.
"""

import os
import sys
import re
import json
import time
import logging
import urllib.request
import urllib.error
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union

# Logging-Setup
logger = logging.getLogger("hermes-reminder")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [hermes-reminder]: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Pfade
BASE_DIR = Path(__file__).resolve().parent
LEBENSPLAN_FILE = BASE_DIR / "lebensplan.json"
TOKEN_FILE = Path("/mnt/storage-alpha/secrets/telegram_outerheaven.token")
ENV_FILE = Path("/home/outerheaven/.hermes/.env")
USER_ENV_FILE = Path.home() / ".hermes" / ".env"

# Feature Toggles & Konfiguration
SPEAKER_ENABLED_DEFAULT = False
DEFAULT_SPEAKER_WEBHOOK = "http://127.0.0.1:8123/api/webhook/xiaomi_speaker_tts"


def is_speaker_enabled() -> bool:
    """Prüft den Feature-Toggle für den Smart Speaker (Standard: False)."""
    raw = os.getenv("SPEAKER_ENABLED", "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


def get_speaker_webhook_url() -> str:
    """Gibt den Endpunkt für die Smart Speaker TTS-Ausgabe zurück."""
    return os.getenv("SPEAKER_WEBHOOK_URL", DEFAULT_SPEAKER_WEBHOOK).strip()


# ==============================================================================
# Telegram & Push Credentials
# ==============================================================================

def load_telegram_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Lädt Bot-Token und Chat-ID für die Telegram-Benachrichtigung aus:
    1. Umgebungsvariablen
    2. /mnt/storage-alpha/secrets/telegram_outerheaven.token
    3. ~/.hermes/.env bzw. /home/outerheaven/.hermes/.env
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed = os.getenv("TELEGRAM_ALLOWED_USERS") or os.getenv("TELEGRAM_CHAT_ID")

    if not token and TOKEN_FILE.is_file():
        try:
            tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok:
                token = tok
        except Exception as e:
            logger.debug("Konnte Token nicht aus %s lesen: %s", TOKEN_FILE, e)

    env_candidates = [ENV_FILE, USER_ENV_FILE]
    for ef in env_candidates:
        if (not token or not allowed) and ef.is_file():
            try:
                for line in ef.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN=") and not token:
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif (line.startswith("TELEGRAM_ALLOWED_USERS=") or line.startswith("TELEGRAM_HOME_CHANNEL=")) and not allowed:
                        allowed = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception as e:
                logger.debug("Konnte %s nicht lesen: %s", ef, e)

    chat_id = None
    if allowed:
        chat_id = allowed.split(",")[0].strip()

    return token, chat_id


def send_telegram_notification(text: str) -> bool:
    """Sendet eine Textnachricht über die bestehende Hermes Telegram-Pipeline."""
    token, chat_id = load_telegram_credentials()
    if not token or not chat_id:
        logger.warning("Keine Telegram-Credentials gefunden. Push übersprungen.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            success = (resp.status == 200)
            if success:
                logger.info("Telegram-Reminder erfolgreich versendet an Chat %s", chat_id)
            return success
    except Exception as e:
        logger.warning("Fehler beim Versenden der Telegram-Benachrichtigung: %s", e)
        return False


def send_desktop_notification(title: str, text: str) -> bool:
    """Sendet eine Desktop-Benachrichtigung via notify-send, falls verfügbar."""
    try:
        subprocess.run(["notify-send", "-a", "Hermes Lebensplan", title, text],
                       check=False, timeout=3, capture_output=True)
        return True
    except Exception:
        return False


# ==============================================================================
# Smart Speaker API (Subtask B)
# ==============================================================================

def dispatch_to_smart_speaker(text: str, endpoint: Optional[str] = None) -> Dict[str, Any]:
    """
    Subtask B: Smart Speaker API-Vorbereitung (Xiaomi / Home Assistant / TTS-Bridge).
    Setzt einen lokalen HTTP-POST an einen konfigurierbaren Endpunkt ab.
    Ausführung ist vorerst hinter dem Feature-Toggle SPEAKER_ENABLED=false geparkt.
    """
    enabled = is_speaker_enabled()
    target_url = endpoint or get_speaker_webhook_url()

    if not enabled:
        msg = f"[SMART-SPEAKER] Feature-Toggle SPEAKER_ENABLED=false: Sprachausgabe für '{text}' übersprungen."
        logger.info(msg)
        return {
            "status": "disabled",
            "enabled": False,
            "message": msg,
            "text": text,
            "target": target_url
        }

    payload = {
        "text": text,
        "message": text,
        "device": "xiaomi_speaker",
        "source": "hermes_reminder",
        "timestamp": datetime.now().isoformat()
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(target_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            status_code = resp.status
            logger.info("Smart-Speaker HTTP-POST an %s erfolgreich (Code %d)", target_url, status_code)
            return {
                "status": "success",
                "enabled": True,
                "status_code": status_code,
                "text": text,
                "target": target_url
            }
    except Exception as e:
        logger.warning("Smart-Speaker HTTP-POST fehlgeschlagen (%s): %s", target_url, e)
        return {
            "status": "failed",
            "enabled": True,
            "error": str(e),
            "text": text,
            "target": target_url
        }


# ==============================================================================
# Persistenz: lebensplan.json
# ==============================================================================

_lebensplan_lock = threading.Lock()

def load_lebensplan(filepath: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Lädt alle Einträge aus der lebensplan.json."""
    target = filepath or LEBENSPLAN_FILE
    with _lebensplan_lock:
        if not target.is_file():
            return []
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Konnte %s nicht laden: %s", target, e)
            return []


def save_lebensplan(entries: List[Dict[str, Any]], filepath: Optional[Path] = None) -> bool:
    """Speichert Einträge threadsicher und persistent in lebensplan.json."""
    target = filepath or LEBENSPLAN_FILE
    with _lebensplan_lock:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_file = target.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            temp_file.replace(target)
            return True
        except Exception as e:
            logger.error("Fehler beim Speichern von %s: %s", target, e)
            return False


def add_reminder(
    task_description: str,
    due_time: Union[datetime, str, float],
    push_notify: bool = True,
    speaker_broadcast: bool = False,
    raw_prompt: str = "",
    filepath: Optional[Path] = None
) -> Dict[str, Any]:
    """Fügt einen neuen Termin zum Lebensplan hinzu."""
    now = datetime.now()

    if isinstance(due_time, (int, float)):
        due_dt = datetime.fromtimestamp(due_time)
        due_timestamp = float(due_time)
        due_iso = due_dt.isoformat()
    elif isinstance(due_time, datetime):
        due_dt = due_time
        due_timestamp = due_dt.timestamp()
        due_iso = due_dt.isoformat()
    elif isinstance(due_time, str):
        try:
            due_dt = datetime.fromisoformat(due_time)
            due_timestamp = due_dt.timestamp()
            due_iso = due_dt.isoformat()
        except ValueError:
            parsed = parse_german_datetime(due_time, reference_time=now)
            if parsed:
                due_dt = parsed
                due_timestamp = due_dt.timestamp()
                due_iso = due_dt.isoformat()
            else:
                raise ValueError(f"Ungültiges Datumsformat: '{due_time}'")
    else:
        raise ValueError(f"Nicht unterstützter Datentyp für due_time: {type(due_time)}")

    clean_task = task_description.strip()
    if not clean_task:
        clean_task = "Geplante Aktivität"

    reminder_id = f"rem_{int(now.timestamp())}_{os.urandom(3).hex()}"
    new_item = {
        "id": reminder_id,
        "task_description": clean_task,
        "due_time": due_iso,
        "due_timestamp": due_timestamp,
        "created_at": now.isoformat(),
        "status": "pending",
        "channels": {
            "push": bool(push_notify),
            "speaker": bool(speaker_broadcast)
        },
        "raw_prompt": raw_prompt
    }

    entries = load_lebensplan(filepath)
    entries.append(new_item)
    save_lebensplan(entries, filepath)

    logger.info("Neuer Eintrag im Lebensplan angelegt: [%s] für %s ('%s')",
                reminder_id, due_iso, clean_task)
    return new_item


def get_reminders(status_filter: Optional[str] = None, filepath: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Gibt Termine aus dem Lebensplan zurück, optional gefiltert nach Status (pending, completed, cancelled)."""
    items = load_lebensplan(filepath)
    if not status_filter:
        return items
    target_status = status_filter.strip().lower()
    return [i for i in items if str(i.get("status", "")).lower() == target_status]


def cancel_reminder(reminder_id: str, filepath: Optional[Path] = None) -> bool:
    """Setzt den Status eines Eintrags auf cancelled."""
    items = load_lebensplan(filepath)
    found = False
    for item in items:
        if item.get("id") == reminder_id:
            item["status"] = "cancelled"
            item["cancelled_at"] = datetime.now().isoformat()
            found = True
            break
    if found:
        save_lebensplan(items, filepath)
    return found


def delete_reminder(reminder_id: str, filepath: Optional[Path] = None) -> bool:
    """Löscht einen Eintrag dauerhaft aus dem Lebensplan."""
    items = load_lebensplan(filepath)
    new_items = [i for i in items if i.get("id") != reminder_id]
    if len(new_items) != len(items):
        save_lebensplan(new_items, filepath)
        return True
    return False


# ==============================================================================
# Intelligente Datums- & Absichts-Extraktion (NLP / Regex)
# ==============================================================================

GERMAN_WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6
}

def parse_german_datetime(text: str, reference_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parst deutsche Zeitangaben relativ zu reference_time (Default: now).
    Unterstützt:
    - "in X Minuten / Stunden / Sekunden"
    - "heute um HH[:MM] [Uhr]"
    - "morgen um HH[:MM] [Uhr]"
    - "übermorgen um HH[:MM] [Uhr]"
    - "am Montag/Dienstag/... um HH[:MM] [Uhr]"
    - "am DD.MM.[YYYY] um HH[:MM] [Uhr]"
    """
    ref = reference_time or datetime.now()
    clean = text.lower().strip()

    # 1. "in X Sekunden / Minuten / Stunden"
    rel_match = re.search(r'\bin\s+(\d+)\s*(sekunden?|sek|minuten?|min|stunden?|std|h)\b', clean)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith("sek"):
            return ref + timedelta(seconds=val)
        elif unit.startswith("min"):
            return ref + timedelta(minutes=val)
        elif unit.startswith("std") or unit.startswith("stund") or unit == "h":
            return ref + timedelta(hours=val)

    # Hilfsfunktion zur Extraktion einer Uhrzeit "um HH[:MM] [Uhr]" oder "HH:MM Uhr"
    hour = 12
    minute = 0
    has_time = False

    time_patterns = [
        r'\bum\s+(\d{1,2}):(\d{2})\s*(?:uhr)?\b',
        r'\bum\s+(\d{1,2})\s*uhr\b',
        r'\b(\d{1,2}):(\d{2})\s*uhr\b',
        r'\bum\s+(\d{1,2})\b'
    ]
    for pat in time_patterns:
        m = re.search(pat, clean)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if len(m.groups()) >= 2 and m.group(2) else 0
            has_time = True
            break

    target_date = None

    # 2. "übermorgen"
    if "übermorgen" in clean:
        target_date = (ref + timedelta(days=2)).date()
    # 3. "morgen"
    elif "morgen" in clean:
        target_date = (ref + timedelta(days=1)).date()
    # 4. "heute"
    elif "heute" in clean:
        target_date = ref.date()
    # 5. Konkretes Datum "am DD.MM." oder "DD.MM.YYYY"
    date_match = re.search(r'\b(?:am\s+)?(\d{1,2})\.(\d{1,2})\.(?:\s*(\d{4})\b)?', clean)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3)) if date_match.group(3) else ref.year
        try:
            target_date = datetime(year, month, day).date()
        except ValueError:
            target_date = None

    # 6. Wochentag ("am Montag", "kommenden Freitag", etc.)
    if not target_date:
        for wd_name, wd_idx in GERMAN_WEEKDAYS.items():
            if re.search(rf'\b(?:am\s+|nächsten\s+|kommenden\s+)?{wd_name}\b', clean):
                days_ahead = (wd_idx - ref.weekday()) % 7
                if days_ahead == 0 and has_time and (hour < ref.hour or (hour == ref.hour and minute <= ref.minute)):
                    days_ahead = 7
                elif days_ahead == 0 and not has_time:
                    days_ahead = 7
                target_date = (ref + timedelta(days=days_ahead)).date()
                break

    # Wenn kein Tag explizit genannt wurde, aber eine Uhrzeit
    if not target_date and has_time:
        if hour < ref.hour or (hour == ref.hour and minute <= ref.minute):
            target_date = (ref + timedelta(days=1)).date()
        else:
            target_date = ref.date()

    if target_date:
        return datetime(target_date.year, target_date.month, target_date.day, hour, minute, 0)

    return None


def extract_reminder_intent(text: str, reference_time: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    Analysiert den Chattext nach Planungen, Vorhaben und Erinnerungen.
    Beispiel: "Ich habe morgen vor X, erinnere mich um 15 Uhr"
              "Erinnere mich in 10 Minuten an die Pizza"
              "Morgen um 14 Uhr Termin mit Dr. Müller"

    Gibt ein Dict zurück:
    {
        "due_time": datetime,
        "due_iso": str,
        "task_description": str,
        "raw_prompt": str,
        "formatted_datetime": str
    }
    oder None, falls keine relevante Planungsabsicht erkannt wurde.
    """
    if not text or not text.strip():
        return None

    ref = reference_time or datetime.now()
    clean = text.strip()
    lower = clean.lower()

    # Indikatoren für Termine / Erinnerungen / Vorhaben
    triggers = [
        "erinnere mich", "erinnerung", "habe vor", "habe morgen vor", "habe heute vor",
        "plane", "planen", "lebensplan", "termin", "erinnerst du mich",
        "trag ein", "merke dir", "notiere", "setze eine erinnerung"
    ]

    has_trigger = any(t in lower for t in triggers)
    has_relative_in = bool(re.search(r'\bin\s+\d+\s*(?:sekunden?|sek|minuten?|min|stunden?|std|h)\b', lower))
    has_morgen_um = bool(re.search(r'\b(?:morgen|heute|übermorgen)\s+um\s+\d{1,2}', lower))
    has_date_time = bool(re.search(r'\b(?:\d{1,2}\.\d{1,2}\.|(?:am\s+)?\d{1,2}\.\d{1,2}\.)', lower) and re.search(r'\bum\s+\d{1,2}', lower))
    has_weekday_time = bool(any(wd in lower for wd in GERMAN_WEEKDAYS) and re.search(r'\bum\s+\d{1,2}', lower))

    if not (has_trigger or has_relative_in or has_morgen_um or has_date_time or has_weekday_time):
        return None

    # Zeitpunkt ermitteln
    parsed_dt = parse_german_datetime(clean, reference_time=ref)
    if not parsed_dt:
        return None

    # Vorhaben / Aufgabenbeschreibung extrahieren
    task = clean

    # Spezifische Satzkonstruktionen auflösen:
    # "Ich habe morgen vor X, erinnere mich um 15 Uhr"
    pattern_vorhaben_erinnere = re.search(
        r'(?:ich\s+habe\s+(?:morgen|heute|übermorgen)?\s*vor\s+)(.+?)(?:,|\.|\s+und)?\s*(?:erinnere\s+mich\b.*)',
        task, re.IGNORECASE
    )
    if pattern_vorhaben_erinnere:
        task = pattern_vorhaben_erinnere.group(1).strip()
    else:
        # Bereinigung gängiger Trigger-Präfixe
        cleanup_prefixes = [
            r'^(?:bitte\s+)?erinnere\s+mich\s+(?:daran,?\s+dass\s+|an\s+|darüber\s+)?',
            r'^ich\s+habe\s+(?:morgen|heute|übermorgen)?\s*vor,?\s*',
            r'^ich\s+plane,?\s*',
            r'^(?:bitte\s+)?trag\s+(?:mir\s+)?ein:?\s*',
            r'^(?:bitte\s+)?merke\s+dir:?\s*',
            r'^(?:bitte\s+)?notiere:?\s*'
        ]
        for cp in cleanup_prefixes:
            task = re.sub(cp, '', task, flags=re.IGNORECASE).strip()

        # Zeitangaben am Anfang oder Ende entfernen
        task = re.sub(r'\bin\s+\d+\s*(?:sekunden?|sek|minuten?|min|stunden?|std|h)\b', '', task, flags=re.IGNORECASE)
        task = re.sub(r'\b(?:morgen|heute|übermorgen)\s+um\s+\d{1,2}(?::\d{2})?\s*(?:uhr)?\b', '', task, flags=re.IGNORECASE)
        task = re.sub(r'\bum\s+\d{1,2}(?::\d{2})?\s*(?:uhr)?\b', '', task, flags=re.IGNORECASE)
        task = re.sub(r'\b(?:am\s+)?\d{1,2}\.\d{1,2}\.(?:\s*\d{4}\b)?', '', task, flags=re.IGNORECASE)
        task = re.sub(r'\b(?:am\s+)?(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b', '', task, flags=re.IGNORECASE)
        task = re.sub(r'\b(?:erinnere\s+mich|erinnerung)\b', '', task, flags=re.IGNORECASE)

    # Füllwörter und Satzzeichen am Rand entfernen
    task = re.sub(r'^(?:an|an\s+das|an\s+den|an\s+die|dass|zu)\s+', '', task, flags=re.IGNORECASE)
    task = task.strip(" ,.:;!?-\n\t")
    if not task:
        task = "Geplante Aufgabe / Vorhaben"

    # Deutsche Formatierung für die Quittierung
    formatted_date = parsed_dt.strftime("%d.%m.%Y um %H:%M Uhr")
    if parsed_dt.date() == ref.date():
        formatted_date = f"heute um {parsed_dt.strftime('%H:%M')} Uhr"
    elif parsed_dt.date() == (ref + timedelta(days=1)).date():
        formatted_date = f"morgen um {parsed_dt.strftime('%H:%M')} Uhr"
    elif parsed_dt.date() == (ref + timedelta(days=2)).date():
        formatted_date = f"übermorgen um {parsed_dt.strftime('%H:%M')} Uhr"

    return {
        "due_time": parsed_dt,
        "due_iso": parsed_dt.isoformat(),
        "task_description": task,
        "raw_prompt": clean,
        "formatted_datetime": formatted_date
    }


# ==============================================================================
# Dispatch Pipeline
# ==============================================================================

def dispatch_reminder(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Führt die Benachrichtigung bei Fälligkeit eines Termins aus:
    1. Push-Pipeline: Telegram & Desktop-Notification
    2. Smart Speaker: Falls im Kanal aktiviert und Feature-Toggle aktiv
    """
    task = item.get("task_description", "Erinnerung")
    rem_id = item.get("id", "unknown")
    channels = item.get("channels", {})
    push_enabled = channels.get("push", True)
    speaker_broadcast = channels.get("speaker", False)

    logger.info("🚨 DISPATCHING REMINDER [%s]: '%s' (Push=%s, Speaker=%s)",
                rem_id, task, push_enabled, speaker_broadcast)

    results = {
        "id": rem_id,
        "task": task,
        "telegram": False,
        "desktop": False,
        "speaker": None
    }

    # 1. Push-Pipeline
    if push_enabled:
        tg_text = (
            f"⏰ *Hermes Lebensplan & Reminder*\n"
            f"Big Boss, dein geplantes Vorhaben steht an:\n\n"
            f"📌 *{task}*\n\n"
            f"_(Pünktlich gemeldet via Outer Heaven Cluster)_"
        )
        results["telegram"] = send_telegram_notification(tg_text)
        results["desktop"] = send_desktop_notification("⏰ Hermes Lebensplan", f"Fällige Aufgabe: {task}")

    # 2. Smart Speaker
    if speaker_broadcast:
        speaker_text = f"Achtung Big Boss, Erinnerung für dein Vorhaben: {task}"
        results["speaker"] = dispatch_to_smart_speaker(speaker_text)

    return results


# ==============================================================================
# Background Scheduler
# ==============================================================================

class ReminderScheduler:
    """Autonomer Background-Scheduler zur Überwachung des Lebensplans."""

    def __init__(self, check_interval_seconds: float = 5.0, filepath: Optional[Path] = None):
        self.interval = check_interval_seconds
        self.filepath = filepath or LEBENSPLAN_FILE
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Startet den Background-Thread, falls noch nicht aktiv."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="HermesReminderScheduler", daemon=True)
        self._thread.start()
        logger.info("Hermes Reminder-Scheduler gestartet (Prüfintervall: %.1fs).", self.interval)

    def stop(self):
        """Stoppt den Background-Thread sauber."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("Hermes Reminder-Scheduler gestoppt.")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def check_and_dispatch(self) -> int:
        """Prüft alle Einträge und löst fällige Termine aus. Gibt die Anzahl ausgelöster Termine zurück."""
        items = load_lebensplan(self.filepath)
        now_ts = time.time()
        due_items = [i for i in items if i.get("status") == "pending" and i.get("due_timestamp", 0) <= now_ts]

        if not due_items:
            return 0

        dispatched_ids = set()
        for item in due_items:
            rem_id = item.get("id")
            try:
                dispatch_reminder(item)
                dispatched_ids.add(rem_id)
            except Exception as e:
                logger.error("Fehler beim Auslösen von Reminder %s: %s", rem_id, e)

        if dispatched_ids:
            with _lebensplan_lock:
                fresh_items = []
                if self.filepath.is_file():
                    try:
                        with open(self.filepath, "r", encoding="utf-8") as f:
                            fresh_items = json.load(f)
                    except Exception:
                        fresh_items = items
                now_iso = datetime.now().isoformat()
                for fi in fresh_items:
                    if fi.get("id") in dispatched_ids:
                        fi["status"] = "completed"
                        fi["dispatched_at"] = now_iso
                try:
                    temp_file = self.filepath.with_suffix(".tmp")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(fresh_items, f, indent=2, ensure_ascii=False)
                    temp_file.replace(self.filepath)
                except Exception as e:
                    logger.error("Fehler beim Aktualisieren von %s: %s", self.filepath, e)

        return len(dispatched_ids)

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.check_and_dispatch()
            except Exception as e:
                logger.error("Fehler im Scheduler-Loop: %s", e)
            self._stop_event.wait(self.interval)


# Globaler Scheduler-Singleton
_scheduler_instance: Optional[ReminderScheduler] = None

def get_scheduler(filepath: Optional[Path] = None) -> ReminderScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = ReminderScheduler(filepath=filepath)
    return _scheduler_instance

def start_scheduler(filepath: Optional[Path] = None) -> ReminderScheduler:
    sched = get_scheduler(filepath)
    sched.start()
    return sched

def stop_scheduler():
    global _scheduler_instance
    if _scheduler_instance:
        _scheduler_instance.stop()


# Automatisch Scheduler im Hintergrund starten beim Modul-Import (falls gewünscht)
if os.getenv("HERMES_AUTORUN_SCHEDULER", "true").lower() in ("true", "1", "yes"):
    try:
        start_scheduler()
    except Exception as _e_init:
        logger.debug("Automatischer Scheduler-Start zurückgestellt: %s", _e_init)
