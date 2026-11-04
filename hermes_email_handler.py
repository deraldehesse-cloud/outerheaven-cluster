#!/usr/bin/env python3
"""
hermes_email_handler.py - Hermes Automated Email Dispatch & Outbox Handler
==========================================================================
Verwaltet E-Mail-Versand (SMTP), Entwurfsablage (IMAP & SQLite Outbox),
autonome LLM-Textgenerierung (megan:hermes) und Benachrichtigungen
(Desktop notify-send & Telegram Bot Push).
"""

import os
import sys
import json
import sqlite3
import smtplib
import imaplib
import logging
import subprocess
import urllib.request
import urllib.error
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [HermesEmail]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("HermesEmail")

# Standard-Pfade
HERMES_DIR = Path(os.path.expanduser("~/.hermes"))
DEFAULT_DB_PATH = HERMES_DIR / "email_outbox.db"
GMAIL_CREDS_FILE = Path(os.path.expanduser("~/.config/google/gmail_creds.json"))
HERMES_ENV_FILE = HERMES_DIR / ".env"
HERMES_EMAIL_CREDS = HERMES_DIR / "email_creds.json"


def get_db_path() -> Path:
    """Ermittelt den Speicherort für die SQLite-Outbox."""
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_DB_PATH
    except Exception:
        fallback = Path(__file__).resolve().parent / "email_outbox.db"
        return fallback


def init_outbox_db(db_path: Optional[Path] = None) -> Path:
    """Initialisiert die SQLite Outbox-Tabelle falls nicht existent."""
    path = db_path or get_db_path()
    with sqlite3.connect(str(path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS outbox_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                is_draft INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                sent_at TEXT,
                error_message TEXT,
                metadata TEXT
            )
        """)
        conn.commit()
    return path


def load_env_variables() -> Dict[str, str]:
    """Liest Umgebungsvariablen aus ~/.hermes/.env ein, falls vorhanden."""
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


def load_mail_credentials() -> Dict[str, Any]:
    """
    Lädt SMTP/IMAP Zugangsdaten:
    1. ~/.hermes/email_creds.json
    2. ~/.config/google/gmail_creds.json
    3. Umgebungsvariablen (SMTP_USER, SMTP_PASSWORD, etc.)
    """
    creds: Dict[str, Any] = {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_user": os.getenv("SMTP_USER", ""),
        "smtp_pass": os.getenv("SMTP_PASSWORD", ""),
        "imap_host": os.getenv("IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(os.getenv("IMAP_PORT", "993")),
        "sender_name": os.getenv("EMAIL_SENDER_NAME", "Sebastian (Big Boss)"),
        "configured": False
    }

    if HERMES_EMAIL_CREDS.is_file():
        try:
            with open(HERMES_EMAIL_CREDS, "r", encoding="utf-8") as f:
                data = json.load(f)
                creds.update(data)
                if creds.get("smtp_user") and creds.get("smtp_pass"):
                    creds["configured"] = True
                    return creds
        except Exception as e:
            logger.warning("Fehler beim Lesen von %s: %s", HERMES_EMAIL_CREDS, e)

    if GMAIL_CREDS_FILE.is_file():
        try:
            with open(GMAIL_CREDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                email = data.get("email")
                app_pass = data.get("app_password", "").replace(" ", "")
                if email and app_pass:
                    creds["smtp_user"] = email
                    creds["smtp_pass"] = app_pass
                    creds["smtp_host"] = "smtp.gmail.com"
                    creds["smtp_port"] = 587
                    creds["imap_host"] = "imap.gmail.com"
                    creds["imap_port"] = 993
                    creds["configured"] = True
                    return creds
        except Exception as e:
            logger.warning("Fehler beim Lesen von %s: %s", GMAIL_CREDS_FILE, e)

    if creds["smtp_user"] and creds["smtp_pass"]:
        creds["configured"] = True

    return creds


# ==============================================================================
# Benachrichtigungen (Desktop & Telegram)
# ==============================================================================

def send_desktop_notification(title: str, message: str) -> bool:
    """Sendet eine Desktop-Benachrichtigung über notify-send."""
    try:
        res = subprocess.run(
            ["notify-send", "-a", "Hermes Agent", title, message],
            capture_output=True,
            timeout=5
        )
        return res.returncode == 0
    except Exception as e:
        logger.debug("notify-send nicht verfügbar oder fehlgeschlagen: %s", e)
        return False


def send_telegram_notification(text: str) -> bool:
    """Sendet Benachrichtigung über Telegram Bot API falls konfiguriert."""
    env = load_env_variables()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    allowed = os.getenv("TELEGRAM_ALLOWED_USERS") or env.get("TELEGRAM_ALLOWED_USERS") or env.get("TELEGRAM_HOME_CHANNEL")
    
    if not token or not allowed:
        logger.debug("Keine Telegram-Konfiguration für Benachrichtigung gefunden.")
        return False

    chat_id = allowed.split(",")[0].strip() if "," in allowed else allowed.strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Telegram-Notification fehlgeschlagen: %s", e)
        return False


def notify_email_event(recipient: str, subject: str, status: str = "gesendet") -> None:
    """Erzeugt synchrone Desktop- und Telegram-Benachrichtigung."""
    desktop_title = "✉️ Hermes E-Mail"
    desktop_msg = f"E-Mail an [{recipient}] {status}.\nBetreff: {subject[:40]}"
    send_desktop_notification(desktop_title, desktop_msg)

    tg_msg = f"✉️ *Hermes E-Mail Dispatch*\nStatus: `{status.upper()}`\nEmpfänger: `{recipient}`\nBetreff: _{subject}_\n"
    send_telegram_notification(tg_msg)


# ==============================================================================
# SMTP & IMAP Aktionen
# ==============================================================================

def send_smtp_email(recipient: str, subject: str, body: str, creds: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Versendet eine E-Mail über SMTP (STARTTLS oder SSL).
    Gibt (Erfolg: bool, Statusmeldung: str) zurück.
    """
    creds = creds or load_mail_credentials()

    if not creds.get("configured") or not creds.get("smtp_user") or not creds.get("smtp_pass"):
        msg = "SMTP nicht konfiguriert (Simulationsmodus: E-Mail als versendet protokolliert)"
        logger.info(msg)
        return True, msg

    smtp_host = creds.get("smtp_host", "smtp.gmail.com")
    smtp_port = creds.get("smtp_port", 587)
    smtp_user = creds.get("smtp_user")
    smtp_pass = creds.get("smtp_pass")
    sender_name = creds.get("sender_name", "Sebastian")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{smtp_user}>"
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=smtp_user.split("@")[-1] if "@" in smtp_user else "outerheaven.local")
    msg.set_content(body)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        
        logger.info("E-Mail erfolgreich an %s gesendet.", recipient)
        return True, "E-Mail erfolgreich über SMTP gesendet."
    except Exception as e:
        err = f"SMTP-Fehler beim Senden an {recipient}: {e}"
        logger.error(err)
        return False, err


