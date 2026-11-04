---
name: google-drive
description: "Google Drive integration via local Rclone mount (~/gdrive/Agenten-Drive) and Python Service Account CLI. Access, search, read, download and upload files on Big Boss Google Drive without interactive browser OAuth."
version: 1.0.0
author: Outer Heaven Cluster
license: MIT
metadata:
  hermes:
    tags: [Google, Drive, Rclone, Cloud, Storage, ServiceAccount, Dateiverwaltung]
---

# Google Drive (Outer Heaven / Fox Integration)

Der Zugriff auf das Google Drive von Big Boss ist auf Station `fox` über zwei Wege nahtlos verfügbar:

## Weg 1: Direktes Dateisystem (Rclone Mount - Empfohlen & Standard)

Google Drive ist als lokaler Ordner gemountet:
- **Hauptpfad**: `/home/fox/gdrive/Agenten-Drive/` (bzw. `~/gdrive/Agenten-Drive/`)
- **Symlinks**: `~/GoogleDrive/` und `~/Agenten-Drive/`

Du kannst ganz normal mit Standard-Dateitools darauf zugreifen:
- Verzeichnis auflisten: `ls -la ~/gdrive/Agenten-Drive/`
- Projekte durchsuchen: `find ~/gdrive/Agenten-Drive/ -maxdepth 3`
- Dateien lesen: `cat ~/gdrive/Agenten-Drive/...`
- Dateien schreiben/kopieren: `cp neue_datei.txt ~/gdrive/Agenten-Drive/projekt-catlord/`

Der Mount läuft als automatischer Systemd-User-Service (`rclone-gdrive.service`) im Hintergrund und wird bei Systemstart automatisch bereitgestellt.

## Weg 2: CLI / Terminal-Tool (`google_drive.py`)

Alternativ steht das Python-Tool mit direkter Google Drive API v3 (Service Account) zur Verfügung:

```bash
# Dateien im Drive auflisten (die 20 neuesten)
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-drive/scripts/google_drive.py list

# Nach Dateien nach Namen suchen
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-drive/scripts/google_drive.py search "catlord"

# Datei herunterladen
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-drive/scripts/google_drive.py download <FILE_ID> /pfad/zum/ziel

# Datei ins Drive hochladen
~/.hermes/.venv/bin/python ~/.hermes/skills/productivity/google-drive/scripts/google_drive.py upload /pfad/zur/lokalen/datei.pdf --name "Bericht.pdf"
```
