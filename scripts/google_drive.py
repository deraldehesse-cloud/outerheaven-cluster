#!/usr/bin/env python3
"""
google_drive.py - Google Drive Tool via Service Account Authentication
Designed for Hermes Agent & Outer Heaven Cluster (outerheaven / fox).

Features:
- Service-Account-based authentication (no interactive browser OAuth required).
- Automatic discovery of service account credentials file across cluster nodes.
- Python API:
    list_files(page_size=20, query=None, folder_id=None)
    search_files(name_query, page_size=20)
    download_file(file_id, destination_path)
    upload_file(local_path, name=None, parent_folder_id=None)
- CLI interface with formatted terminal output and optional --json mode.
"""

import os
import sys
import io
import json
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]

CANDIDATE_KEY_PATHS = [
    lambda: os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    lambda: os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"),
    lambda: "/mnt/storage-alpha/secrets/google_service_account.json",
    lambda: "/mnt/storage-beta/secrets/google_service_account.json",
    lambda: os.path.expanduser("~/.hermes/google_service_account.json"),
    lambda: os.path.expanduser("~/.hermes/secrets/google_service_account.json"),
    lambda: os.path.expanduser("~/google_service_account.json"),
]


def resolve_credentials_path(explicit_path: Optional[str] = None) -> str:
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        if p.is_file():
            return str(p)
        raise FileNotFoundError(f"Angegebene Schlüsseldatei nicht gefunden: {explicit_path}")

    checked_paths = []
    for getter in CANDIDATE_KEY_PATHS:
        raw = getter()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        checked_paths.append(str(p))
        if p.is_file() and os.access(p, os.R_OK):
            return str(p)

    raise FileNotFoundError(
        "Keine gültige Google Service Account Schlüsseldatei gefunden.\n"
        f"Geprüfte Pfade:\n  " + "\n  ".join(checked_paths)
    )


def get_drive_service(credentials_path: Optional[str] = None):
    key_file = resolve_credentials_path(credentials_path)
    creds = service_account.Credentials.from_service_account_file(
        key_file,
        scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def list_files(
    page_size: int = 20,
    query: Optional[str] = None,
    folder_id: Optional[str] = None,
    credentials_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    service = get_drive_service(credentials_path)
    q_parts = ["trashed = false"]
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    if query:
        q_parts.append(query)
    q_string = " and ".join(q_parts)
    results = service.files().list(
        q=q_string,
        pageSize=page_size,
        fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, webViewLink)",
        orderBy="modifiedTime desc"
    ).execute()
    return results.get("files", [])


def search_files(
    name_query: str,
    page_size: int = 20,
    credentials_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    safe_name = name_query.replace("'", "\\'")
    query = f"name contains '{safe_name}'"
    return list_files(page_size=page_size, query=query, credentials_path=credentials_path)


def download_file(
    file_id: str,
    destination_path: str,
    credentials_path: Optional[str] = None
) -> str:
    service = get_drive_service(credentials_path)
    dest = Path(destination_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(str(dest), "wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return str(dest)


def upload_file(
    local_path: str,
    name: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
    credentials_path: Optional[str] = None
) -> Dict[str, Any]:
    src = Path(local_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Lokale Datei existiert nicht: {local_path}")
    service = get_drive_service(credentials_path)
    file_metadata: Dict[str, Any] = {
        "name": name or src.name
    }
    if parent_folder_id:
        file_metadata["parents"] = [parent_folder_id]
    media = MediaFileUpload(str(src), resumable=True)
    file_created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, size, mimeType, webViewLink"
    ).execute()
    return file_created


def check_connection(credentials_path: Optional[str] = None) -> bool:
    try:
        key_file = resolve_credentials_path(credentials_path)
        print(f"🔑 Schlüsseldatei gefunden: {key_file}")
        service = get_drive_service(credentials_path)
        res = service.files().list(pageSize=5, fields="files(id, name)").execute()
        files = res.get("files", [])
        print(f"✅ Verbindung ERFOLGREICH! Zugriff auf Google Drive funktioniert.")
        print(f"   Sichtbare Dateien gefunden: {len(files)}")
        for f in files:
            print(f"   - {f.get('name')} (ID: {f.get('id')})")
        return True
    except Exception as e:
        print(f"❌ Fehler bei Verbindung zu Google Drive: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Google Drive Tool via Service Account")
    subparsers = parser.add_subparsers(dest="command", help="Befehl")
    subparsers.add_parser("check", help="Prüft die Verbindung zu Google Drive")
    
    list_p = subparsers.add_parser("list", help="Dateien auflisten")
    list_p.add_argument("--max-results", "-n", type=int, default=20, help="Maximale Trefferanzahl")
    list_p.add_argument("--folder-id", type=str, default=None, help="ID des übergeordneten Ordners")
    list_p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    
    search_p = subparsers.add_parser("search", help="Nach Dateinamen suchen")
    search_p.add_argument("query", type=str, help="Suchbegriff im Dateinamen")
    search_p.add_argument("--max-results", "-n", type=int, default=20, help="Maximale Trefferanzahl")
    search_p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    
    dl_p = subparsers.add_parser("download", help="Datei herunterladen")
    dl_p.add_argument("file_id", type=str, help="Google Drive Datei-ID")
    dl_p.add_argument("--output", "-o", type=str, required=True, help="Lokaler Zielpfad")
    
    up_p = subparsers.add_parser("upload", help="Datei hochladen")
    up_p.add_argument("file_path", type=str, help="Lokaler Dateipfad")
    up_p.add_argument("--name", type=str, default=None, help="Name im Drive")
    up_p.add_argument("--folder-id", type=str, default=None, help="Zielordner ID")
    up_p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    
    args = parser.parse_args()
    if args.command == "check" or not args.command:
        success = check_connection()
        sys.exit(0 if success else 1)
    elif args.command == "list":
        files = list_files(page_size=args.max_results, folder_id=args.folder_id)
        if args.json:
            print(json.dumps(files, indent=2, ensure_ascii=False))
        else:
            if not files:
                print("ℹ️ Keine Dateien gefunden.")
            else:
                print(f"{'NAME':<40} {'TYP':<25} {'ID':<35}")
                print("-" * 105)
                for f in files:
                    name = f.get("name", "")[:38]
                    mime = f.get("mimeType", "").replace("application/vnd.google-apps.", "g-")[:23]
                    fid = f.get("id", "")
                    print(f"{name:<40} {mime:<25} {fid:<35}")
    elif args.command == "search":
        files = search_files(name_query=args.query, page_size=args.max_results)
        if args.json:
            print(json.dumps(files, indent=2, ensure_ascii=False))
        else:
            if not files:
                print(f"ℹ️ Keine Dateien für '{args.query}' gefunden.")
            else:
                print(f"{'NAME':<40} {'TYP':<25} {'ID':<35}")
                print("-" * 105)
                for f in files:
                    name = f.get("name", "")[:38]
                    mime = f.get("mimeType", "").replace("application/vnd.google-apps.", "g-")[:23]
                    fid = f.get("id", "")
                    print(f"{name:<40} {mime:<25} {fid:<35}")
    elif args.command == "download":
        dest = download_file(args.file_id, args.output)
        print(f"✅ Datei erfolgreich heruntergeladen nach: {dest}")
    elif args.command == "upload":
        created = upload_file(args.file_path, name=args.name, parent_folder_id=args.folder_id)
        if args.json:
            print(json.dumps(created, indent=2, ensure_ascii=False))
        else:
            print(f"✅ Datei erfolgreich hochgeladen: {created.get('name')} (ID: {created.get('id')})")

if __name__ == "__main__":
    main()