def save_draft_to_imap(recipient: str, subject: str, body: str, creds: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Speichert den Entwurf im IMAP-Ordner 'Drafts' / '[Gmail]/Drafts'.
    """
    creds = creds or load_mail_credentials()

    if not creds.get("configured") or not creds.get("smtp_user") or not creds.get("smtp_pass"):
        return True, "IMAP nicht konfiguriert (Entwurf lokal in Outbox gespeichert)"

    imap_host = creds.get("imap_host", "imap.gmail.com")
    imap_port = creds.get("imap_port", 993)
    user = creds.get("smtp_user")
    pw = creds.get("smtp_pass")
    sender_name = creds.get("sender_name", "Sebastian")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{user}>"
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)

    try:
        import time
        mail = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=15)
        mail.login(user, pw)

        # Zielordner ermitteln
        draft_folders = ["[Gmail]/Drafts", "[Gmail]/Entwürfe", "Drafts", "Entwürfe"]
        chosen_folder = None
        for folder in draft_folders:
            st, _ = mail.select(folder)
            if st == "OK":
                chosen_folder = folder
                break

        if not chosen_folder:
            chosen_folder = "Drafts"
            mail.create(chosen_folder)

        raw_bytes = msg.as_bytes()
        mail.append(chosen_folder, "\\Draft", imaplib.Time2Internaldate(time.time()), raw_bytes)
        mail.logout()
        logger.info("Entwurf via IMAP in '%s' gespeichert.", chosen_folder)
        return True, f"Entwurf via IMAP in '{chosen_folder}' gespeichert."
    except Exception as e:
        logger.warning("Konnte Entwurf nicht via IMAP synchronisieren (%s). Bleibt lokal in Outbox.", e)
        return False, str(e)


# ==============================================================================
# Outbox Database CRUD
# ==============================================================================

def create_email_record(
    recipient: str,
    subject: str,
    body: str,
    is_draft: bool = True,
    document_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    auto_send: bool = False
) -> Dict[str, Any]:
    """
    Erstellt einen E-Mail-Eintrag in der SQLite-Outbox.
    Falls auto_send=True oder is_draft=False, wird die Mail direkt versendet.
    """
    db_path = init_outbox_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    meta_str = json.dumps(metadata or {}, ensure_ascii=False)

    initial_status = "draft" if is_draft and not auto_send else "pending_send"
    record_id = None

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO outbox_emails (
                document_id, recipient, subject, body, is_draft, status, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(document_id) if document_id is not None else None,
            recipient.strip(),
            subject.strip(),
            body.strip(),
            1 if is_draft and not auto_send else 0,
            initial_status,
            now_iso,
            meta_str
        ))
        conn.commit()
        record_id = cursor.lastrowid

    # Wenn Entwurf angefordert
    if is_draft and not auto_send:
        # Optional IMAP-Sync
        try:
            save_draft_to_imap(recipient, subject, body)
        except Exception as e:
            logger.debug("IMAP-Sync übersprungen: %s", e)

        return get_outbox_item(record_id)

    # Wenn Auto-Send
    success, msg = send_smtp_email(recipient, subject, body)
    sent_iso = datetime.now(timezone.utc).isoformat() if success else None
    final_status = "sent" if success else "failed"

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE outbox_emails
            SET status = ?, is_draft = 0, sent_at = ?, error_message = ?
            WHERE id = ?
        """, (final_status, sent_iso, None if success else msg, record_id))
        conn.commit()

    if success:
        notify_email_event(recipient, subject, status="verschickt")

    return get_outbox_item(record_id)


