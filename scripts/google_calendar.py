#!/usr/bin/env python3
"""
google_calendar.py - Google Calendar Tool via Service Account Authentication
Designed for Hermes Agent & Outer Heaven Cluster (outerheaven / fox).

Features:
- Service-Account-based authentication (no interactive browser OAuth required).
- Automatic discovery of service account credentials file across cluster nodes.
- Python API:
    list_events(time_min=None, max_results=10, calendar_id="<YOUR_GMAIL_ADDRESS>")
    add_event(summary, start_time, end_time, description="", calendar_id="<YOUR_GMAIL_ADDRESS>")
- CLI interface with formatted terminal output and optional --json mode.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

DEFAULT_CALENDAR_ID = "<YOUR_GMAIL_ADDRESS>"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

CANDIDATE_KEY_PATHS = [
    # 1. Environment variables
    lambda: os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    lambda: os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"),
    # 2. Cluster shared storage paths
    lambda: "/mnt/storage-alpha/secrets/google_service_account.json",
    lambda: "/mnt/storage-beta/secrets/google_service_account.json",
    # 3. Hermes home and user directories
    lambda: os.path.expanduser("~/.hermes/google_service_account.json"),
    lambda: os.path.expanduser("~/.hermes/secrets/google_service_account.json"),
    lambda: os.path.expanduser("~/google_service_account.json"),
]


def resolve_credentials_path(explicit_path: Optional[str] = None) -> str:
    """Resolve the service account JSON file from explicit argument, env, or known cluster paths."""
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
        "Keine lesbare Google Service-Account-Schlüsseldatei gefunden!\n"
        f"Geprüfte Pfade:\n  - " + "\n  - ".join(checked_paths) + "\n"
        "Bitte Datei unter /mnt/storage-alpha/secrets/google_service_account.json "
        "oder ~/.hermes/google_service_account.json hinterlegen."
    )


def get_calendar_service(credentials_path: Optional[str] = None):
    """Build and return an authorized Google Calendar Resource service."""
    cred_file = resolve_credentials_path(credentials_path)
    creds = service_account.Credentials.from_service_account_file(
        cred_file, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def format_iso_time(time_str: Optional[str]) -> str:
    """Ensure a given date/time string is formatted as ISO 8601 with timezone."""
    if not time_str:
        return datetime.now(timezone.utc).isoformat()

    time_str = time_str.strip()
    # If date only (e.g. 2026-09-11), treat as start of day in UTC
    if len(time_str) == 10 and time_str.count("-") == 2:
        return f"{time_str}T00:00:00Z"

    # If datetime without timezone, append local offset or Z
    if "T" in time_str and not (time_str.endswith("Z") or "+" in time_str[10:] or "-" in time_str[10:]):
        local_tz = datetime.now().astimezone().tzinfo
        try:
            dt = datetime.fromisoformat(time_str).replace(tzinfo=local_tz)
            return dt.isoformat()
        except Exception:
            return f"{time_str}Z"

    return time_str


def list_events(
    time_min: Optional[str] = None,
    max_results: int = 10,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    credentials_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    List upcoming events from the specified calendar.

    :param time_min: RFC3339 timestamp (e.g. '2026-09-10T00:00:00Z'). Default: current UTC time.
    :param max_results: Maximum number of events to return.
    :param calendar_id: Google Calendar ID (default: <YOUR_GMAIL_ADDRESS>).
    :param credentials_path: Optional path to service account json.
    :return: List of event dicts.
    """
    service = get_calendar_service(credentials_path)
    time_min_formatted = format_iso_time(time_min)

    try:
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min_formatted,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return events_result.get("items", [])
    except HttpError as err:
        raise RuntimeError(f"Google Calendar API Fehler: {err}") from err


