#!/usr/bin/env bash
# ==============================================================================
# 🦊 FOX POWER-SCHEDULE (DEPRECATED / ABGELÖST DURCH CLUSTER-POWER.SH)
# ==============================================================================
# ACHTUNG: Die automatisierte Mitternachts-Abschaltung (00:00 Uhr) und der
# feste RTC-Wakeup-Schedule (10:00 Uhr) wurden entkoppelt und stillgelegt.
#
# Alle Rechner im Verbund werden ab sofort on-demand und autark gesteuert:
# Zentrale Steuerung: /home/bigboss/Documents/OuterHeaven-Projekt-doc/scripts/cluster-power.sh
# Desktop-Starter:   Cluster Power Management (cluster-power.desktop)
# ==============================================================================

set -uo pipefail

echo "HINWEIS: Automatisierter Schlafensbefehl (fox-power-schedule.sh) ist entkoppelt und deaktiviert."
echo "Bitte nutze stattdessen das zentrale 'cluster-power.sh' Menü."
if [ "${1:-}" != "--force-legacy-run" ]; then
    exit 0
fi

SCRIPT_NAME="fox-power-schedule"
LOCK_FILE="/run/lock/${SCRIPT_NAME}.lock"
HERMES_ENV_FILE="/home/fox/.hermes/.env"
LOG_FILE="/home/fox/scripts/power-schedule.log"
MAX_WAIT_SEC=900    # Maximal 15 Minuten auf laufende Downloads/Transfers warten
CHECK_INTERVAL_SEC=30

DRY_RUN=false
FORCE=false
STATUS_ONLY=false

# ANSI-Farben
GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

log_info() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') [${CYAN}INFO${RESET}] $1"
    echo -e "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

log_success() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') [${GREEN}OK${RESET}] $1"
    echo -e "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

log_warn() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') [${YELLOW}WARNUNG${RESET}] $1"
    echo -e "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

log_alert() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') [${BOLD}${RED}ALARM${RESET}] $1"
    echo -e "$msg" >&2
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# Parameter auswerten
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=true
            ;;
        --force)
            FORCE=true
            ;;
        --status)
            STATUS_ONLY=true
            ;;
        -h|--help)
            echo "Verwendung: $0 [--dry-run] [--force] [--status]"
            echo "  --dry-run:  Simuliert Prüfung und Zeitberechnung ohne Shutdown"
            echo "  --force:    Ignoriert aktive Transfers und schaltet sofort ab"
            echo "  --status:   Zeigt aktuellen Systemzustand und nächste Weckzeit"
            exit 0
            ;;
    esac
done