def send_existing_draft(draft_id: int) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Gibt einen bestehenden Entwurf frei und versendet ihn per SMTP.
    Aktualisiert den Status auf 'sent' und triggert Notifications.
    """
    db_path = init_outbox_db()
    item = get_outbox_item(draft_id)
    if not item:
        return False, f"Entwurf #{draft_id} nicht gefunden.", {}

    if item.get("status") == "sent":
        return True, "E-Mail wurde bereits zuvor versendet.", item

    recipient = item.get("recipient", "")
    subject = item.get("subject", "")
    body = item.get("body", "")

    success, msg = send_smtp_email(recipient, subject, body)
    sent_iso = datetime.now(timezone.utc).isoformat() if success else None
    new_status = "sent" if success else "failed"

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE outbox_emails
            SET status = ?, is_draft = 0, sent_at = ?, error_message = ?
            WHERE id = ?
        """, (new_status, sent_iso, None if success else msg, draft_id))
        conn.commit()

    updated = get_outbox_item(draft_id)
    if success:
        notify_email_event(recipient, subject, status="verschickt")

    return success, msg, updated


def update_existing_draft(
    draft_id: int,
    recipient: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Aktualisiert Inhalt eines bestehenden Entwurfs."""
    db_path = init_outbox_db()
    item = get_outbox_item(draft_id)
    if not item:
        return None

    new_recipient = recipient.strip() if recipient is not None else item["recipient"]
    new_subject = subject.strip() if subject is not None else item["subject"]
    new_body = body.strip() if body is not None else item["body"]

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE outbox_emails
            SET recipient = ?, subject = ?, body = ?
            WHERE id = ?
        """, (new_recipient, new_subject, new_body, draft_id))
        conn.commit()

    return get_outbox_item(draft_id)


