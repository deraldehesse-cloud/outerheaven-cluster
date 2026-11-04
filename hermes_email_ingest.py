#!/usr/bin/env python3
r"""
hermes_email_ingest.py - Hermes Autonomous Email Ingest & Paperless Dispatcher
==============================================================================
Überwacht Postfächer (z. B. GMX/Gmail) via IMAP.
Eingehende E-Mails und Anhänge werden vom lokalen LLM analysiert und kategorisiert:
  - SPAM: Automatisch löschen (\Deleted Flag).
  - PAPERLESS: Belege, Rechnungen, Behördenpost -> via API an Paperless-ngx (192.168.188.96:8000).
  - REVIEW: Unklare Dokumente -> Lokale SQLite-DB ("Pending Review") zur Freigabe via App.
"""

import os
import sys
import re
import time
import json
import sqlite3
import imaplib
import email
from email.header import decode_header
import logging
import threading
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [HermesEmailIngest]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("HermesEmailIngest")

# Pfade und Default-Konfigurationen
HERMES_DIR = Path(os.path.expanduser("~/.hermes"))
DEFAULT_DB_PATH = HERMES_DIR / "email_ingest.db"
DEFAULT_ATTACHMENTS_DIR = HERMES_DIR / "attachments"
HERMES_EMAIL_CREDS = HERMES_DIR / "email_creds.json"
GMAIL_CREDS_FILE = Path(os.path.expanduser("~/.config/google/gmail_creds.json"))
HERMES_ENV_FILE = HERMES_DIR / ".env"

PAPERLESS_URL = os.getenv("PAPERLESS_URL", "http://192.168.188.96:8000")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "428107bcaa486337ba98b06547d0a843b3007036")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.getenv("HERMES_EMAIL_MODEL", "qwen2.5:7b")


def get_db_path() -> Path:
    """Ermittelt den Speicherort für die SQLite-Ingest-Datenbank."""
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_DB_PATH
    except Exception:
        fallback = Path(__file__).resolve().parent / "email_ingest.db"
        return fallback


