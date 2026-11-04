---
name: google-calendar
description: "Google Calendar integration via Service Account authentication. List upcoming events and add new events to deraldehesse@gmail.com without browser OAuth."
version: 1.0.0
author: Outer Heaven Cluster
license: MIT
metadata:
  hermes:
    tags: [Google, Calendar, ServiceAccount, Events, Termine]
---

# Google Calendar (Service Account)

Dieses Tool ermöglicht dem Hermes-Agenten den direkten Zugriff auf den Google Kalender (`deraldehesse@gmail.com`) via Google Service Account Authentifizierung.
Es wird kein interaktiver Browser-OAuth benötigt.

## Nutzung via CLI / Terminal-Tool

Das Python-Skript liegt unter `~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py` (oder im Pfad als `google_calendar`).
Ausführung immer im Hermes Virtualenv:

```bash
# Nächste Termine auflisten
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py list

# Nächste 5 Termine mit JSON-Ausgabe
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py list --max-results 5 --json

# Neuen Termin anlegen (mit Datum & Uhrzeit)
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py add --summary "Team Meeting" --start "2026-09-11T10:00:00" --end "2026-09-11T11:00:00" --description "Besprechung der Cluster-Wartung"

# Ganztägigen Termin anlegen
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py add --summary "Urlaub" --start "2026-09-15" --end "2026-09-16"

# Verbindung & Kalender-Zugriff prüfen
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py check
```

## Nutzung via Python-Modul

```python
import sys
sys.path.append("/home/outerheaven/.hermes/skills/productivity/google-calendar/scripts") # bzw. /home/fox/...
from google_calendar import list_events, add_event

# Termine abrufen
events = list_events(max_results=5)

# Termin eintragen
event = add_event(
    summary="Projekt-Review",
    start_time="2026-09-12T14:00:00+02:00",
    end_time="2026-09-12T15:30:00+02:00",
    description="Review mit Big Boss"
)
```
