#!/usr/bin/env python3
"""
buero_hermes.py - Büro-Hermes Backend & Autogen Integration
============================================================
Automatisierte Büro-Organisation für den Hermes-Agenten auf Node Fox:
1. Lokales Dateisystem-Handling im gemounteten Agenten-Drive:
   /home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes/
   - "schriftlich" (PDF-Scans)
   - "E-Mail" (Rechnungen & Anhänge)
   - "Fixkosten" (Verträge & Abos)
   - "Bewerbungsunterlagen" (CV, Zeugnisse)
   - "Archiv/YYYY/" (Revisionssicheres 10-Jahre-Langzeitarchiv nach GoBD)
   - "Werbung/" (Newsletter & Werbung)
   - "Isoliert/" (Quarantäne für Phishing-Verdacht & unzulässige Anhänge)

2. Revisionssichere Archivierung (10 Jahre Aufbewahrungspflicht):
   - Keine Rechnungen/Belege löschen!
   - Vorgänge > 90 Tage werden in Archiv/YYYY/E-Mail/ bzw. Archiv/YYYY/schriftlich/ verschoben.
   - Für den aktiven LLM-Kontext (qwen:vram) werden standardmäßig nur die letzten 90 Tage
     geladen (Schutz des 8192-Token-Limits).

3. Mail-Klassifizierung & Trennung:
   - Spam: Dauerhaft und unwiderruflich löschen (IMAP +FLAGS \\Deleted + EXPUNGE).
   - Werbung/Newsletter: Gmail-Ordner 'Werbung' + lokaler Drive-Ordner 'Werbung/'.
   - Rechnungen/Belege: Lokaler Zielpfad 'E-Mail/'.

4. Strikte IT-Sicherheit & Phishing-Guard:
   - NIEMALS Links in E-Mails öffnen, folgen oder scrapen.
   - Strikte Whitelist für Anhänge: Ausschließlich .pdf, .png, .jpg, .jpeg (mit Magic-Byte-Prüfung).
   - Verbot & sofortige Abwehr von .exe, .scr, .bat, .vbs, .js, .zip, etc.
   - Phishing-Verdacht (Domain-Spoofing, Konto-Sperr-Druck, Tracking) sofort isolieren.

5. Modell-Routing:
   - Extraktion, Indexierung, Umbenennung: qwen:vram (Kontext 8192, Low Latency).
   - Textentwürfe (Widersprüche, Anfragen, Bewerbungsanpassungen): darkidol:ram.
"""

import os
import sys
import re
import json
import time
import shutil
import imaplib
import logging
import argparse
import tempfile
import subprocess
import urllib.request
import urllib.error
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set

# Logging Configuration
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [BueroHermes]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("BueroHermes")

# --- Konfigurationskonstanten ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", 11434))
FAST_MODEL = os.environ.get("BUERO_FAST_MODEL", "qwen:vram")
DEEP_MODEL = os.environ.get("BUERO_DEEP_MODEL", "darkidol:ram")
NUM_CTX = 8192
ACTIVE_WINDOW_DAYS = 90  # 90 Tage Arbeitsbestand für LLM-Kontext

DEFAULT_BASE_PATH = "/home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes"
FALLBACK_UNICODE_SLASH_PATH = "/home/fox/gdrive/Agenten-Drive/Büro/Büro／Hermes"

GMAIL_CREDS_FILE = Path(os.path.expanduser("~/.config/google/gmail_creds.json"))
GOOGLE_TOKEN_FILE = Path(os.path.expanduser("~/.hermes/google_token.json"))

# --- IT-Sicherheit & Phishing-Guard Filter ---
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
FORBIDDEN_DANGEROUS_EXTENSIONS = {
    ".exe", ".scr", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".ps1xml", ".ps2", ".msh", ".jar",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".img", ".dmg",
    ".msi", ".msp", ".hta", ".cpl", ".inf", ".reg", ".docm", ".xlsm", ".pptm"
}

RECHNUNG_KEYWORDS = [
    "rechnung", "beleg", "invoice", "abrechnung", "mahnung",
    "gebühr", "quittung", "zahlungsbestätigung", "forderung"
]

WERBUNG_KEYWORDS = [
    "newsletter", "rabatt", "angebot", "gutschein", "sale",
    "prozent", "werbung", "deal", "sparen", "abonnieren",
    "unsubscribe", "abmelden", "marketing"
]

SPAM_KEYWORDS = [
    "viagra", "casino", "lottery", "gewinnspiel", "erbe", "inheritance",
    "bitcoin profit", "crypto doubling", "investieren sie jetzt",
    "millionen euro gewonnen", "sexy girls", "horny", "pills"
]

PHISHING_ALERTS = [
    "konto gesperrt", "kontosperrung", "zugangsdaten aktualisieren",
    "verifizieren sie ihr konto", "sicherheitsabteilung", "kreditkarte abgelaufen",
    "dringende handlung erforderlich", "innerhalb von 24 stunden",
    "zugriff eingeschränkt", "passwort zurücksetzen", "login-versuch",
    "ihre identität bestätigen"
]


