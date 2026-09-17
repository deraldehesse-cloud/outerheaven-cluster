#!/usr/bin/env python3
"""
fox_doc_ingest.py - Fox Document Ingest Routine & Service-Health Guard
=====================================================================
Autonomes Hintergrundskript für den Worker "fox":
1. Service Account Verifikation & Health Guard (Drive, Calendar, Gmail).
   - Prominente Warnung ("Meckern/Rumheulen") bei fehlendem Kalender- oder Gmail-Zugriff.
   - Statusbericht nach ~/.config/google/status.json.
2. Rechnungs- & Dokumenten-Ingest (Google Drive -> NFS):
   - Überwacht den Ingest-Ordner "Posteingang_Scans".
   - Lädt neue PDFs / Scans nach /mnt/storage-beta/rechnungseingang/ herunter.
   - Benennt verarbeitete Dateien in Google Drive mit dem Präfix [PROCESSED]_ um.
"""

import os
import sys
import time
import json
import logging
import argparse
import signal
import socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Standard-Timeout für Google API Anfragen
socket.setdefaulttimeout(40.0)

# Pfadkonfiguration
SERVICE_ACCOUNT_FILE = Path(os.path.expanduser("~/.config/google/service_account.json"))
STATUS_FILE = Path(os.path.expanduser("~/.config/google/status.json"))
NFS_TARGET_DIR = Path("/mnt/storage-beta/rechnungseingang")
INGEST_FOLDER_NAME = "Posteingang_Scans"
PROCESSED_PREFIX = "[PROCESSED]_"
DEFAULT_POLL_INTERVAL = int(os.environ.get("INGEST_POLL_INTERVAL", 60))

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("fox_doc_ingest")

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from googleapiclient.errors import HttpError
except ImportError as err:
    logger.critical(f"Fehlende Google-Bibliotheken: {err}")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly"
]

running = True


def signal_handler(signum, frame):
    global running
    sig_name = signal.Signals(signum).name
    logger.info(f"Signal {sig_name} empfangen. Fahre sauber herunter...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def get_credentials() -> Optional[service_account.Credentials]:
    """Lädt die Service-Account-Anmeldedaten."""
    if not SERVICE_ACCOUNT_FILE.is_file():
        logger.error(f"Service-Account-Datei nicht gefunden: {SERVICE_ACCOUNT_FILE}")
        return None
    try:
        return service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_FILE),
            scopes=SCOPES
        )
    except Exception as e:
        logger.error(f"Fehler beim Laden des Service-Accounts: {e}")
        return None


