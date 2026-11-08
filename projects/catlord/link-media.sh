#!/usr/bin/env bash
# ==============================================================================
# 😼 Catlord Media Symlink Manager
# ==============================================================================
# Verknüpft das Medien-Archiv von /mnt/storage-beta mit dem Projektverzeichnis.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
TARGET_MEDIA="/mnt/storage-beta/projects/catlord-media"
LINK_PATH="${SCRIPT_DIR}/media"

echo -e "\033[1;36m[Catlord]\033[0m Prüfe Medien-Storage Anbindung..."

if [ ! -d "${TARGET_MEDIA}" ]; then
    echo -e "\033[1;33m[WARNUNG]\033[0m '${TARGET_MEDIA}' ist lokal nicht verfügbar."
    echo -e "Bitte sicherstellen, dass NFS gemountet ist (z. B. autofs oder /mnt/storage-beta Mount auf outerheaven)."
fi

ln -sfn "${TARGET_MEDIA}" "${LINK_PATH}"
echo -e "\033[1;32m[OK]\033[0m Symlink gesetzt: ${LINK_PATH} -> ${TARGET_MEDIA}"
ls -ld "${LINK_PATH}"