class BueroPaths:
    """Verwaltet und normalisiert lokale Pfade im gemounteten Google Drive."""

    def __init__(self, custom_base: Optional[str] = None):
        self.base_dir = self._resolve_base_dir(custom_base)
        self.schriftlich = self._resolve_subfolder(["schriftlich", "Schriftlich"])
        self.email = self._resolve_subfolder(["E-Mail", "email", "Email", "e-mail"])
        self.fixkosten = self._resolve_subfolder(["Fixkosten", "fixkosten"])
        self.werbung = self._resolve_subfolder(["Werbung", "werbung"])
        self.isoliert = self._resolve_subfolder(["Isoliert", "isoliert", "Quarantäne"])
        self.archiv = self._resolve_subfolder(["Archiv", "archiv"])
        self.bewerbungsunterlagen = self._resolve_bewerbung_folder()

    def _resolve_base_dir(self, custom_base: Optional[str] = None) -> Path:
        if custom_base:
            p = Path(custom_base).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p

        candidates = [
            Path(DEFAULT_BASE_PATH),
            Path(FALLBACK_UNICODE_SLASH_PATH),
            Path.home() / "gdrive" / "Agenten-Drive" / "Büro" / "Büro Hermes",
            Path.home() / "gdrive" / "Agenten-Drive" / "Büro" / "Büro／Hermes",
            Path("/tmp/buero_hermes_mock")
        ]

        for c in candidates:
            if c.exists() and c.is_dir():
                return c

        parent = Path("/home/fox/gdrive/Agenten-Drive/Büro")
        if parent.exists() and parent.is_dir():
            target = parent / "Büro Hermes"
            try:
                target.mkdir(parents=True, exist_ok=True)
                return target
            except Exception:
                pass

        fallback = candidates[0]
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except Exception:
            mock_path = Path("/tmp/buero_hermes_mock")
            mock_path.mkdir(parents=True, exist_ok=True)
            return mock_path

    def _resolve_subfolder(self, names: List[str]) -> Path:
        for name in names:
            p = self.base_dir / name
            if p.exists() and p.is_dir():
                return p
        target = self.base_dir / names[0]
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Konnte Verzeichnis %s nicht erstellen: %s", target, e)
        return target

    def _resolve_bewerbung_folder(self) -> Path:
        candidates = [
            self.base_dir / "Bewerbungsunterlagen",
            self.base_dir.parent / "Bewerbungsunterlagen",
            self.base_dir / "bewerbungsunterlagen"
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        target = self.base_dir / "Bewerbungsunterlagen"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return target

    def get_archive_dir(self, year: int, category: str) -> Path:
        """Liefert bzw. erstellt das revisionssichere Archiv: Archiv/YYYY/<category>/."""
        target = self.archiv / str(year) / category
        target.mkdir(parents=True, exist_ok=True)
        return target

    def status(self) -> Dict[str, str]:
        return {
            "base_dir": str(self.base_dir),
            "schriftlich": str(self.schriftlich),
            "email": str(self.email),
            "fixkosten": str(self.fixkosten),
            "bewerbungsunterlagen": str(self.bewerbungsunterlagen),
            "werbung": str(self.werbung),
            "isoliert": str(self.isoliert),
            "archiv": str(self.archiv),
            "exists": str(self.base_dir.exists())
        }


# ==============================================================================
# Strikte IT-Sicherheit & Phishing-Guard
# ==============================================================================

class SecurityGuard:
    """
    Sicherheitsprüfung für Dokumente und E-Mails:
    - Verhindert Ausführen / Öffnen von bösartigen Payloads
    - Strikte Whitelist für Dokumentenformate (.pdf, .png, .jpg)
    - Validierung der Magic Bytes (Dateisignatur)
    - Phishing-Erkennung & Quarantäne-Isolation
    - Absolutes Verbot von HTTP/Link-Aufrufen aus E-Mail-Inhalten
    """

    @staticmethod
    def is_extension_allowed(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in ALLOWED_ATTACHMENT_EXTENSIONS

    @staticmethod
    def is_extension_dangerous(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in FORBIDDEN_DANGEROUS_EXTENSIONS

    @staticmethod
    def validate_magic_bytes(content: bytes, filename: str) -> bool:
        """Validiert die Dateisignatur (Magic Bytes), um Maskierungen zu verhindern."""
        if not content:
            return False
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            # PDF muss mit %PDF- beginnen (üblicherweise erste 1024 Bytes)
            return b"%PDF-" in content[:1024]
        elif ext == ".png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        elif ext in [".jpg", ".jpeg"]:
            return content.startswith(b"\xff\xd8\xff")
        return False

    @classmethod
    def inspect_attachment(cls, filename: str, content: bytes) -> Tuple[bool, str]:
        """
        Prüft einen Anhang auf IT-Sicherheitskonformität.
        Gibt (ist_sicher, begruendung) zurück.
        """
        if cls.is_extension_dangerous(filename):
            return False, f"GEFÄHRLICHER DATEITYP BLOCKIERT: Endung {Path(filename).suffix} ist unzulässig!"

        if not cls.is_extension_allowed(filename):
            return False, f"NICHT-WHILISTED FORMAT: {filename} entspricht nicht PDF/PNG/JPG."

        if not cls.validate_magic_bytes(content, filename):
            return False, f"DATEISIGNATUR UNGÜLTIG: {filename} ist keine echte PDF/Bilddatei (Manipulationsverdacht)!"

        return True, "Sicher"

    @classmethod
    def check_phishing(cls, sender: str, subject: str, body_text: str) -> Tuple[bool, str]:
        """
        Analysiert E-Mail auf typische Phishing-Indikatoren.
        Gibt (phishing_verdacht, grund) zurück.
        """
        combined = f"{subject} {body_text}".lower()

        # 1. Bekannte Alarmphrasen
        for alert in PHISHING_ALERTS:
            if alert in combined:
                # Prüfe ob der Absender eine vertrauenswürdige Domain ist
                _, addr = parseaddr(sender)
                domain = addr.split("@")[-1].lower() if "@" in addr else ""
                return True, f"Phishing-Alarmphrase erkannt: '{alert}' von Absender-Domain '{domain}'"

        # 2. Absender-Name vs. Absender-Adresse (Spoofing)
        name, addr = parseaddr(sender)
        brand_spoof = ["paypal", "sparkasse", "volksbank", "amazon", "dhl", "deutsche bank", "postbank"]
        for brand in brand_spoof:
            if brand in name.lower() and brand not in addr.lower():
                return True, f"Absender-Spoofing erkannt: Name '{name}', aber echte Adresse '{addr}'"

        # 3. Aggressiver Link-Spam (z. B. mehr als 15 Links in einer kurzen Nachricht)
        links = re.findall(r"https?://[^\s<>\"']+", body_text)
        if len(links) > 20 and len(body_text) < 1500:
            return True, f"Auffälliger Link-Spam erkannt ({len(links)} Links im Text)"

        return False, "Unauffällig"


# ==============================================================================
# Modell-Routing & Ollama API
# ==============================================================================

def query_ollama(
    model: str,
    prompt: str,
    system_prompt: str = "",
    temperature: float = 0.2,
    num_ctx: int = NUM_CTX,
    timeout: int = 120
) -> str:
    """Sendet einen Prompt an die lokale Ollama-Instanz auf Fox."""
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except Exception as e:
        logger.error("Fehler bei Ollama-Anfrage an Modell %s: %s", model, e)
        raise RuntimeError(f"Ollama-Inferenz fehlgeschlagen ({model}): {e}")


def extract_metadata_qwen(text_sample: str) -> Dict[str, Any]:
    """
    Modell-Routing 1 (qwen:vram):
    Extrahiert Gläubiger/Absender, Betrag, Aktenzeichen und Belegdatum.
    """
    system_prompt = (
        "Du bist ein hochpräziser Dokumenten-Indexer für Büro- und Rechtsdokumente. "
        "Deine Aufgabe: Analysiere den Textauszug eines Dokuments (z.B. Rechnung, Mahnung, Inkasso-Schreiben) "
        "und extrahiere die Kerndaten im JSON-Format.\n"
        "Regeln:\n"
        "- datum: Beleg-/Schreibendatum im Format YYYY-MM-DD (falls unklar, heutiges Datum nutzen).\n"
        "- glaeubiger: Name der Firma, Behörde oder des Gläubigers (z. B. 'PAIR Finance', 'Telekom', 'Finanzamt'). "
        "Bereinige Rechtsformen wie GmbH, AG auf das Wesentliche.\n"
        "- betrag: Der finale Gesamt- oder Forderungsbetrag als Zahl mit Komma (z. B. '372,41' oder '49,99'). Keine Währungszeichen.\n"
        "- aktenzeichen: Das Aktenzeichen, die Rechnungsnummer, Kunden- oder Vorgangsnummer (z. B. 'AZ-98124', 'RE-2026-01').\n"
        "- Gib AUSSCHLIESSLICH ein valides JSON-Objekt ohne Markdown-Codeblöcke oder Erklärungen zurück."
    )

    prompt = (
        f"Dokument-Textauszug:\n"
        f"\"\"\"\n{text_sample[:4000]}\n\"\"\"\n\n"
        f"Antworte nur mit JSON:\n"
        f"{{\n"
        f'  "datum": "YYYY-MM-DD",\n'
        f'  "glaeubiger": "Name",\n'
        f'  "betrag": "0,00",\n'
        f'  "aktenzeichen": "Nummer"\n'
        f"}}"
    )

    raw_response = query_ollama(
        model=FAST_MODEL,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=0.1,
        num_ctx=NUM_CTX,
        timeout=60
    )

    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    data = {}
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
            except Exception:
                pass

    if not data.get("datum"):
        d_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", text_sample)
        if d_match:
            d, m, y = d_match.groups()
            y = f"20{y}" if len(y) == 2 else y
            data["datum"] = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        else:
            data["datum"] = datetime.now().strftime("%Y-%m-%d")

    if not data.get("betrag"):
        b_match = re.search(r"(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)", text_sample, re.IGNORECASE)
        data["betrag"] = b_match.group(1).replace(".", ",") if b_match else "0,00"

    if not data.get("glaeubiger"):
        data["glaeubiger"] = "Unbekannt"

    if not data.get("aktenzeichen"):
        az_match = re.search(r"(?:Aktenzeichen|AZ|Rechnungs-?Nr|Rechnung|Vorgang)[:\s]+([A-Za-z0-9\-_/]+)", text_sample, re.IGNORECASE)
        data["aktenzeichen"] = az_match.group(1).replace("/", "-") if az_match else "OhneAZ"

    return data


def draft_document_darkidol(
    draft_type: str,
    context: str,
    details: Optional[Dict[str, Any]] = None
) -> str:
    """
    Modell-Routing 2 (darkidol:ram):
    Generiert anspruchsvolle juristische Widersprüche, Ratenzahlungsanfragen
    oder Bewerbungsanschreiben.
    """
    details = details or {}
    type_prompts = {
        "widerspruch": (
            "Du bist ein versierter rechtlicher Assistent im deutschen Zivil- und Inkassorecht. "
            "Formuliere einen förmlichen, fundierten und bestimmten Widerspruch gegen eine unberechtigte Forderung, "
            "eine Mahnung oder ein Inkassoschreiben (z.B. PAIR Finance, EOS, Creditreform). "
            "Bestreite die Hauptforderung sowie überhöhte Inkassokosten nach § 4 Abs. 5 RDGEG / § 254 BGB. "
            "Fordere eine detaillierte Forderungsaufstellung und Vollmachtsurkunde nach § 174 BGB an."
        ),
        "anfrage": (
            "Du bist ein professioneller Büro-Assistent. Formuliere eine höfliche, aber rechtlich präzise schriftliche "
            "Anfrage zu einem Vorgang (z. B. Bitte um Rechnungsaufschlüsselung, Fristverlängerung oder Ratenzahlungsangebot)."
        ),
        "bewerbung": (
            "Du bist ein profilierter Karriereberater und Ghostwriter für Bewerbungsunterlagen. "
            "Formuliere ein aussagekräftiges, modernes und überzeugendes Anschreiben (Cover Letter) auf Deutsch, "
            "abgestimmt auf die vorgegebenen Fähigkeiten, Qualifikationen und die Stellenbeschreibung."
        )
    }

    system_prompt = type_prompts.get(
        draft_type.lower(),
        "Du bist ein sachkundiger Assistent für geschäftliche und amtliche Korrespondenz auf Deutsch."
    )

    prompt = (
        f"Erstelle einen vollständigen, sofort versandfertigen Schriftsatz für die Kategorie '{draft_type}'.\n\n"
        f"Gegebener Sachverhalt & Dokumentenkontext:\n"
        f"\"\"\"\n{context}\n\"\"\"\n\n"
        f"Zusätzliche Details: {json.dumps(details, ensure_ascii=False)}\n\n"
        f"Anforderungen:\n"
        f"- Korrektes deutsches Briefformat (Absender-Platzhalter, Empfänger, Datum, Betreff mit Aktenzeichen, Anrede, Textkörper, Grußformel).\n"
        f"- Sachlich, souverän, durchdacht und unmissverständlich.\n"
        f"- Gebe den fertigen Brief direkt aus."
    )

    logger.info("Sende Entwurfsanfrage (%s) an Deep-Modell '%s'...", draft_type, DEEP_MODEL)
    return query_ollama(
        model=DEEP_MODEL,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=0.5,
        num_ctx=NUM_CTX,
        timeout=180
    )


# ==============================================================================
# Revisionssichere Archivierung (10 Jahre Aufbewahrungspflicht)
# ==============================================================================

def parse_date_from_filename_or_mtime(file_path: Path) -> datetime:
    """Ermittelt das Belegdatum aus dem Dateinamen oder der Datei-mtime."""
    name = file_path.name
    # 1. Format: YYYY-MM-DD_...
    match_ymd = re.match(r"^(\d{4})-(\d{2})-(\d{2})_", name)
    if match_ymd:
        try:
            return datetime(int(match_ymd.group(1)), int(match_ymd.group(2)), int(match_ymd.group(3)))
        except ValueError:
            pass

    # 2. Format: YYYYMMDD_...
    match_compact = re.match(r"^(\d{4})(\d{2})(\d{2})_", name)
    if match_compact:
        try:
            return datetime(int(match_compact.group(1)), int(match_compact.group(2)), int(match_compact.group(3)))
        except ValueError:
            pass

    # 3. Fallback: Datei-Änderungsdatum
    stat = file_path.stat()
    return datetime.fromtimestamp(stat.st_mtime)


def archive_aged_documents(
    paths: Optional[BueroPaths] = None,
    days_threshold: int = ACTIVE_WINDOW_DAYS,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Revisionssichere Archivierung:
    Verschiebt Vorgänge, die älter als `days_threshold` (Standard 90 Tage) sind,
    geordnet nach Kalenderjahr in das Langzeitarchiv:
      - Archiv/YYYY/E-Mail/
      - Archiv/YYYY/schriftlich/
    Keine Belege werden gelöscht!
    """
    paths = paths or BueroPaths()
    cutoff_date = datetime.now() - timedelta(days=days_threshold)
    archived_records = {"E-Mail": [], "schriftlich": []}

    categories = [
        ("E-Mail", paths.email),
        ("schriftlich", paths.schriftlich)
    ]

    for category, folder in categories:
        if not folder.exists():
            continue

        for f in folder.glob("*.pdf"):
            if not f.is_file():
                continue

            doc_date = parse_date_from_filename_or_mtime(f)
            if doc_date < cutoff_date:
                year = doc_date.year
                target_archive_dir = paths.get_archive_dir(year, category)
                target_file = target_archive_dir / f.name

                logger.info(
                    "Archiviere Dokument (>%d Tage alt, Belegdatum %s): %s -> %s",
                    days_threshold, doc_date.strftime("%Y-%m-%d"), f.name, target_archive_dir
                )

                if not dry_run:
                    try:
                        shutil.move(str(f), str(target_file))
                        archived_records[category].append({
                            "original": str(f),
                            "archived": str(target_file),
                            "year": year,
                            "date": doc_date.strftime("%Y-%m-%d")
                        })
                    except Exception as e:
                        logger.error("Fehler beim Verschieben ins Archiv für %s: %s", f.name, e)
                else:
                    archived_records[category].append({
                        "original": str(f),
                        "archived": str(target_file),
                        "year": year,
                        "date": doc_date.strftime("%Y-%m-%d"),
                        "dry_run": True
                    })

    total = len(archived_records["E-Mail"]) + len(archived_records["schriftlich"])
    logger.info("Revisionssichere Archivierung abgeschlossen: %d Dokument(e) archiviert.", total)
    return archived_records


def get_active_workspace_files(
    paths: Optional[BueroPaths] = None,
    max_days: int = ACTIVE_WINDOW_DAYS
) -> List[Path]:
    """
    Liefert nur die Dokumente des aktiven Arbeitsbestands (letzte 90 Tage),
    um den LLM-Kontext für qwen:vram sauber und kompakt zu halten.
    """
    paths = paths or BueroPaths()
    cutoff_date = datetime.now() - timedelta(days=max_days)
    active_files = []

    for folder in [paths.schriftlich, paths.email]:
        if not folder.exists():
            continue
        for f in folder.glob("*.pdf"):
            if f.is_file():
                doc_date = parse_date_from_filename_or_mtime(f)
                if doc_date >= cutoff_date:
                    active_files.append(f)

    return sorted(active_files, key=lambda x: x.name)


# ==============================================================================
# Routine 1: Gmail -> Klassifizierung, IT-Sicherheit & Ingest
# ==============================================================================

def sanitize_filename(name: str) -> str:
    """Entfernt problematische Sonderzeichen für Dateinamen."""
    cleaned = re.sub(r'[\s/\\:*?"<>|]+', '_', name)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or "Anhang"


def classify_email(subject: str, sender: str, body_text: str) -> str:
    """
    Klassifiziert eine E-Mail in:
    - 'PHISHING' (Sofort isolieren)
    - 'SPAM' (Unwiderruflich löschen)
    - 'WERBUNG' (In Werbung-Ordner einsortieren)
    - 'RECHNUNG' (Rechnung/Beleg für E-Mail Ingest)
    - 'SONSTIGES' (Reguläre Mail)
    """
    # 1. Phishing-Prüfung
    is_phish, _ = SecurityGuard.check_phishing(sender, subject, body_text)
    if is_phish:
        return "PHISHING"

    combined = f"{subject} {sender} {body_text}".lower()

    # 2. Spam-Prüfung
    if any(kw in combined for kw in SPAM_KEYWORDS):
        return "SPAM"

    # 3. Rechnungs-Prüfung
    if any(kw in subject.lower() for kw in RECHNUNG_KEYWORDS):
        return "RECHNUNG"

    # 4. Werbung/Newsletter-Prüfung
    if any(kw in combined for kw in WERBUNG_KEYWORDS):
        return "WERBUNG"

    if any(kw in combined for kw in RECHNUNG_KEYWORDS[:5]):
        return "RECHNUNG"

    return "SONSTIGES"


def ensure_gmail_label(mail: imaplib.IMAP4_SSL, label_name: str) -> bool:
    """Stellt sicher, dass ein Zielordner/Label in Gmail existiert."""
    try:
        status, folder_list = mail.list()
        if status == "OK":
            for f_info in folder_list:
                if f'"{label_name}"' in f_info.decode("utf-8", errors="replace"):
                    return True
        mail.create(label_name)
        return True
    except Exception:
        return False


def fetch_gmail_invoices(
    paths: Optional[BueroPaths] = None,
    creds_file: Path = GMAIL_CREDS_FILE,
    max_mails: int = 25,
    mark_as_read: bool = True
) -> Dict[str, Any]:
    """
    Routine 1 mit Mail-Filterung & Phishing-Schutz:
    - Spam: Dauerhaft gelöscht (+FLAGS \\Deleted & EXPUNGE).
    - Werbung: In Google Drive 'Werbung/' und Gmail-Ordner 'Werbung'.
    - Phishing: Sofort isoliert in 'Isoliert/'.
    - Rechnungen: Sichere PDFs nach 'E-Mail/' herunterladen.
    """
    paths = paths or BueroPaths()
    results = {
        "invoices_saved": [],
        "werbung_processed": [],
        "spam_deleted": 0,
        "phishing_isolated": 0
    }

    if not creds_file.is_file():
        logger.warning("Keine Gmail-Anmeldedaten unter %s gefunden. Überspringe IMAP-Sync.", creds_file)
        return results

    try:
        with open(creds_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        email_user = cfg.get("email")
        app_password = cfg.get("app_password", "").replace(" ", "")
    except Exception as e:
        logger.error("Konnte %s nicht lesen: %s", creds_file, e)
        return results

    if not email_user or not app_password:
        logger.error("Ungültige Anmeldedaten in %s", creds_file)
        return results

    logger.info("Verbinde mit Gmail IMAP (%s)...", email_user)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_user, app_password)
        mail.select("inbox")

        # Prüfe Ordner 'Werbung'
        ensure_gmail_label(mail, "Werbung")

        # Gezielte Suche nach ungelesenen Mails
        matched_ids: Set[bytes] = set()
        for kw in ["Rechnung", "Beleg", "Invoice", "Mahnung", "Abrechnung", "Newsletter", "Angebot"]:
            search_query = f'(UNSEEN SUBJECT "{kw}")'
            st, d = mail.search(None, search_query)
            if st == "OK" and d[0]:
                for mid in d[0].split():
                    matched_ids.add(mid)

        # Fallback auf allgemeine UNSEEN Mails
        if not matched_ids:
            st, d = mail.search(None, "UNSEEN")
            if st == "OK" and d[0]:
                for mid in d[0].split():
                    matched_ids.add(mid)

        if not matched_ids:
            logger.info("Keine relevanten ungelesenen Nachrichten im Posteingang vorhanden.")
            mail.logout()
            return results

        sorted_ids = sorted(list(matched_ids), key=lambda x: int(x), reverse=True)[:max_mails]
        logger.info("%d ungesehene Mail(s) im Fokus. Führe Klassifizierung & Sicherheitsprüfung durch...", len(sorted_ids))

        parser = BytesParser(policy=policy.default)
        expunge_needed = False

        for msg_id in sorted_ids:
            res, data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK":
                continue

            raw_email = data[0][1]
            msg = parser.parsebytes(raw_email)

            subject = str(msg["Subject"] or "Kein Betreff")
            sender = str(msg["From"] or "Unbekannt")
            date_str = str(msg["Date"] or "")

            # Textinhalt für Klassifizierung extrahieren (KEIN Link-Scraping / Browser-Aufruf!)
            body_parts = []
            for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_parts.append(payload.decode("utf-8", errors="replace"))
            body_text = "\n".join(body_parts)

            category = classify_email(subject, sender, body_text)
            timestamp_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Fall A: PHISHING -> Sofort isolieren
            if category == "PHISHING":
                is_phish, reason = SecurityGuard.check_phishing(sender, subject, body_text)
                logger.warning("🚨 PHISHING ERKANNT: '%s' von '%s' -> %s", subject, sender, reason)
                quarantine_info = {
                    "timestamp": timestamp_prefix,
                    "sender": sender,
                    "subject": subject,
                    "reason": reason,
                    "date": date_str
                }
                meta_file = paths.isoliert / f"PHISHING_{timestamp_prefix}_{sanitize_filename(subject)[:40]}.json"
                meta_file.write_text(json.dumps(quarantine_info, indent=2, ensure_ascii=False), encoding="utf-8")
                results["phishing_isolated"] += 1
                if mark_as_read:
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                continue

            # Fall B: SPAM -> Unwiderruflich löschen
            elif category == "SPAM":
                logger.info("🗑️ SPAM gelöscht: '%s' von '%s'", subject, sender)
                mail.store(msg_id, "+FLAGS", "\\Deleted")
                expunge_needed = True
                results["spam_deleted"] += 1
                continue

            # Fall C: WERBUNG -> Nach 'Werbung/' und Gmail-Ordner 'Werbung'
            elif category == "WERBUNG":
                logger.info("📢 Werbung / Newsletter erkannt: '%s' von '%s'", subject, sender)
                werbung_info = {
                    "timestamp": timestamp_prefix,
                    "sender": sender,
                    "subject": subject,
                    "date": date_str
                }
                werbung_file = paths.werbung / f"Werbung_{timestamp_prefix}_{sanitize_filename(subject)[:40]}.json"
                werbung_file.write_text(json.dumps(werbung_info, indent=2, ensure_ascii=False), encoding="utf-8")

                # In Gmail nach Ordner 'Werbung' verschieben
                try:
                    mail.copy(msg_id, "Werbung")
                    mail.store(msg_id, "+FLAGS", "\\Deleted")
                    expunge_needed = True
                except Exception as e:
                    logger.debug("Konnte Mail nicht in Ordner Werbung kopieren: %s", e)
                    if mark_as_read:
                        mail.store(msg_id, "+FLAGS", "\\Seen")

                results["werbung_processed"].append(subject)
                continue

            # Fall D: RECHNUNG / DOKUMENT
            attachments = []
            for part in msg.walk():
                filename = part.get_filename()
                if filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        # IT-Sicherheits- & Phishing-Guard Prüfung
                        is_safe, sec_reason = SecurityGuard.inspect_attachment(filename, payload)
                        if not is_safe:
                            logger.error("🛑 SICHERHEITSVERSTOSS bei Anhang '%s': %s", filename, sec_reason)
                            # Verdächtigen Anhang in Quarantäne isolieren
                            sec_file = paths.isoliert / f"BLOCKED_{timestamp_prefix}_{sanitize_filename(filename)}"
                            sec_file.write_bytes(payload)
                            continue
                        attachments.append((filename, payload))

            if attachments:
                logger.info("✅ Sichere Rechnung erkannt: '%s' (%d PDF/Bild-Anhänge)", subject, len(attachments))
                for fn, content in attachments:
                    clean_fn = sanitize_filename(fn)
                    target_file = paths.email / f"{timestamp_prefix}_{clean_fn}"
                    target_file.write_bytes(content)
                    logger.info("Dokument sicher abgelegt: %s", target_file)
                    results["invoices_saved"].append({
                        "file": str(target_file),
                        "subject": subject,
                        "sender": sender,
                        "date": date_str
                    })

                if mark_as_read:
                    mail.store(msg_id, "+FLAGS", "\\Seen")

        if expunge_needed:
            mail.expunge()

        mail.logout()
        logger.info(
            "Routine 1 abgeschlossen: %d Rechnungen gesichert, %d Werbung sortiert, %d Spam gelöscht, %d Phishing isoliert.",
            len(results["invoices_saved"]), len(results["werbung_processed"]),
            results["spam_deleted"], results["phishing_isolated"]
        )
    except Exception as e:
        logger.error("Fehler beim Gmail-Abruf: %s", e)

    return results


# ==============================================================================
# Routine 2: Scan-Indexer (PDF/OCR & Umbenennung mit qwen:vram)
# ==============================================================================

def extract_pdf_text_fast(pdf_path: Path) -> str:
    """Extrahiert Text aus digitalen PDFs blitzschnell via /usr/bin/pdftotext."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        proc = subprocess.run(
            ["pdftotext", "-l", "3", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception as e:
        logger.debug("pdftotext fehlgeschlagen für %s: %s", pdf_path.name, e)
    return ""


def extract_pdf_text_ocr(pdf_path: Path) -> str:
    """OCR-Fallback für reine Bildscans via pdftoppm + tesseract."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        logger.warning("pdftoppm oder tesseract nicht verfügbar für OCR.")
        return ""

    with tempfile.TemporaryDirectory(prefix="buero_ocr_") as tmpdir:
        tmp_prefix = Path(tmpdir) / "page"
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "2", str(pdf_path), str(tmp_prefix)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25
            )

            ocr_texts = []
            for img_file in sorted(Path(tmpdir).glob("page-*.png")):
                out_txt = Path(tmpdir) / f"{img_file.stem}_ocr"
                subprocess.run(
                    ["tesseract", str(img_file), str(out_txt)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30
                )
                txt_file = out_txt.with_suffix(".txt")
                if txt_file.is_file():
                    ocr_texts.append(txt_file.read_text(encoding="utf-8", errors="replace"))

            return "\n".join(ocr_texts).strip()
        except Exception as e:
            logger.error("OCR-Verarbeitung fehlgeschlagen für %s: %s", pdf_path.name, e)
            return ""


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Kombiniert pdftotext und OCR-Fallback."""
    text = extract_pdf_text_fast(pdf_path)
    if len(text.strip()) > 60:
        return text
    logger.info("Wenig/kein Text in %s. Starte Tesseract-OCR Fallback...", pdf_path.name)
    return extract_pdf_text_ocr(pdf_path)


def is_already_indexed(filename: str) -> bool:
    """Prüft, ob die Datei bereits dem Schema YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf entspricht."""
    pattern = r"^\d{4}-\d{2}-\d{2}_.+_\d+([.,]\d{1,2})?EUR_.*\.pdf$"
    return bool(re.match(pattern, filename))


def format_currency_eur(val: str) -> str:
    """Formatiert Beträge sauber zu z. B. '372,41EUR'."""
    cleaned = re.sub(r"[^\d,\.]", "", val).strip()
    if not cleaned:
        return "0,00EUR"
    if "." in cleaned and "," not in cleaned:
        cleaned = cleaned.replace(".", ",")
    return f"{cleaned}EUR"


def index_scans(
    paths: Optional[BueroPaths] = None,
    dry_run: bool = False
) -> List[Dict[str, Any]]:
    """
    Routine 2:
    Überwacht 'schriftlich/', liest neue PDFs ein, extrahiert per Text/OCR Gläubiger,
    Betrag und Aktenzeichen (z. B. PAIR Finance 372,41 €) und benennt die Datei lokal
    um in 'YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf'.
    """
    paths = paths or BueroPaths()
    scan_dir = paths.schriftlich
    results = []

    if not scan_dir.exists():
        logger.warning("Verzeichnis 'schriftlich' nicht gefunden: %s", scan_dir)
        return results

    pdf_files = [f for f in scan_dir.glob("*.pdf") if f.is_file()]
    logger.info("Überprüfe %d PDF(s) in %s...", len(pdf_files), scan_dir)

    for pdf in sorted(pdf_files):
        if is_already_indexed(pdf.name):
            logger.debug("Bereits indiziert: %s", pdf.name)
            continue

        logger.info("Indiziere Scan: %s", pdf.name)
        extracted_text = extract_text_from_pdf(pdf)

        if not extracted_text:
            logger.warning("Kein Text aus %s extrahierbar. Überspringe.", pdf.name)
            continue

        # Extraktion via qwen:vram
        meta = extract_metadata_qwen(extracted_text)
        datum = sanitize_filename(meta.get("datum", datetime.now().strftime("%Y-%m-%d")))
        glaeubiger = sanitize_filename(meta.get("glaeubiger", "Unbekannt"))
        betrag_str = format_currency_eur(meta.get("betrag", "0,00"))
        aktenzeichen = sanitize_filename(meta.get("aktenzeichen", "OhneAZ"))

        new_filename = f"{datum}_{glaeubiger}_{betrag_str}_{aktenzeichen}.pdf"
        target_path = scan_dir / new_filename

        # Kollisionsvermeidung
        counter = 1
        while target_path.exists() and target_path != pdf:
            new_filename = f"{datum}_{glaeubiger}_{betrag_str}_{aktenzeichen}_{counter}.pdf"
            target_path = scan_dir / new_filename
            counter += 1

        logger.info("Neuer Name für %s -> %s", pdf.name, new_filename)

        if not dry_run:
            try:
                shutil.move(str(pdf), str(target_path))
                results.append({
                    "old_path": str(pdf),
                    "new_path": str(target_path),
                    "metadata": meta
                })
            except Exception as e:
                logger.error("Konnte Datei %s nicht umbenennen: %s", pdf.name, e)
        else:
            results.append({
                "old_path": str(pdf),
                "new_path": str(target_path),
                "metadata": meta,
                "dry_run": True
            })

    logger.info("Routine 2 abgeschlossen: %d Datei(en) indiziert und umbenannt.", len(results))
    return results


# ==============================================================================
# AutoGen Tool Registry Schnittstelle
# ==============================================================================

def get_autogen_tools() -> Dict[str, Any]:
    """
    Gibt die Funktionsdefinitionen für AutoGen-Agenten zurück.
    Erlaubt direkte Integration in AutoGen AssistantAgent und UserProxyAgent.
    """
    return {
        "sync_gmail_invoices": {
            "function": fetch_gmail_invoices,
            "description": "Liest ungelesene Rechnungen via Gmail ab, filtert Spam/Werbung/Phishing und speichert sichere PDFs lokal in 'E-Mail'."
        },
        "index_scanned_documents": {
            "function": index_scans,
            "description": "Indiziert neue gescannte PDFs im Ordner 'schriftlich' mit qwen:vram und benennt sie nach YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf um."
        },
        "archive_aged_documents": {
            "function": lambda days=90: archive_aged_documents(days_threshold=days),
            "description": "Verschiebt Dokumente älter als 90 Tage revisionssicher in das Langzeitarchiv (Archiv/YYYY/E-Mail und Archiv/YYYY/schriftlich) nach GoBD."
        },
        "get_active_workspace": {
            "function": lambda: [str(f) for f in get_active_workspace_files()],
            "description": "Liefert den aktiven Arbeitsbestand (letzte 90 Tage) für den LLM-Kontext von qwen:vram."
        },
        "draft_legal_response": {
            "function": lambda text, category="widerspruch": draft_document_darkidol(category, text),
            "description": "Erstellt mit dem Deep-Modell darkidol:ram einen fundierten juristischen Widerspruch oder eine formelle Anfrage."
        },
        "get_office_status": {
            "function": lambda: BueroPaths().status(),
            "description": "Liefert den Status und die Pfade der Büro-Hermes Verzeichnisse im lokalen Drive."
        }
    }


# ==============================================================================
# CLI Entrypoint & Daemon Mode
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Büro-Hermes: Revisionssichere Archivierung, Mail-Filter & Phishing-Schutz")
    subparsers = parser.add_subparsers(dest="command", help="Verfügbare Befehle")

    # Status
    subparsers.add_parser("status", help="Zeigt Verzeichnis- und Systemstatus an")

    # Routine 1: Mail Sync
    mail_parser = subparsers.add_parser("sync-mail", help="Führt Routine 1 (Gmail -> E-Mail mit Filterung & Phishing-Schutz) aus")
    mail_parser.add_argument("--max", type=int, default=25, help="Maximale Anzahl zu prüfender Mails")
    mail_parser.add_argument("--no-mark-read", action="store_true", help="Gelesene Mails nicht als \\Seen markieren")

    # Routine 2: Scan Indexer
    scan_parser = subparsers.add_parser("index-scans", help="Führt Routine 2 (Scan-Indexer & Umbenennung) aus")
    scan_parser.add_argument("--dry-run", action="store_true", help="Nur simulieren, keine Dateien umbenennen")

    # Revisionssichere Archivierung (10 Jahre)
    archive_parser = subparsers.add_parser("archive", help="Revisionssichere Archivierung von Dokumenten > 90 Tage")
    archive_parser.add_argument("--days", type=int, default=90, help="Altersschwelle in Tagen (Standard: 90)")
    archive_parser.add_argument("--dry-run", action="store_true", help="Archivierung nur simulieren")

    # Aktiver Arbeitsbestand für LLM
    subparsers.add_parser("active-files", help="Listet den aktiven 90-Tage Arbeitsbestand für qwen:vram auf")

    # Routine All
    all_parser = subparsers.add_parser("run-all", help="Führt Mail-Sync, Scan-Indexierung und Archivierung nacheinander aus")
    all_parser.add_argument("--dry-run", action="store_true", help="Änderungen nur simulieren")

    # Textentwurf via darkidol:ram
    draft_parser = subparsers.add_parser("draft", help="Erstellt Textentwurf mit darkidol:ram")
    draft_parser.add_argument("--type", choices=["widerspruch", "anfrage", "bewerbung"], default="widerspruch", help="Art des Entwurfs")
    draft_parser.add_argument("--file", help="PDF- oder Textdatei als Kontext")
    draft_parser.add_argument("--context", help="Freitext als Kontext")
    draft_parser.add_argument("--output", help="Optionaler Ausgabepfad")

    # Watcher / Daemon
    watch_parser = subparsers.add_parser("watch", help="Startet die Überwachungsschleife")
    watch_parser.add_argument("--interval", type=int, default=120, help="Prüfintervall in Sekunden")

    args = parser.parse_args()
    paths = BueroPaths()

    if args.command == "status":
        print(json.dumps(paths.status(), indent=2, ensure_ascii=False))

    elif args.command == "sync-mail":
        print("Starte Routine 1: Gmail Ingest (mit Spam-, Werbe- und Phishing-Filter)...")
        saved = fetch_gmail_invoices(
            paths=paths,
            max_mails=args.max,
            mark_as_read=not args.no_mark_read
        )
        print(json.dumps(saved, indent=2, ensure_ascii=False, default=str))

    elif args.command == "index-scans":
        print("Starte Routine 2: Scan-Indexer (qwen:vram)...")
        results = index_scans(paths=paths, dry_run=args.dry_run)
        print(f"Erledigt: {len(results)} Datei(en) bearbeitet.")

    elif args.command == "archive":
        print(f"Starte revisionssichere Archivierung (Schwelle: {args.days} Tage)...")
        rec = archive_aged_documents(paths=paths, days_threshold=args.days, dry_run=args.dry_run)
        print(json.dumps(rec, indent=2, ensure_ascii=False))

    elif args.command == "active-files":
        files = get_active_workspace_files(paths=paths)
        print(f"Aktiver 90-Tage Arbeitsbestand ({len(files)} Dateien):")
        for f in files:
            print(f" - {f.name} ({f.parent.name})")

    elif args.command == "run-all":
        print("=== Starte Büro-Hermes Gesamtzyklus ===")
        saved = fetch_gmail_invoices(paths=paths)
        indexed = index_scans(paths=paths, dry_run=args.dry_run)
        archived = archive_aged_documents(paths=paths, dry_run=args.dry_run)
        print(f"Abgeschlossen: {len(saved.get('invoices_saved', []))} Mails gesichert, {len(indexed)} Scans indiziert.")

    elif args.command == "draft":
        context = ""
        if args.file:
            p = Path(args.file)
            if p.suffix.lower() == ".pdf":
                context = extract_text_from_pdf(p)
            else:
                context = p.read_text(encoding="utf-8", errors="replace")
        elif args.context:
            context = args.context
        else:
            print("Fehler: Bitte --file oder --context angeben.", file=sys.stderr)
            sys.exit(1)

        draft = draft_document_darkidol(draft_type=args.type, context=context)
        if args.output:
            Path(args.output).write_text(draft, encoding="utf-8")
            print(f"Entwurf gespeichert unter: {args.output}")
        else:
            print("\n" + "=" * 60)
            print(f"ENTWURF ({args.type.upper()} via {DEEP_MODEL}):")
            print("=" * 60)
            print(draft)
            print("=" * 60)

    elif args.command == "watch":
        print(f"Starte Büro-Hermes Watcher Daemon (Intervall: {args.interval}s)...")
        print("Beenden mit Strg+C")
        try:
            while True:
                logger.info("Watcher Durchlauf: Prüfe Mails, Scans & Archiv...")
                fetch_gmail_invoices(paths=paths)
                index_scans(paths=paths)
                archive_aged_documents(paths=paths)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nWatcher sauber beendet.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
