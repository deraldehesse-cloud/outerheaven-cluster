#!/bin/bash
set -e
SRC_DIR="/home/bigboss/Desktop/APKs/Catlord-Drop"
REMOTE_TARGET="metal-dgear:/mnt/storage-fast/rea-sandbox/workspace/catlord-dropzone/"

echo "=== Sende APKs an metal-dgear ==="
shopt -s nullglob
FILES=("$SRC_DIR"/*.apk)

if [ ${#FILES[@]} -eq 0 ]; then
    echo "Keine .apk-Dateien in $SRC_DIR vorhanden!"
    read -p "Drücke [Enter] zum Beenden..."
    exit 0
fi

scp "${FILES[@]}" "$REMOTE_TARGET"
echo "Übertragung erfolgreich abgeschlossen!"
read -p "Drücke [Enter] zum Beenden..."
