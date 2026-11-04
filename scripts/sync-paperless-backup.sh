#!/usr/bin/env bash
# ==============================================================================
# 📄 Automatisierter Paperless-ngx Backup-Sync (Fox ➔ Der Alde Hesse)
# ==============================================================================
# Quell-Host:  fox (100.93.184.87)
# Ziel-Host:   deraldehesse (100.127.247.117, User: basti)
# Datenpfad:   /mnt/fox-assets/paperless/
# Stackpfad:   /home/fox/paperless-custom/
# Zielpfad:    /mnt/storage_sda1/cluster-workspace/paperless/backup/
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Konfiguration & Pfade
# ------------------------------------------------------------------------------
SOURCE_ASSETS="/mnt/fox-assets/paperless"
SOURCE_COMPOSE="/home/fox/paperless-custom"

REMOTE_HOST="deraldehesse"
REMOTE_BASE_DIR="/mnt/storage_sda1/cluster-workspace/paperless/backup"
REMOTE_LATEST_DIR="${REMOTE_BASE_DIR}/latest"
REMOTE_DUMPS_DIR="${REMOTE_BASE_DIR}/db_dumps"

LOG_FILE="/home/fox/scripts/paperless-backup.log"
LOCK_FILE="/tmp/paperless-backup.lock"
RETENTION_DAYS=14

TIMESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
TEMP_DUMP="/tmp/paperless_db_${TIMESTAMP}.sql.gz"

# ------------------------------------------------------------------------------
# Formatierung & Logging
# ------------------------------------------------------------------------------
if [ -t 1 ] || [ "${FORCE_COLOR:-0}" = "1" ]; then
    GREEN="\033[92m"
    RED="\033[91m"
    YELLOW="\033[93m"
    CYAN="\033[96m"
    BOLD="\033[1m"
    RESET="\033[0m"
else
    GREEN=""
    RED=""
    YELLOW=""
    CYAN=""
    BOLD=""
    RESET=""
fi

log() {
    local level="$1"
    local color="$2"
    local msg="$3"
    local formatted="$(date '+%Y-%m-%d %H:%M:%S') [${level}] ${msg}"
    echo -e "${color}${formatted}${RESET}"
    echo "${formatted}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_info() { log "INFO" "${CYAN}" "$1"; }
log_success() { log "OK" "${GREEN}" "$1"; }
log_warn() { log "WARN" "${YELLOW}" "$1"; }
log_error() { log "ERROR" "${RED}" "$1"; }

cleanup() {
    if [ -f "${TEMP_DUMP}" ]; then
        rm -f "${TEMP_DUMP}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ------------------------------------------------------------------------------
# Lockfile-Mechanismus (Verhindert parallele Ausführung)
# ------------------------------------------------------------------------------
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
    log_warn "Ein Backup-Prozess läuft bereits (Lock ${LOCK_FILE} aktiv). Breche ab."
    exit 0
fi

# ------------------------------------------------------------------------------
# Banner
# ------------------------------------------------------------------------------
log_info "======================================================"
log_info "  📄 PAPERLESS-NGX BACKUP-SYNC: FOX ➔ DERALDEHESSE   "
log_info "======================================================"
log_info "Zeitstempel: ${TIMESTAMP}"

# ------------------------------------------------------------------------------
# Pre-Flight Checks
# ------------------------------------------------------------------------------
log_info "[1/5] Prüfe Erreichbarkeit von Ziel-Host '${REMOTE_HOST}'..."
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_HOST}" true 2>/dev/null; then
    log_error "Ziel-Host '${REMOTE_HOST}' nicht per SSH erreichbar! Ist Tailscale aktiv? Abbruch."
    exit 1
fi
log_success "SSH-Verbindung zu '${REMOTE_HOST}' erfolgreich validiert."

log_info "[2/5] Prüfe Quellverzeichnisse und Docker-Container..."
if [ ! -d "${SOURCE_ASSETS}" ]; then
    log_error "Quellverzeichnis ${SOURCE_ASSETS} existiert nicht!"
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^paperless-db-1$"; then
    log_error "PostgreSQL-Container 'paperless-db-1' läuft nicht!"
    exit 1
fi
log_success "Quellen und Datenbank-Container sind bereit."

# Zielverzeichnisse auf dem NAS sicherstellen
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_LATEST_DIR}/assets' '${REMOTE_LATEST_DIR}/compose' '${REMOTE_DUMPS_DIR}'"