# ==============================================================================
# 📲 TELEGRAM BENACHRICHTIGUNG
# ==============================================================================
send_telegram() {
    local header="$1"
    local body="$2"

    if [ ! -f "$HERMES_ENV_FILE" ]; then
        log_warn "Telegram: .env Datei nicht gefunden ($HERMES_ENV_FILE)."
        return 0
    fi

    local token
    token=$(grep -E "^(TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ')
    local chat_id
    chat_id=$(grep -E "^(TELEGRAM_CHAT_ID|TELEGRAM_ALLOWED_USERS)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ' | cut -d',' -f1)

    if [ -z "$token" ] || [ -z "$chat_id" ]; then
        log_warn "Telegram: Token oder Chat-ID nicht konfiguriert."
        return 0
    fi

    local msg="${header}%0A%0A${body}"
    local res
    res=$(curl -s -m 10 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat_id}" \
        -d "parse_mode=HTML" \
        -d "text=${msg}" 2>/dev/null || true)

    if echo "$res" | grep -q '"ok":true'; then
        log_success "Telegram-Nachricht erfolgreich übermittelt."
    else
        log_warn "Telegram-Versand fehlgeschlagen: $res"
    fi
}

# ==============================================================================
# 🔍 TRANSFER- & DOWNLOAD-PRÜFUNG
# ==============================================================================
get_active_nfs_connections() {
    # Zählt aktive NFSv4/v3 TCP-Verbindungen auf Port 2049
    ss -tn state established '( sport = :2049 or dport = :2049 )' 2>/dev/null | grep -v 'Local Address' | wc -l || echo 0
}

get_active_download_pids() {
    # Sucht nach bekannten Download- und Datentransfer-Prozessen
    pgrep -f "(wget|curl|aria2c|git clone|git fetch|yt-dlp|rclone sync|rclone copy)" 2>/dev/null || true
}

get_active_organizer_pids() {
    # Prüft, ob Backup oder Comfy-Organizer noch aktiv sind
    pgrep -f "(backup-fox\.sh|comfyui-organizer\.py)" 2>/dev/null || true
}

check_transfers() {
    local nfs_count
    nfs_count=$(get_active_nfs_connections)
    local dl_pids
    dl_pids=$(get_active_download_pids)
    local org_pids
    org_pids=$(get_active_organizer_pids)

    if [ "$nfs_count" -gt 0 ] || [ -n "$dl_pids" ] || [ -n "$org_pids" ]; then
        return 1
    fi
    return 0
}

# ==============================================================================
# ⏰ BERECHNUNG DER NÄCHSTEN WECKZEIT (10:00:00 UHR)
# ==============================================================================
calculate_next_1000() {
    local now_epoch
    now_epoch=$(date +%s)
    local today_10_epoch
    today_10_epoch=$(date -d "10:00:00" +%s)

    if [ "$now_epoch" -lt "$today_10_epoch" ]; then
        echo "$today_10_epoch"
    else
        date -d "tomorrow 10:00:00" +%s
    fi
}

# Status-Abfrage
if [ "$STATUS_ONLY" = true ]; then
    NOW_EPOCH=$(date +%s)
    TARGET_WAKE=$(calculate_next_1000)
    TARGET_STR=$(date -d "@${TARGET_WAKE}" '+%Y-%m-%d %H:%M:%S')
    DIFF_SEC=$(( TARGET_WAKE - NOW_EPOCH ))
    HOURS=$(( DIFF_SEC / 3600 ))
    MINUTES=$(( (DIFF_SEC % 3600) / 60 ))

    echo -e "${BOLD}${CYAN}=== Fox Power-Schedule Status ===${RESET}"
    echo -e "Aktuelle Zeit:            $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "Nächster Wakeup (10:00):  ${GREEN}${TARGET_STR}${RESET} (in ${HOURS}h ${MINUTES}m)"
    echo -e "Aktive NFS-Verbindungen:  $(get_active_nfs_connections)"
    echo -e "Laufende Downloads:       $([ -n "$(get_active_download_pids)" ] && echo 'JA' || echo 'NEIN')"
    echo -e "Backups/Organizer aktiv:  $([ -n "$(get_active_organizer_pids)" ] && echo 'JA' || echo 'NEIN')"
    echo -e "RTC Wakeup Device:        $([ -f /sys/class/rtc/rtc0/device/power/wakeup ] && cat /sys/class/rtc/rtc0/device/power/wakeup || 'n/a')"
    exit 0
fi

# Lock-Mechanismus
exec 200>"$LOCK_FILE"
flock -n 200 || {
    log_warn "Skript läuft bereits oder Lock-Datei ist gesperrt. Beende."
    exit 0
}

log_info "======================================================"
log_info "   🦊 FOX POWER-SCHEDULE: SHUTDOWN-SEQUENZ GESTARTET   "
log_info "======================================================"

# 1. Auf laufende Transfers und Downloads warten
if [ "$FORCE" = false ]; then
    WAIT_ELAPSED=0
    while ! check_transfers; do
        NFS_CONNS=$(get_active_nfs_connections)
        DL_PIDS=$(get_active_download_pids)
        ORG_PIDS=$(get_active_organizer_pids)

        log_warn "Aktive Transfers erkannt (NFS: $NFS_CONNS | DL-PIDs: ${DL_PIDS:-keine} | Wartung: ${ORG_PIDS:-keine})."
        log_warn "Warte $CHECK_INTERVAL_SEC Sekunden... (Verstrichen: ${WAIT_ELAPSED}s / Max: ${MAX_WAIT_SEC}s)"

        if [ "$WAIT_ELAPSED" -ge "$MAX_WAIT_SEC" ]; then
            log_alert "Timeout von ${MAX_WAIT_SEC}s erreicht! Erzwinge Shutdown trotz aktiver Transfers."
            break
        fi

        sleep "$CHECK_INTERVAL_SEC"
        WAIT_ELAPSED=$(( WAIT_ELAPSED + CHECK_INTERVAL_SEC ))
    done
    log_success "Alle Transfers & Downloads beendet bzw. Wartezeit abgeschlossen."
else
    log_warn "Parameter --force gesetzt: Überspringe Transfer-Prüfung."
fi

# 2. Weckzeit berechnen
NOW_EPOCH=$(date +%s)
TARGET_WAKE_EPOCH=$(calculate_next_1000)
TARGET_DATE_STR=$(date -d "@${TARGET_WAKE_EPOCH}" '+%Y-%m-%d %H:%M:%S')
log_info "Nächster geplanter Wakeup: $TARGET_DATE_STR (Epoche: $TARGET_WAKE_EPOCH)"

# 3. Vorwarnung per Telegram und Handshake zu Outer Heaven
CURRENT_UPTIME=$(uptime -p 2>/dev/null || uptime)
TG_HEADER="🔌 <b>FOX NAS: PLANMÄSSIGER POWER-OFF (00:00 Uhr)</b>"
TG_BODY="Fox geht jetzt zur Stromeinsparung in den Ruhezustand.%0A%0A"
TG_BODY+="• <b>Host:</b> <code>fox-HP-EliteDesk-800-G1-SFF</code>%0A"
TG_BODY+="• <b>Uptime vor Shutdown:</b> <code>${CURRENT_UPTIME}</code>%0A"
TG_BODY+="• <b>Geplantes Aufwachen:</b> <code>${TARGET_DATE_STR}</code> (10:00 Uhr)%0A"
TG_BODY+="• <b>Wakeup-Methoden:</b>%0A"
TG_BODY+="  1. <i>Methode A:</i> Hardware-RTC Alarm (direkt auf Mainboard)%0A"
TG_BODY+="  2. <i>Methode B:</i> Wake-on-LAN Magic Packet via Outer Heaven%0A%0A"
TG_BODY+="Bis später um 10:00 Uhr!"

send_telegram "$TG_HEADER" "$TG_BODY"

if [ -f /home/fox/scripts/cluster-pre-shutdown.sh ]; then
    log_info "Signalisiere Outer Heaven bevorstehenden Offline-Zustand..."
    /home/fox/scripts/cluster-pre-shutdown.sh 2>/dev/null || true
fi

# 4. Container sauber stoppen
if command -v docker >/dev/null 2>&1; then
    RUNNING_CONTAINERS=$(docker ps -q 2>/dev/null || true)
    if [ -n "$RUNNING_CONTAINERS" ]; then
        log_info "Stoppe aktive Docker-Container geordnet (Paperless-Stack)..."
        if [ "$DRY_RUN" = false ]; then
            docker stop $RUNNING_CONTAINERS 2>/dev/null || true
        else
            log_info "[DRY-RUN] docker stop $RUNNING_CONTAINERS übersprungen."
        fi
    fi
fi

# 5. Wake-on-LAN sicherstellen
log_info "Aktivieren von Wake-on-LAN auf Netzwerk-Interface eno1..."
if [ "$DRY_RUN" = false ]; then
    if command -v ethtool >/dev/null 2>&1; then
        ethtool -s eno1 wol g 2>/dev/null || true
    fi
    if command -v nmcli >/dev/null 2>&1; then
        nmcli connection modify netplan-eno1 802-3-ethernet.wake-on-lan magic 2>/dev/null || true
    fi
fi

# 6. Dateisystem-Puffer synchronisieren
log_info "Dateisysteme synchronisieren (sync)..."
if [ "$DRY_RUN" = false ]; then
    sync
fi

# 7. Hardware-RTC Wakeup programmieren & System abschalten
if [ "$DRY_RUN" = true ]; then
    log_info "[DRY-RUN] Würde ausführen:"
    log_info "  /usr/sbin/rtcwake -m off -t $TARGET_WAKE_EPOCH"
    log_info "  Fallback: /usr/sbin/shutdown -h now"
    log_success "[DRY-RUN] Testlauf erfolgreich abgeschlossen."
    exit 0
fi

log_alert "🚨 Programmiere RTC-Wakeup für $TARGET_DATE_STR und fahre System herunter..."
if /usr/sbin/rtcwake -m off -t "$TARGET_WAKE_EPOCH"; then
    log_success "rtcwake erfolgreich ausgeführt. System schaltet ab."
else
    log_alert "rtcwake fehlgeschlagen! Verwende regulären Shutdown als Fallback..."
    /usr/sbin/shutdown -h now
fi