def add_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    calendar_id: str = DEFAULT_CALENDAR_ID,
    credentials_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Add an event to the specified calendar.

    :param summary: Event title/summary.
    :param start_time: Start datetime (e.g. '2026-09-11T10:00:00+02:00' or '2026-09-11').
    :param end_time: End datetime (e.g. '2026-09-11T11:00:00+02:00' or '2026-09-11').
    :param description: Optional description / notes.
    :param calendar_id: Google Calendar ID (default: <YOUR_GMAIL_ADDRESS>).
    :param credentials_path: Optional path to service account json.
    :return: Created event dict.
    """
    service = get_calendar_service(credentials_path)

    event_body: Dict[str, Any] = {
        "summary": summary,
    }
    if description:
        event_body["description"] = description

    # All-day event check (YYYY-MM-DD)
    if len(start_time) == 10 and "T" not in start_time:
        event_body["start"] = {"date": start_time}
        event_body["end"] = {"date": end_time}
    else:
        start_iso = format_iso_time(start_time)
        end_iso = format_iso_time(end_time)
        event_body["start"] = {"dateTime": start_iso}
        event_body["end"] = {"dateTime": end_iso}

    try:
        created_event = (
            service.events()
            .insert(calendarId=calendar_id, body=event_body)
            .execute()
        )
        return created_event
    except HttpError as err:
        raise RuntimeError(f"Google Calendar API Fehler beim Erstellen: {err}") from err


# ----------------------------------------------------------------------
# CLI Interface
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Google Calendar CLI & Hermes Tool (Service Account Auth)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--credentials",
        "-c",
        help="Pfad zur Google Service Account JSON-Schlüsseldatei",
        default=None,
    )
    parser.add_argument(
        "--calendar-id",
        help=f"Google Kalender ID (Standard: {DEFAULT_CALENDAR_ID})",
        default=DEFAULT_CALENDAR_ID,
    )

    subparsers = parser.add_subparsers(dest="command", help="Verfügbare Befehle")

    # Command: list
    list_parser = subparsers.add_parser("list", help="Anstehende Termine auflisten")
    list_parser.add_argument(
        "--time-min",
        help="Ab wann Termine gelistet werden sollen (ISO-8601, z.B. 2026-09-10T00:00:00Z)",
        default=None,
    )
    list_parser.add_argument(
        "--max-results",
        type=int,
        help="Maximale Anzahl an Terminen (Standard: 10)",
        default=10,
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Rohdaten im JSON-Format ausgeben",
    )

    # Command: add
    add_parser = subparsers.add_parser("add", help="Neuen Termin eintragen")
    add_parser.add_argument("--summary", required=True, help="Titel/Zusammenfassung des Termins")
    add_parser.add_argument(
        "--start",
        required=True,
        help="Startzeit (z.B. '2026-09-11T14:00:00' oder '2026-09-11')",
    )
    add_parser.add_argument(
        "--end",
        required=True,
        help="Endzeit (z.B. '2026-09-11T15:00:00' oder '2026-09-11')",
    )
    add_parser.add_argument("--description", "-d", default="", help="Beschreibung / Notizen")
    add_parser.add_argument("--json", action="store_true", help="Erstelltes Event als JSON ausgeben")

    # Command: check
    subparsers.add_parser("check", help="Schlüsseldatei & API-Konnektivität prüfen")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "check":
            key_path = resolve_credentials_path(args.credentials)
            print(f"🔑 Schlüsseldatei gefunden: {key_path}")
            print(f"📅 Teste Verbindung zu Kalender '{args.calendar_id}'...")
            events = list_events(
                time_min=None,
                max_results=1,
                calendar_id=args.calendar_id,
                credentials_path=args.credentials,
            )
            print(f"✅ Verbindung ERFOLGREICH! Zugriff auf Kalender '{args.calendar_id}' funktioniert.")
            print(f"   Aktuelle anstehende Termine gefunden: {len(events)}")

        elif args.command == "list":
            events = list_events(
                time_min=args.time_min,
                max_results=args.max_results,
                calendar_id=args.calendar_id,
                credentials_path=args.credentials,
            )

            if args.json:
                print(json.dumps(events, indent=2, ensure_ascii=False))
                return

            if not events:
                print(f"📅 Keine anstehenden Termine im Kalender '{args.calendar_id}' gefunden.")
                return

            print(f"\n📅 Nächste Termine für '{args.calendar_id}' ({len(events)} gefunden):")
            print("=" * 70)
            for idx, item in enumerate(events, 1):
                start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
                end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
                summary = item.get("summary", "(Kein Titel)")
                desc = item.get("description", "")
                loc = item.get("location", "")

                print(f"[{idx}] {summary}")
                print(f"    Zeitraum: {start}  bis  {end}")
                if loc:
                    print(f"    Ort:      {loc}")
                if desc:
                    print(f"    Notiz:    {desc.strip()}")
                print("-" * 70)

        elif args.command == "add":
            event = add_event(
                summary=args.summary,
                start_time=args.start,
                end_time=args.end,
                description=args.description,
                calendar_id=args.calendar_id,
                credentials_path=args.credentials,
            )

            if args.json:
                print(json.dumps(event, indent=2, ensure_ascii=False))
                return

            event_link = event.get("htmlLink", "")
            print(f"✅ Termin erfolgreich angelegt!")
            print(f"   Titel:    {event.get('summary')}")
            print(f"   Start:    {event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')}")
            print(f"   Ende:     {event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')}")
            if event_link:
                print(f"   Link:     {event_link}")

    except Exception as e:
        print(f"❌ FEHLER: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