def cancel_existing_draft(draft_id: int, delete_permanent: bool = False) -> bool:
    """Verwirft oder löscht einen Entwurf."""
    db_path = init_outbox_db()
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        if delete_permanent:
            cursor.execute("DELETE FROM outbox_emails WHERE id = ?", (draft_id,))
        else:
            cursor.execute("UPDATE outbox_emails SET status = 'cancelled' WHERE id = ?", (draft_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_outbox_item(item_id: int) -> Optional[Dict[str, Any]]:
    """Gibt einen einzelnen Eintrag als Dict zurück."""
    db_path = init_outbox_db()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM outbox_emails WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        except Exception:
            pass
        return d


def list_outbox(status_filter: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Listet Outbox-Einträge sortiert nach ID absteigend."""
    db_path = init_outbox_db()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if status_filter:
            cursor.execute(
                "SELECT * FROM outbox_emails WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status_filter, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM outbox_emails ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
            except Exception:
                pass
            result.append(d)
        return result


# ==============================================================================
# Prompt-Logik für autonome E-Mails (megan:hermes)
# ==============================================================================

def generate_autonomous_email_text(
    correspondent: str,
    aktenzeichen: str,
    betrag: Optional[Any] = None,
    due_date: Optional[str] = None,
    doc_summary: str = "",
    doc_type: str = "Rechnung",
    user_name: str = "Sebastian",
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "megan:hermes"
) -> Dict[str, str]:
    """
    Formuliert via Ollama (megan:hermes oder Fallback) eine professionelle, deeskalierende E-Mail.
    Beispiel:
    'Sehr geehrte Damen und Herren, Ihre Nachricht zum Aktenzeichen X liegt vor und wird bis zum [Datum] geprüft/überwiesen. Mit freundlichen Grüßen.'
    """
    target_date = due_date or datetime.now().strftime("%d.%m.%Y")
    betrag_str = f"{betrag} EUR" if betrag else "entsprechendem Beleg"

    system_prompt = (
        f"Du bist Hermes, ein loyaler und professioneller Büro- und Kommunikationsassistent für {user_name}.\n"
        "Deine Aufgabe: Verfasse ein kurzes, klares, deeskalierendes und verbindliches deutsches Antwortschreiben "
        "(E-Mail) auf ein eingegangenes Dokument (Rechnung, Mahnung, behördliches Schreiben oder Anfrage).\n"
        "Tonalität:\n"
        "- Extrem professionell, sachlich, ruhig und deeskalierend.\n"
        "- Bestätige den Eingang des Vorgangs unter Nennung des Aktenzeichens / Vorgangszeichens.\n"
        "- Gib einen klaren Prüf- oder Bearbeitungszeitraum an (z. B. 'liegt vor und wird bis zum [Datum] geprüft bzw. angewiesen').\n"
        "- Schließe mit einer formal korrekten Grußformel und dem Namen des Absenders.\n"
        "- Antworte AUSSCHLIESSLICH im geforderten JSON-Format mit 'subject' und 'body'. Keine Erklärungen rundherum."
    )

    user_prompt = f"""
Dokumentendaten:
- Absender / Korrespondent: {correspondent}
- Dokumenttyp: {doc_type}
- Aktenzeichen / Referenz: {aktenzeichen or 'ohne Aktenzeichen'}
- Betrag: {betrag_str}
- Frist / Prüfziel: {target_date}
- Kontext / Zusammenfassung: {doc_summary[:1000]}

Erstelle die E-Mail im JSON-Format:
{{
  "subject": "Kurzer, aussagekräftiger Betreff mit Aktenzeichen",
  "body": "Vollständiger, professioneller E-Mail-Text inklusive Anrede und Grußformel"
}}
"""

    candidate_models = [model, "hermes-agent", "deepseek-r1:14b", "qwen:vram", "darkidol:ram"]

    for m in candidate_models:
        try:
            req_data = json.dumps({
                "model": m,
                "prompt": user_prompt,
                "system": system_prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.3}
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                raw_text = res_data.get("response", "").strip()
                if raw_text.startswith("```"):
                    import re
                    raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                    raw_text = re.sub(r"\n?```$", "", raw_text).strip()
                response_json = json.loads(raw_text)
                if response_json.get("subject") and response_json.get("body"):
                    logger.info("E-Mail erfolgreich mit Modell '%s' formuliert.", m)
                    return {
                        "subject": response_json["subject"],
                        "body": response_json["body"]
                    }
        except Exception as e:
            logger.debug("Ollama-Generierung mit Modell '%s' fehlgeschlagen: %s", m, e)

    # Fallback Template falls Ollama offline
    fallback_subject = f"Rückmeldung zu Ihrem Schreiben – Aktenzeichen {aktenzeichen or 'ohne AZ'}"
    fallback_body = f"""Sehr geehrte Damen und Herren,

vielen Dank für Ihr Schreiben bezüglich des Aktenzeichens {aktenzeichen or 'ohne AZ'}.

Ihre Nachricht liegt mir vor. Die Unterlagen werden derzeit sorgfältig geprüft und die weitere Veranlassung wird bis zum {target_date} erfolgen.

Sollten vorab Rückfragen bestehen, stehe ich Ihnen gerne zur Verfügung.

Mit freundlichen Grüßen,
{user_name}
"""
    return {
        "subject": fallback_subject,
        "body": fallback_body
    }


if __name__ == "__main__":
    init_outbox_db()
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        items = list_outbox()
        print(json.dumps(items, indent=2, ensure_ascii=False))
    else:
        print("Hermes E-Mail Outbox Handler initialisiert.")
