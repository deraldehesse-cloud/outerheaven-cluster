#!/usr/bin/env bash
# ==============================================================================
# 🦊 Host: Fox - Automatisierte Konfigurations- & Backup-Lösung
# ==============================================================================
# Sichert:
#   - ~/scripts/
#   - ~/.hermes/ (inkl. .env, config.yaml und aller Unterordner)
#   - Ausgeschlossen: ~/.hermes/cache/
#
# Zielverzeichnis:
#   - Bevorzugt:  /mnt/storage-alpha/backups/ (NFS-Share)
#   - Fallback:   ~/backups/ (lokaler Speicher falls NFS nicht erreichbar ist)
#
# Vorhaltezeit / Cleanup:
#   - Automatische Löschung von Archiven älter als 7 Tage im Zielverzeichnis
#
# Dateinamen-Schema:
#   - fox-backup-YYYY-MM-DD_HH-MM.tar.gz
#
# ------------------------------------------------------------------------------
# ⏰ CRONJOB-VORLAGE:
# ------------------------------------------------------------------------------
# Um dieses Backup täglich automatisch um 03:00 Uhr nachts auszuführen,
# trage folgende Zeile in die Benutzer-Crontab ein ('crontab -e'):
#
# 0 3 * * * /home/fox/scripts/backup-fox.sh >> /home/fox/backups/backup.log 2>&1
#
# Alternativ mit dynamischem Pfad:
# 0 3 * * * $HOME/scripts/backup-fox.sh >> $HOME/backups/backup.log 2>&1
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Farb- & Formatierungsdefinitionen
# ------------------------------------------------------------------------------
if [ -t 1 ] || [ "${FORCE_COLOR:-0}" = "1" ]; then
    GREEN="\033[92m"
    RED="\033[91m"
    YELLOW="\033[93m"
    CYAN="\033[96m"
    BLUE="\033[94m"
    BOLD="\033[1m"
    RESET="\033[0m"
else
    GREEN=""
    RED=""
    YELLOW=""
    CYAN=""
    BLUE=""
    BOLD=""
    RESET=""
fi

log_info() {
    echo -e "${CYAN}[INFO]${RESET}    $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_success() {
    echo -e "${GREEN}[OK]${RESET}      $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNUNG]${RESET} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[FEHLER]${RESET}  $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

# ------------------------------------------------------------------------------
# Parameter & Pfade
# ------------------------------------------------------------------------------
TIMESTAMP="$(date +'%Y-%m-%d_%H-%M')"
ARCHIVE_NAME="fox-backup-${TIMESTAMP}.tar.gz"

NFS_MOUNT="/mnt/storage-alpha"
NFS_TARGET_DIR="${NFS_MOUNT}/backups"
LOCAL_TARGET_DIR="${HOME}/backups"
RETENTION_DAYS=7

# ------------------------------------------------------------------------------
# Banner
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🦊 FOX AUTOMATISIERTES KONFIGURATIONS-BACKUP      ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
log_info "Host: $(hostname -s 2>/dev/null || echo 'fox') | Benutzer: ${USER:-fox}"
log_info "Zeitstempel: ${TIMESTAMP}"

# ------------------------------------------------------------------------------
# Speicherziel bestimmen (NFS mit lokalem Fallback)
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}[1/4] Prüfe Speicherziel...${RESET}"

TARGET_DIR=""
IS_FALLBACK=0

# Prüfe, ob NFS eingehängt ist und innerhalb eines Timeouts reagiert & beschreibbar ist
if (mountpoint -q "$NFS_MOUNT" 2>/dev/null || mountpoint -q "$NFS_TARGET_DIR" 2>/dev/null) && \
   timeout 4 bash -c "mkdir -p '$NFS_TARGET_DIR' 2>/dev/null && [ -w '$NFS_TARGET_DIR' ]"; then
    TARGET_DIR="$NFS_TARGET_DIR"
    log_success "NFS-Mount '${NFS_MOUNT}' ist aktiv und beschreibbar."
    log_info "Zielverzeichnis: ${TARGET_DIR}"
else
    TARGET_DIR="$LOCAL_TARGET_DIR"
    IS_FALLBACK=1
    log_warn "NFS-Mount '${NFS_MOUNT}' ist nicht eingehängt, antwortet nicht oder ist schreibgeschützt."
    log_info "Aktiviere lokalen Fallback: ${TARGET_DIR}"
    mkdir -p "$TARGET_DIR"
fi

# ------------------------------------------------------------------------------
# Quellverzeichnisse prüfen
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}[2/4] Überprüfe Quellverzeichnisse...${RESET}"

BACKUP_ITEMS=()

if [ -d "$HOME/scripts" ]; then
    BACKUP_ITEMS+=("scripts")
    log_info "Gefunden: $HOME/scripts/"
else
    log_warn "Verzeichnis $HOME/scripts/ wurde nicht gefunden!"
fi

if [ -d "$HOME/.hermes" ]; then
    BACKUP_ITEMS+=(".hermes")
    log_info "Gefunden: $HOME/.hermes/ (Cache wird ausgeschlossen)"
else
    log_warn "Verzeichnis $HOME/.hermes/ wurde nicht gefunden!"
fi