def get_attachments_dir() -> Path:
    """Ermittelt den Speicherort für zwischengespeicherte Anhänge."""
    try:
        DEFAULT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_ATTACHMENTS_DIR
    except Exception:
        fallback = Path(__file__).resolve().parent / "attachments"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def init_ingest_db(db_path: Optional[Path] = None) -> Path:
    """Initialisiert die SQLite Ingest & Review Tabellen falls nicht vorhanden."""
    path = db_path or get_db_path()
    with sqlite3.connect(str(path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_inbox_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                subject TEXT NOT NULL,
                received_at TEXT NOT NULL,
                summary TEXT,
                category TEXT NOT NULL DEFAULT 'REVIEW',
                confidence REAL DEFAULT 0.0,
                attachment_name TEXT,
                attachment_path TEXT,
                attachment_size INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                llm_response TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_ingest_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                sender TEXT,
                subject TEXT,
                category TEXT,
                action_taken TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    return path


def load_env_variables() -> Dict[str, str]:
    """Liest Umgebungsvariablen aus ~/.hermes/.env ein."""
    env_vars: Dict[str, str] = {}
    if HERMES_ENV_FILE.is_file():
        try:
            with open(HERMES_ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning("Konnte ~/.hermes/.env nicht parsen: %s", e)
    return env_vars


def load_imap_credentials() -> Dict[str, Any]:
    """
    Lädt IMAP Zugangsdaten aus:
    1. Umgebungsvariablen (IMAP_HOST, IMAP_USER, IMAP_PASSWORD, etc.)
    2. ~/.hermes/email_creds.json
    3. ~/.config/google/gmail_creds.json
    """
    env = load_env_variables()
    creds: Dict[str, Any] = {
        "imap_host": os.getenv("IMAP_HOST") or env.get("IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(os.getenv("IMAP_PORT") or env.get("IMAP_PORT", "993")),
        "imap_user": os.getenv("IMAP_USER") or env.get("IMAP_USER", ""),
        "imap_pass": os.getenv("IMAP_PASSWORD") or env.get("IMAP_PASSWORD", ""),
        "imap_folder": os.getenv("IMAP_FOLDER") or env.get("IMAP_FOLDER", "INBOX"),
        "poll_interval": int(os.getenv("IMAP_POLL_INTERVAL_SEC") or env.get("IMAP_POLL_INTERVAL_SEC", "60")),
        "configured": False
    }

    if HERMES_EMAIL_CREDS.is_file():
        try:
            with open(HERMES_EMAIL_CREDS, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("imap_user") and data.get("imap_pass"):
                    creds["imap_user"] = data.get("imap_user")
                    creds["imap_pass"] = data.get("imap_pass")
                    creds["imap_host"] = data.get("imap_host", creds["imap_host"])
                    creds["imap_port"] = int(data.get("imap_port", creds["imap_port"]))
                    creds["configured"] = True
                    return creds
                elif data.get("smtp_user") and data.get("smtp_pass"):
                    creds["imap_user"] = data.get("smtp_user")
                    creds["imap_pass"] = data.get("smtp_pass")
                    creds["imap_host"] = data.get("imap_host", "imap.gmail.com")
                    creds["configured"] = True
                    return creds
        except Exception as e:
            logger.warning("Fehler beim Lesen von %s: %s", HERMES_EMAIL_CREDS, e)

    if GMAIL_CREDS_FILE.is_file():
        try:
            with open(GMAIL_CREDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                email_addr = data.get("email")
                app_pass = data.get("app_password", "").replace(" ", "")
                if email_addr and app_pass:
                    creds["imap_user"] = email_addr
                    creds["imap_pass"] = app_pass
                    creds["imap_host"] = "imap.gmail.com"
                    creds["imap_port"] = 993
                    creds["configured"] = True
                    return creds
        except Exception as e:
            logger.warning("Fehler beim Lesen von %s: %s", GMAIL_CREDS_FILE, e)

    if creds["imap_user"] and creds["imap_pass"]:
        creds["configured"] = True

    return creds


# ==============================================================================
# E-Mail & MIME Extraktion
# ==============================================================================

def decode_mime_header(header_value: Optional[str]) -> str:
    """Dekodiert RFC 2047 kodierte Headerzeilen."""
    if not header_value:
        return ""
    decoded_fragments = decode_header(header_value)
    result = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            try:
                result.append(fragment.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                result.append(fragment.decode("latin-1", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result)


def extract_email_data(raw_bytes: bytes) -> Dict[str, Any]:
    """
    Parst rohe E-Mail-Bytes und extrahiert Metadaten, Body-Text und Anhänge.
    """
    msg = email.message_from_bytes(raw_bytes)

    subject = decode_mime_header(msg.get("Subject", "(Kein Betreff)"))
    sender = decode_mime_header(msg.get("From", "Unbekannt"))
    message_id = msg.get("Message-ID", "")
    date_str = msg.get("Date", "")

    body_text_parts = []
    attachments = []

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        filename = part.get_filename()
        if filename:
            filename = decode_mime_header(filename)

        if "attachment" in content_disposition or filename:
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append({
                    "filename": filename or f"attachment_{len(attachments)+1}.bin",
                    "content_type": content_type,
                    "size": len(payload),
                    "data": payload
                })
        elif content_type == "text/plain" and "attachment" not in content_disposition:
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    body_text_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    body_text_parts.append(payload.decode("utf-8", errors="replace"))
        elif content_type == "text/html" and "attachment" not in content_disposition and not body_text_parts:
            # Fallback falls kein reiner Text vorliegt
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    clean_text = re.sub(r"<[^>]+>", " ", html)
                    clean_text = re.sub(r"\s+", " ", clean_text).strip()
                    body_text_parts.append(clean_text)
                except Exception:
                    pass

    full_body = "\n".join(body_text_parts).strip()

    return {
        "message_id": message_id,
        "sender": sender,
        "subject": subject,
        "date": date_str,
        "body": full_body,
        "attachments": attachments
    }


# ==============================================================================
# KI-Klassifizierung (via lokales Ollama/LLM)
# ==============================================================================

def classify_email_with_llm(
    sender: str,
    subject: str,
    body: str,
    attachment_names: List[str],
    ollama_url: Optional[str] = None,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Klassifiziert eine E-Mail via LLM in SPAM, PAPERLESS oder REVIEW.
    """
    url = ollama_url or OLLAMA_URL
    target_model = model or DEFAULT_MODEL
    att_str = ", ".join(attachment_names) if attachment_names else "Keine Anhänge"

    system_prompt = (
        "Du bist das intelligente E-Mail-Ingest- und Triage-Modul von Hermes.\n"
        "Deine Aufgabe ist es, eingehende E-Mails strikt in genau EINE von drei Kategorien einzustufen:\n"
        "1. 'SPAM': Unerwünschte Werbung, Newsletter, Marketing, Phishing, Gewinnspiele, automatisierter Werbemüll.\n"
        "2. 'PAPERLESS': Wichtige Dokumente zur dauerhaften Archivierung. Rechnungen (z.B. Telekom, Strom, Hosting, Amazon), "
        "Behördenschreiben (Arbeitsagentur, Jobcenter, Finanzamt, Krankenkasse, Rentenversicherung), Verträge, Gehaltsabrechnungen, Mahnungen.\n"
        "3. 'REVIEW': Unklare Fälle, persönliche Nachrichten von Menschen, wichtige Fragen oder Dokumente, bei denen Unsicherheit herrscht.\n\n"
        "Regeln für die Triage:\n"
        "- Wenn es eindeutig eine Rechnung oder Behördenpost mit Anhang ist -> 'PAPERLESS'.\n"
        "- Wenn es eindeutig Werbung / Rabattaktion / Newsletter ist -> 'SPAM'.\n"
        "- Bei jeglicher Unsicherheit oder wenn eine manuelle Bestätigung sinnvoll ist -> 'REVIEW'.\n"
        "- Antworte AUSSCHLIESSLICH im geforderten JSON-Format."
    )

    user_prompt = f"""
E-Mail-Details:
- Absender: {sender}
- Betreff: {subject}
- Anhänge: {att_str}
- Textauszug:
{body[:2500]}

Antworte im folgenden JSON-Format:
{{
  "category": "SPAM|PAPERLESS|REVIEW",
  "confidence": 0.95,
  "summary": "1-2 Sätze auf Deutsch: Was ist der Kerninhalt und warum diese Einstufung?",
  "suggested_title": "YYYY-MM-DD [Absender] - [Dokumenttyp] - [Betreff]",
  "correspondent": "Name des Absenders / Unternehmens",
  "document_type": "Rechnung|Bescheid|Mahnung|Vertrag|Werbung|Info"
}}
"""

    models_to_try = [target_model, "qwen2.5:7b", "hermes3:8b", "darkidol-8b:latest"]
    for m in models_to_try:
        try:
            req_data = json.dumps({
                "model": m,
                "prompt": user_prompt,
                "system": system_prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.1}
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{url}/api/generate",
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                raw_text = res_data.get("response", "").strip()

                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                    raw_text = re.sub(r"\n?```$", "", raw_text).strip()

                parsed = json.loads(raw_text)
                cat = str(parsed.get("category", "REVIEW")).upper()
                if cat not in ["SPAM", "PAPERLESS", "REVIEW"]:
                    cat = "REVIEW"
                parsed["category"] = cat
                logger.info("E-Mail [%s] von '%s' klassifiziert als: %s (Modell: %s)", subject[:30], sender[:30], cat, m)
                return parsed
        except Exception as e:
            logger.debug("Ollama-Triage mit Modell '%s' fehlgeschlagen: %s", m, e)

    # Heuristischer Fallback falls Ollama nicht antwortet
    subject_lower = subject.lower()
    body_lower = body.lower()
    sender_lower = sender.lower()

    spam_keywords = ["rabatt", "abmelden", "newsletter", "unsubscribe", "gutschein", "sale", "angebot", "prozent"]
    paperless_keywords = ["rechnung", "invoice", "arbeitsagentur", "bescheid", "mahnung", "aktenzeichen", "abrechnung"]

    if any(k in subject_lower or k in sender_lower for k in spam_keywords):
        fallback_cat = "SPAM"
        summary = "Heuristisch als Werbe-E-Mail / Newsletter erkannt (Ollama Offline-Fallback)."
    elif any(k in subject_lower or k in body_lower for k in paperless_keywords) or any(a.lower().endswith(".pdf") for a in attachment_names):
        fallback_cat = "PAPERLESS"
        summary = "Heuristisch als rechnungs- oder behördenrelevantes Dokument erkannt."
    else:
        fallback_cat = "REVIEW"
        summary = "Unklarer E-Mail-Inhalt; manuelle Überprüfung empfohlen."

    return {
        "category": fallback_cat,
        "confidence": 0.6,
        "summary": summary,
        "suggested_title": f"{datetime.now().strftime('%Y-%m-%d')} {subject[:40]}",
        "correspondent": sender,
        "document_type": "Info"
    }


# ==============================================================================
# Paperless-ngx Dispatcher
# ==============================================================================

def send_to_paperless(
    file_bytes: bytes,
    filename: str,
    title: Optional[str] = None,
    paperless_url: Optional[str] = None,
    token: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Sendet eine Datei via Multipart/form-data an Paperless-ngx.
    POST /api/documents/post_document/
    """
    url = (paperless_url or PAPERLESS_URL).rstrip("/")
    api_token = token or PAPERLESS_TOKEN
    post_url = f"{url}/api/documents/post_document/"

    boundary = "----HermesFormBoundary" + str(int(time.time() * 1000))
    crlf = "\r\n"

    parts = []
    # File field
    parts.append(f"--{boundary}".encode("utf-8"))
    parts.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode("utf-8"))
    mime_type = "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
    parts.append(f'Content-Type: {mime_type}'.encode("utf-8"))
    parts.append(b"")
    parts.append(file_bytes)

    # Optional Title
    if title:
        parts.append(f"--{boundary}".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="title"'.encode("utf-8"))
        parts.append(b"")
        parts.append(title.encode("utf-8"))

    parts.append(f"--{boundary}--".encode("utf-8"))
    parts.append(b"")

    body_bytes = crlf.encode("utf-8").join(parts)

    headers = {
        "Authorization": f"Token {api_token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body_bytes))
    }

    try:
        req = urllib.request.Request(post_url, data=body_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            if resp.status in [200, 201, 202]:
                logger.info("Dokument '%s' erfolgreich an Paperless übertragen (Status %d)", filename, resp.status)
                return True, f"Erfolgreich an Paperless gesendet: {resp_body}"
            return False, f"Paperless HTTP Status {resp.status}: {resp_body}"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        logger.error("Paperless HTTP Fehler %d: %s", e.code, err_msg)
        return False, f"Paperless HTTP {e.code}: {err_msg}"
    except Exception as e:
        logger.error("Verbindungsfehler zu Paperless: %s", e)
        return False, str(e)


# ==============================================================================
# SQLite Review Management
# ==============================================================================

def add_review_item(
    sender: str,
    subject: str,
    received_at: str,
    summary: str,
    category: str,
    confidence: float,
    attachment_name: Optional[str] = None,
    attachment_data: Optional[bytes] = None,
    llm_response: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None
) -> int:
    """Speichert ein unklares Dokument in der SQLite Review-Tabelle."""
    path = db_path or init_ingest_db()
    att_path_str = None
    att_size = 0

    if attachment_data and attachment_name:
        att_dir = get_attachments_dir()
        safe_name = re.sub(r"[^\w\-.]", "_", attachment_name)
        timestamp_prefix = datetime.now().strftime("%Y%m%d_%H%M%S_")
        target_file = att_dir / f"{timestamp_prefix}{safe_name}"
        try:
            with open(target_file, "wb") as f:
                f.write(attachment_data)
            att_path_str = str(target_file)
            att_size = len(attachment_data)
        except Exception as e:
            logger.error("Konnte Anhang nicht speichern: %s", e)

    now_iso = datetime.now(timezone.utc).isoformat()
    llm_json_str = json.dumps(llm_response or {}, ensure_ascii=False)

    with sqlite3.connect(str(path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO email_inbox_reviews (
                sender, subject, received_at, summary, category, confidence,
                attachment_name, attachment_path, attachment_size, status,
                llm_response, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """, (
            sender, subject, received_at, summary, category, confidence,
            attachment_name, att_path_str, att_size, llm_json_str,
            now_iso, now_iso
        ))
        conn.commit()
        item_id = cursor.lastrowid
        logger.info("Review-Element #%d angelegt für Betreff: '%s'", item_id, subject[:30])
        return item_id


def list_pending_reviews(limit: int = 50, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Gibt alle offenen Review-Elemente zurück."""
    path = db_path or init_ingest_db()
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sender, subject, received_at, summary, category, confidence,
                   attachment_name, attachment_size, status, llm_response, created_at
            FROM email_inbox_reviews
            WHERE status = 'pending'
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("llm_response"):
                try:
                    d["llm_response"] = json.loads(d["llm_response"])
                except Exception:
                    pass
            results.append(d)
        return results


def resolve_review_item(
    item_id: int,
    action: str,  # "DELETE" oder "SEND_TO_PAPERLESS"
    custom_title: Optional[str] = None,
    db_path: Optional[Path] = None
) -> Tuple[bool, str]:
    """
    Führt die vom User gewünschte Aktion aus (Löschen oder an Paperless senden).
    """
    path = db_path or init_ingest_db()
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM email_inbox_reviews WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            return False, f"Review-Element #{item_id} nicht gefunden."

        item = dict(row)
        action_upper = action.strip().upper()
        now_iso = datetime.now(timezone.utc).isoformat()

        if action_upper == "DELETE":
            # Anhang-Datei löschen falls vorhanden
            att_path = item.get("attachment_path")
            if att_path and os.path.isfile(att_path):
                try:
                    os.remove(att_path)
                except Exception as e:
                    logger.warning("Konnte temporäre Datei %s nicht entfernen: %s", att_path, e)

            cursor.execute("""
                UPDATE email_inbox_reviews
                SET status = 'deleted', updated_at = ?
                WHERE id = ?
            """, (now_iso, item_id))
            conn.commit()
            logger.info("Review-Element #%d erfolgreich gelöscht.", item_id)
            return True, f"Review-Element #{item_id} gelöscht."

        elif action_upper == "SEND_TO_PAPERLESS":
            att_path = item.get("attachment_path")
            file_bytes = None
            filename = item.get("attachment_name") or "email_document.pdf"

            if att_path and os.path.isfile(att_path):
                try:
                    with open(att_path, "rb") as f:
                        file_bytes = f.read()
                except Exception as e:
                    return False, f"Konnte Anhang-Datei nicht lesen: {e}"
            else:
                # Text als Beleg erzeugen wenn kein Anhang da war
                doc_text = f"Absender: {item['sender']}\nBetreff: {item['subject']}\nDatum: {item['received_at']}\n\nZusammenfassung:\n{item['summary']}"
                file_bytes = doc_text.encode("utf-8")
                filename = f"email_{item_id}.txt"

            title_to_use = custom_title or item.get("subject") or filename
            success, msg = send_to_paperless(file_bytes, filename, title=title_to_use)

            if success:
                # Temporäre Datei aufräumen
                if att_path and os.path.isfile(att_path):
                    try:
                        os.remove(att_path)
                    except Exception:
                        pass
                cursor.execute("""
                    UPDATE email_inbox_reviews
                    SET status = 'sent_to_paperless', updated_at = ?
                    WHERE id = ?
                """, (now_iso, item_id))
                conn.commit()
                return True, f"Dokument #{item_id} erfolgreich an Paperless übertragen."
            else:
                cursor.execute("""
                    UPDATE email_inbox_reviews
                    SET error_message = ?, updated_at = ?
                    WHERE id = ?
                """, (msg, now_iso, item_id))
                conn.commit()
                return False, f"Übertragung an Paperless fehlgeschlagen: {msg}"

        else:
            return False, f"Unbekannte Aktion: {action}. Erlaubt sind 'DELETE' und 'SEND_TO_PAPERLESS'."


# ==============================================================================
# IMAP Polling & Verarbeitungs-Worker
# ==============================================================================

class EmailIngestWorker:
    """
    Hintergrund-Worker, der periodisch eine IMAP-Mailbox pollt,
    E-Mails abholt, per LLM klassifiziert und Aktionen ausführt.
    """
    def __init__(self, poll_interval: Optional[int] = None):
        self.creds = load_imap_credentials()
        self.poll_interval = poll_interval or self.creds.get("poll_interval", 60)
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Startet den Polling-Worker in einem separaten Thread."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, name="HermesEmailIngestWorker", daemon=True)
        self.thread.start()
        logger.info("Hermes E-Mail Ingest Worker gestartet (Intervall: %ds).", self.poll_interval)

    def stop(self):
        """Stoppt den Worker sicher."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        logger.info("Hermes E-Mail Ingest Worker beendet.")

    def _run_loop(self):
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error("Fehler beim IMAP-Polling Durchlauf: %s", e)
            for _ in range(self.poll_interval):
                if not self.running:
                    break
                time.sleep(1)

    def poll_once(self) -> int:
        """
        Führt einen einzelnen Abruf durch.
        Gibt die Anzahl verarbeiteter E-Mails zurück.
        """
        self.creds = load_imap_credentials()
        if not self.creds.get("configured") or not self.creds.get("imap_user") or not self.creds.get("imap_pass"):
            logger.debug("IMAP nicht vollständig konfiguriert - überspringe Abruf.")
            return 0

        host = self.creds.get("imap_host", "imap.gmail.com")
        port = self.creds.get("imap_port", 993)
        user = self.creds.get("imap_user")
        pw = self.creds.get("imap_pass")
        folder = self.creds.get("imap_folder", "INBOX")

        processed_count = 0

        try:
            mail = imaplib.IMAP4_SSL(host, port, timeout=20)
            mail.login(user, pw)
            status, _ = mail.select(folder)
            if status != "OK":
                logger.warning("Konnte IMAP Ordner '%s' nicht öffnen.", folder)
                mail.logout()
                return 0

            # Suche nach ungelesenen Nachrichten
            status, data = mail.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                mail.logout()
                return 0

            mail_ids = data[0].split()
            logger.info("%d neue ungelesene E-Mail(s) in '%s' gefunden.", len(mail_ids), folder)

            for m_id in mail_ids:
                if not self.running:
                    break
                try:
                    res, msg_data = mail.fetch(m_id, "(RFC822)")
                    if res != "OK" or not msg_data:
                        continue

                    raw_email = msg_data[0][1]
                    email_data = extract_email_data(raw_email)

                    sender = email_data["sender"]
                    subject = email_data["subject"]
                    body = email_data["body"]
                    attachments = email_data["attachments"]
                    att_names = [a["filename"] for a in attachments]

                    # KI-Klassifizierung
                    classification = classify_email_with_llm(
                        sender=sender,
                        subject=subject,
                        body=body,
                        attachment_names=att_names
                    )
                    category = classification.get("category", "REVIEW")
                    summary = classification.get("summary", "")
                    title = classification.get("suggested_title", subject)

                    if category == "SPAM":
                        # Markieren zum Löschen
                        mail.store(m_id, "+FLAGS", "\\Deleted")
                        logger.info("🗑️ E-Mail '%s' als SPAM erkannt und zum Löschen markiert.", subject[:40])

                    elif category == "PAPERLESS":
                        # Belege extrahieren & an Paperless senden
                        uploaded_any = False
                        if attachments:
                            for att in attachments:
                                ok, p_msg = send_to_paperless(att["data"], att["filename"], title=title)
                                if ok:
                                    uploaded_any = True
                        else:
                            # E-Mail selbst als Dokument
                            doc_bytes = f"Von: {sender}\nBetreff: {subject}\nDatum: {email_data['date']}\n\n{body}".encode("utf-8")
                            ok, _ = send_to_paperless(doc_bytes, f"email_{int(time.time())}.txt", title=title)
                            if ok:
                                uploaded_any = True

                        # E-Mail als gelesen markieren
                        mail.store(m_id, "+FLAGS", "\\Seen")
                        logger.info("📄 E-Mail '%s' als PAPERLESS verarbeitet (%s).", subject[:40], "Erfolgreich" if uploaded_any else "Kein Upload")

                    else:  # REVIEW
                        first_att = attachments[0] if attachments else None
                        add_review_item(
                            sender=sender,
                            subject=subject,
                            received_at=email_data["date"] or datetime.now().isoformat(),
                            summary=summary,
                            category=category,
                            confidence=float(classification.get("confidence", 0.5)),
                            attachment_name=first_att["filename"] if first_att else None,
                            attachment_data=first_att["data"] if first_att else None,
                            llm_response=classification
                        )
                        # Als gelesen markieren, damit wir sie nicht in jedem Zyklus erneut erfassen
                        mail.store(m_id, "+FLAGS", "\\Seen")
                        logger.info("📋 E-Mail '%s' zur manuellen REVIEW vorgemerkt.", subject[:40])

                    processed_count += 1
                except Exception as inner_ex:
                    logger.error("Fehler bei Verarbeitung von E-Mail ID %s: %s", m_id, inner_ex)

            # Löschungen anwenden
            try:
                mail.expunge()
            except Exception:
                pass
            mail.logout()

        except Exception as e:
            logger.warning("IMAP-Verbindungsfehler: %s", e)

        return processed_count


# Globaler Worker-Singleton
_ingest_worker: Optional[EmailIngestWorker] = None

def get_ingest_worker() -> EmailIngestWorker:
    global _ingest_worker
    if _ingest_worker is None:
        _ingest_worker = EmailIngestWorker()
    return _ingest_worker


if __name__ == "__main__":
    init_ingest_db()
    worker = get_ingest_worker()
    print("Hermes E-Mail Ingest Modul gestartet. Teste einmaligen Abruf...")
    count = worker.poll_once()
    print(f"Abruf abgeschlossen. {count} E-Mails verarbeitet.")