def verify_drive_service(creds: service_account.Credentials) -> Dict[str, Any]:
    """
    Prüft die Erreichbarkeit der Google Drive API und des Ingest-Ordners 'Posteingang_Scans'.
    Status: PFLICHT.
    """
    res = {
        "status": "UNKNOWN",
        "reachable": False,
        "folder_id": None,
        "folder_name": INGEST_FOLDER_NAME,
        "message": ""
    }
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        # Suche nach Ordner
        query = (
            f"name = '{INGEST_FOLDER_NAME}' and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )
        response = service.files().list(
            q=query,
            fields="files(id, name, parents)",
            pageSize=10
        ).execute()

        folders = response.get("files", [])
        if folders:
            folder = folders[0]
            res["status"] = "OK"
            res["reachable"] = True
            res["folder_id"] = folder["id"]
            res["message"] = f"Ingest-Ordner '{INGEST_FOLDER_NAME}' erfolgreich verifiziert (ID: {folder['id']})."
            logger.info(f"[DRIVE] ✅ {res['message']}")
        else:
            # Versuche in Agenten-Drive zu suchen oder zu erstellen falls nicht existent
            agenten_drive_query = "name = 'Agenten-Drive' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            ad_res = service.files().list(q=agenten_drive_query, fields="files(id, name)", pageSize=1).execute()
            ad_folders = ad_res.get("files", [])
            parent_id = ad_folders[0]["id"] if ad_folders else None

            create_meta = {
                "name": INGEST_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder"
            }
            if parent_id:
                create_meta["parents"] = [parent_id]

            new_folder = service.files().create(body=create_meta, fields="id, name").execute()
            res["status"] = "OK"
            res["reachable"] = True
            res["folder_id"] = new_folder["id"]
            res["message"] = f"Ingest-Ordner '{INGEST_FOLDER_NAME}' neu angelegt (ID: {new_folder['id']})."
            logger.info(f"[DRIVE] 📁 {res['message']}")

    except Exception as e:
        res["status"] = "ERROR"
        res["reachable"] = False
        res["message"] = f"Google Drive API Fehler: {e}"
        logger.error(f"[DRIVE] ❌ {res['message']}")

    return res


def verify_calendar_service(creds: service_account.Credentials) -> Dict[str, Any]:
    """
    Prüft Lese-/Schreibzugriff auf Google Calendar.
    Schlägt fehl oder meckert, falls kein Benutzer-Kalender freigegeben ist.
    """
    res = {
        "status": "DISCONNECTED",
        "connected": False,
        "calendars_count": 0,
        "message": ""
    }
    sa_email = getattr(creds, "service_account_email", "Service-Account")
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cals_res = service.calendarList().list().execute()
        items = cals_res.get("items", [])
        
        # Prüfen auf freigegebene Benutzer-Kalender (der eigene leere SA-Account gilt nicht als verknüpfter Nutzer-Kalender)
        human_cals = [c for c in items if c.get("id") != sa_email]
        if human_cals:
            res["status"] = "OK"
            res["connected"] = True
            res["calendars_count"] = len(human_cals)
            res["message"] = f"{len(human_cals)} Kalender freigegeben und verbunden."
            logger.info(f"[CALENDAR] ✅ {res['message']}")
        else:
            res["status"] = "DISCONNECTED"
            res["connected"] = False
            res["message"] = f"Google Calendar nicht verbunden! Bitte Kalender für Service-Account freigeben ({sa_email})."
            print_warning_banner(f"[WARN] Google Calendar nicht verbunden! Bitte Kalender für Service-Account freigeben.\n      Account: {sa_email}")
    except Exception as e:
        res["status"] = "ERROR"
        res["connected"] = False
        res["message"] = f"Google Calendar Fehler: {e}"
        print_warning_banner(f"[WARN] Google Calendar nicht verbunden! Bitte Kalender für Service-Account freigeben.\n      Account: {sa_email} | Fehler: {e}")

    return res


def verify_gmail_service(creds: service_account.Credentials) -> Dict[str, Any]:
    """
    Prüft Mail-Abruf via Gmail / Workspace API.
    Loggt prominente Warnung, falls Mail-Scope nicht verknüpft ist.
    """
    res = {
        "status": "DISCONNECTED",
        "connected": False,
        "message": ""
    }
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        service.users().getProfile(userId="me").execute()
        res["status"] = "OK"
        res["connected"] = True
        res["message"] = "Gmail API Zugriff erfolgreich verifiziert."
        logger.info(f"[GMAIL] ✅ {res['message']}")
    except HttpError:
        res["status"] = "DISCONNECTED"
        res["connected"] = False
        res["message"] = "Gmail/Mail-Scope nicht verknüpft!"
        print_warning_banner("[WARN] Gmail/Mail-Scope nicht verknüpft!")
    except Exception as e:
        res["status"] = "ERROR"
        res["connected"] = False
        res["message"] = f"Gmail API Fehler: {e}"
        print_warning_banner(f"[WARN] Gmail/Mail-Scope nicht verknüpft! Details: {e}")

    return res


def print_warning_banner(msg: str):
    """Gibt eine unübersehbare Warnung im Log aus."""
    banner = [
        "",
        "!" * 78,
        f"  ⚠️  {msg}",
        "!" * 78,
        ""
    ]
    logger.warning("\n".join(banner))


def update_status_file(sa_email: str, drive_res: Dict[str, Any], cal_res: Dict[str, Any], gmail_res: Dict[str, Any], ingest_stats: Optional[Dict[str, Any]] = None):
    """Schreibt den aggregierten Status in ~/.config/google/status.json."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Ermittlung des Gesamtzustands
    if drive_res.get("status") == "OK":
        if cal_res.get("status") == "OK" and gmail_res.get("status") == "OK":
            overall = "HEALTHY"
        else:
            overall = "DEGRADED"  # Drive läuft, aber optionale Dienste meckern
    else:
        overall = "CRITICAL"

    data = {
        "last_check": datetime.now(timezone.utc).isoformat(),
        "service_account_email": sa_email,
        "overall_health": overall,
        "services": {
            "drive": drive_res,
            "calendar": cal_res,
            "gmail": gmail_res
        },
        "ingest": ingest_stats or {
            "target_dir": str(NFS_TARGET_DIR),
            "last_processed_count": 0,
            "status": "IDLE"
        }
    }

    tmp_file = STATUS_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_file.replace(STATUS_FILE)
    except Exception as e:
        logger.error(f"Fehler beim Schreiben der Statusdatei {STATUS_FILE}: {e}")


def ingest_documents_from_drive(creds: service_account.Credentials, folder_id: str) -> Dict[str, Any]:
    """
    Liest neue Scans/PDFs aus Google Drive ('Posteingang_Scans') und überträgt sie nach NFS.
    Benennt die Dateien in Drive anschließend mit [PROCESSED]_ um.
    """
    stats = {
        "target_dir": str(NFS_TARGET_DIR),
        "last_processed_count": 0,
        "processed_files": [],
        "status": "OK"
    }

    # 1. NFS Mount / Verzeichnisprüfung
    if not NFS_TARGET_DIR.exists():
        try:
            NFS_TARGET_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[INGEST] ❌ NFS-Zielverzeichnis {NFS_TARGET_DIR} existiert nicht und kann nicht erstellt werden: {e}")
            stats["status"] = "NFS_UNAVAILABLE"
            return stats

    if not os.access(NFS_TARGET_DIR, os.W_OK):
        logger.error(f"[INGEST] ❌ Keine Schreibrechte auf {NFS_TARGET_DIR}!")
        stats["status"] = "NFS_WRITE_ERROR"
        return stats

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        # Alle Dateien im Ordner auflisten (keine Ordner, nicht im Papierkorb)
        query = (
            f"'{folder_id}' in parents and "
            f"mimeType != 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )
        response = service.files().list(
            q=query,
            fields="files(id, name, mimeType, size)",
            pageSize=50
        ).execute()

        files = response.get("files", [])
        new_files = [f for f in files if not f["name"].startswith(PROCESSED_PREFIX)]

        if not new_files:
            logger.info(f"[INGEST] Keine neuen Dokumente in '{INGEST_FOLDER_NAME}'.")
            return stats

        logger.info(f"[INGEST] 📥 {len(new_files)} neue(s) Dokument(e) in '{INGEST_FOLDER_NAME}' gefunden.")

        for f_info in new_files:
            f_id = f_info["id"]
            f_name = f_info["name"]

            # Zieldateinamen berechnen (Kollisionsschutz)
            clean_name = f_name.replace("/", "_").replace("\\", "_")
            dest_file = NFS_TARGET_DIR / clean_name

            if dest_file.exists():
                # Zeitstempel anhängen um Überschreiben zu verhindern
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = dest_file.stem
                suffix = dest_file.suffix
                dest_file = NFS_TARGET_DIR / f"{stem}_{ts}{suffix}"

            part_file = dest_file.with_suffix(dest_file.suffix + ".part")
            logger.info(f"[INGEST] Starte Download: '{f_name}' -> '{dest_file.name}'")

            # Download
            request = service.files().get_media(fileId=f_id)
            with open(part_file, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            # Atomar umbenennen
            part_file.replace(dest_file)
            file_size = dest_file.stat().st_size
            logger.info(f"[INGEST] ✅ Gespeichert: '{dest_file.name}' ({file_size} Bytes)")

            # In Google Drive umbenennen ([PROCESSED]_)
            new_drive_name = f"{PROCESSED_PREFIX}{f_name}"
            try:
                service.files().update(
                    fileId=f_id,
                    body={"name": new_drive_name},
                    fields="id, name"
                ).execute()
                logger.info(f"[INGEST] 🏷️ In Google Drive umbenannt: '{f_name}' -> '{new_drive_name}'")
            except Exception as ue:
                logger.warning(f"[INGEST] ⚠️ Konnte Datei in Drive nicht umbenennen: {ue}")

            stats["last_processed_count"] += 1
            stats["processed_files"].append({
                "original_name": f_name,
                "local_file": dest_file.name,
                "size_bytes": file_size,
                "ingested_at": datetime.now(timezone.utc).isoformat()
            })

    except Exception as e:
        logger.error(f"[INGEST] ❌ Fehler während des Dokumenten-Ingests: {e}")
        stats["status"] = f"ERROR: {e}"

    return stats


def run_cycle(creds: service_account.Credentials) -> None:
    """Führt einen einzelnen Überprüfungs- und Ingest-Zyklus aus."""
    sa_email = getattr(creds, "service_account_email", "Service-Account")
    logger.info("=" * 60)
    logger.info(f"🔄 Starte Überprüfungs-Zyklus (Service-Account: {sa_email})")
    logger.info("=" * 60)

    # 1. Google Drive API (PFLICHT)
    drive_res = verify_drive_service(creds)

    # 2. Google Calendar API (Lese-/Schreibtest + Warnung)
    cal_res = verify_calendar_service(creds)

    # 3. Gmail API (Mail-Abruf + Warnung)
    gmail_res = verify_gmail_service(creds)

    # 4. Ingest ausführen, falls Drive verfügbar ist
    ingest_stats = None
    if drive_res.get("reachable") and drive_res.get("folder_id"):
        ingest_stats = ingest_documents_from_drive(creds, drive_res["folder_id"])

    # 5. Statusdatei schreiben
    update_status_file(sa_email, drive_res, cal_res, gmail_res, ingest_stats)
    logger.info(f"💾 Statusbericht aktualisiert in: {STATUS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Fox Document Ingest Routine & Service-Health Guard")
    parser.add_argument("--once", action="store_true", help="Führt einen einzelnen Zyklus aus und beendet sich.")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL, help="Polling-Intervall in Sekunden (Default: 60).")
    args = parser.parse_args()

    logger.info("🦊 Fox Document Ingest & Service-Health Guard wird initialisiert...")

    creds = get_credentials()
    if not creds:
        logger.critical("Konnte Anmeldedaten nicht laden. Breche ab.")
        sys.exit(1)

    sa_email = getattr(creds, "service_account_email", "Service-Account")
    logger.info(f"Service Account geladen: {sa_email}")

    if args.once:
        run_cycle(creds)
        logger.info("Einmaliger Zyklus abgeschlossen (--once).")
        return

    logger.info(f"Starte Daemon-Schleife (Intervall: {args.interval}s)...")
    while running:
        try:
            run_cycle(creds)
        except Exception as e:
            logger.error(f"Unerwarteter Fehler im Zyklus: {e}")

        # Schlafphase mit Unterbrechbarkeit
        for _ in range(args.interval):
            if not running:
                break
            time.sleep(1)

    logger.info("🛑 Fox Document Ingest Daemon beendet.")


if __name__ == "__main__":
    main()