if [ ${#BACKUP_ITEMS[@]} -eq 0 ]; then
    log_error "Keine der zu sichernden Quellen ($HOME/scripts, $HOME/.hermes) existiert. Abbruch!"
    exit 1
fi

# ------------------------------------------------------------------------------
# Backup-Archiv erstellen (.tar.gz)
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}[3/4] Erstelle komprimiertes Archiv...${RESET}"
ARCHIVE_PATH="${TARGET_DIR}/${ARCHIVE_NAME}"
log_info "Archiviere Quellen nach ${ARCHIVE_PATH} ..."

# Tar-Befehl ausführen:
# -C "$HOME": Wechselt ins Benutzer-Homeverzeichnis, damit Pfade im Archiv relativ bleiben
# --exclude='.hermes/cache': Schließt den Cache-Ordner und alle darin liegenden Daten aus
set +e
tar -czf "$ARCHIVE_PATH" \
    -C "$HOME" \
    --exclude='.hermes/cache' \
    --exclude='.hermes/cache/*' \
    "${BACKUP_ITEMS[@]}"
TAR_EXIT_CODE=$?
set -e

if [ $TAR_EXIT_CODE -eq 0 ]; then
    log_success "Archivierung erfolgreich abgeschlossen."
elif [ $TAR_EXIT_CODE -eq 1 ]; then
    log_warn "Archivierung mit Warnung beendet (möglicherweise Dateien während des Lesens geändert)."
else
    log_error "Tar ist mit Fehlercode ${TAR_EXIT_CODE} fehlgeschlagen!"
    rm -f "$ARCHIVE_PATH"
    exit $TAR_EXIT_CODE
fi

if [ ! -f "$ARCHIVE_PATH" ]; then
    log_error "Archivdatei wurde nicht erstellt: ${ARCHIVE_PATH}"
    exit 1
fi

ARCHIVE_SIZE=$(ls -lh "$ARCHIVE_PATH" | awk '{print $5}')
log_success "Backup erstellt: ${ARCHIVE_NAME} (${ARCHIVE_SIZE})"

# Symlink auf latest erstellen
ln -sf "$ARCHIVE_NAME" "${TARGET_DIR}/fox-backup-latest.tar.gz" 2>/dev/null || true
log_info "Symlink aktualisiert: ${TARGET_DIR}/fox-backup-latest.tar.gz"

# ------------------------------------------------------------------------------
# Cleanup-Funktion: Backups älter als 7 Tage löschen
# ------------------------------------------------------------------------------
cleanup_old_backups() {
    local target="$1"
    local days="$2"

    echo -e "\n${BOLD}[4/4] Führe automatische Bereinigung durch (Aufbewahrung: ${days} Tage)...${RESET}"
    log_info "Suche nach Archiven älter als ${days} Tagen in: ${target}"

    if [ ! -d "$target" ]; then
        log_warn "Bereinigungsverzeichnis existiert nicht: ${target}"
        return 0
    fi

    local deleted_count=0
    while IFS= read -r -d '' old_file; do
        local file_name
        file_name="$(basename "$old_file")"
        log_info "Lösche veraltetes Backup: ${file_name}"
        rm -f "$old_file"
        deleted_count=$((deleted_count + 1))
    done < <(find "$target" -maxdepth 1 -type f -name "fox-backup-*.tar.gz" -mtime +"$days" -print0 2>/dev/null)

    if [ "$deleted_count" -gt 0 ]; then
        log_success "${deleted_count} veraltete(s) Backup-Archiv(e) gelöscht."
    else
        log_info "Keine veralteten Backups gefunden. Alles sauber."
    fi

    # Falls wir auf NFS gesichert haben, aber im lokalen Fallback noch alte Archive liegen, diese ebenfalls bereinigen
    if [ "$IS_FALLBACK" -eq 0 ] && [ -d "$LOCAL_TARGET_DIR" ] && [ "$LOCAL_TARGET_DIR" != "$target" ]; then
        local local_deleted=0
        while IFS= read -r -d '' old_local; do
            rm -f "$old_local"
            local_deleted=$((local_deleted + 1))
        done < <(find "$LOCAL_TARGET_DIR" -maxdepth 1 -type f -name "fox-backup-*.tar.gz" -mtime +"$days" -print0 2>/dev/null)
        if [ "$local_deleted" -gt 0 ]; then
            log_info "${local_deleted} veraltete(s) Archiv(e) im lokalen Fallback-Ordner (${LOCAL_TARGET_DIR}) bereinigt."
        fi
    fi
}

cleanup_old_backups "$TARGET_DIR" "$RETENTION_DAYS"

# ------------------------------------------------------------------------------
# Zusammenfassung
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${GREEN}✔ BACKUP ERFOLGREICH BEENDET!${RESET}"
echo -e "  Typ:       $([ "$IS_FALLBACK" -eq 1 ] && echo -e "${YELLOW}Lokaler Fallback${RESET}" || echo -e "${GREEN}NFS Netzlaufwerk${RESET}")"
echo -e "  Datei:     ${CYAN}${ARCHIVE_PATH}${RESET}"
echo -e "  Größe:     ${CYAN}${ARCHIVE_SIZE}${RESET}"
echo -e "  Symlink:   ${CYAN}${TARGET_DIR}/fox-backup-latest.tar.gz${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}\n"