# ------------------------------------------------------------------------------
# PostgreSQL Live-Dump erstellen
# ------------------------------------------------------------------------------
log_info "[3/5] Erstelle konsistenten PostgreSQL-Dump..."
if docker exec -i paperless-db-1 pg_dump -U paperless paperless | gzip -9 > "${TEMP_DUMP}"; then
    if [ ! -s "${TEMP_DUMP}" ] || ! gzip -t "${TEMP_DUMP}" 2>/dev/null; then
        log_error "Dump-Erstellung fehlgeschlagen oder Datei korrupt: ${TEMP_DUMP}"
        exit 1
    fi
    DUMP_SIZE=$(du -h "${TEMP_DUMP}" | cut -f1)
    log_success "PostgreSQL-Dump erstellt: ${TEMP_DUMP} (${DUMP_SIZE})"
else
    log_error "Fehler bei pg_dump über Container 'paperless-db-1'!"
    exit 1
fi

log_info "Übertrage Datenbank-Dump auf NAS..."
rsync -az "${TEMP_DUMP}" "${REMOTE_HOST}:${REMOTE_DUMPS_DIR}/paperless_db_${TIMESTAMP}.sql.gz"
ssh "${REMOTE_HOST}" "ln -sf '${REMOTE_DUMPS_DIR}/paperless_db_${TIMESTAMP}.sql.gz' '${REMOTE_DUMPS_DIR}/paperless_db_latest.sql.gz'"
log_success "Datenbank-Dump erfolgreich im NAS-Archiv abgelegt."

# ------------------------------------------------------------------------------
# Dateisystem-Synchronisation (data, media, export, compose)
# ------------------------------------------------------------------------------
log_info "[4/5] Synchronisiere Paperless-Dateien nach ${REMOTE_LATEST_DIR}..."

# 1. Assets synchronisieren (media, data, export; Ausschluss von pgdata, da via pg_dump gesichert)
rsync -avz --delete \
    --exclude='pgdata' \
    --exclude='pgdata/***' \
    --exclude='*.tmp' \
    --exclude='*lock*' \
    "${SOURCE_ASSETS}/" \
    "${REMOTE_HOST}:${REMOTE_LATEST_DIR}/assets/"
log_success "Paperless-Assets (data, media, export) synchronisiert."

# 2. Stack-Konfiguration synchronisieren
rsync -avz --delete \
    --exclude='.git' \
    --exclude='.git/***' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "${SOURCE_COMPOSE}/" \
    "${REMOTE_HOST}:${REMOTE_LATEST_DIR}/compose/"
log_success "Stack-Konfiguration (${SOURCE_COMPOSE}) synchronisiert."

# ------------------------------------------------------------------------------
# Bereinigung alter Dumps auf dem NAS (Retention)
# ------------------------------------------------------------------------------
log_info "[5/5] Bereinige Datenbank-Dumps älter als ${RETENTION_DAYS} Tage auf dem NAS..."
DELETED_DUMPS=$(ssh "${REMOTE_HOST}" "find '${REMOTE_DUMPS_DIR}' -maxdepth 1 -type f -name 'paperless_db_*.sql.gz' -mtime +${RETENTION_DAYS} -print -delete | wc -l" 2>/dev/null || echo 0)
log_info "${DELETED_DUMPS} veraltete(r) Dump(s) bereinigt."

# ------------------------------------------------------------------------------
# Abschluss
# ------------------------------------------------------------------------------
log_success "======================================================"
log_success "✔ PAPERLESS-NGX BACKUP-SYNC ERFOLGREICH ABGESCHLOSSEN!"
log_success "  Ziel:     ${REMOTE_HOST}:${REMOTE_BASE_DIR}"
log_success "  DB-Dump:  paperless_db_${TIMESTAMP}.sql.gz (${DUMP_SIZE})"
log_success "  Logfile:  ${LOG_FILE}"
log_success "======================================================"
