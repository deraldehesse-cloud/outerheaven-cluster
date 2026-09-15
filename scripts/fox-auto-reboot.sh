#!/usr/bin/env bash
# ==============================================================================
# ⏰ FOX TÄGLICHER 06:00 UHR REBOOT-SERVICE (Outer Heaven Cluster)
# ==============================================================================
# Zeitplan: Jeden Morgen pünktlich um 06:00:00 Uhr.
# Outer Heaven startet ca. 1 Minute später um 06:01:00 Uhr.
# Vorwarnung an Big Boss per Telegram um 05:55:00 Uhr (5 Min. vorher).
# ==============================================================================

set -uo pipefail

TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID="YOUR_TELEGRAM_CHAT_ID_HERE"
HERMES_ENV_FILE="/home/fox/.hermes/.env"

WARN_BEFORE_SEC=300
HEARTBEAT_INTERVAL_SEC=1800

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

log_info() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${CYAN}INFO${RESET}] $1"; }
log_success() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${GREEN}OK${RESET}] $1"; }
log_warn() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${YELLOW}WARNUNG${RESET}] $1"; }
log_alert() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${BOLD}${RED}ALARM${RESET}] $1" >&2; }

trap 'log_info "Auto-Reboot-Dienst wird kontrolliert beendet..."; exit 0' SIGTERM SIGINT

# 0. Bereinigung defekter Dienste auf Fox (ComfyUI Zombie-Fix mit Root-Rechten)
if systemctl is-active --quiet comfyui.service 2>/dev/null || systemctl is-enabled --quiet comfyui.service 2>/dev/null; then
    log_info "Deaktiviere fehlerhaften lokalen comfyui.service auf Fox..."
    systemctl disable --now comfyui.service 2>/dev/null || true
fi

# Telegram Zugangsdaten laden
if [ -f "$HERMES_ENV_FILE" ]; then
    ENV_TOKEN=$(grep -E "^(TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ')
    if [ -n "$ENV_TOKEN" ]; then
        TELEGRAM_BOT_TOKEN="$ENV_TOKEN"
        log_info "Telegram Bot Token aus $HERMES_ENV_FILE bezogen."
    fi
    ENV_CHAT=$(grep -E "^(TELEGRAM_CHAT_ID|TELEGRAM_ALLOWED_USERS)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ' | cut -d',' -f1)
    if [ -n "$ENV_CHAT" ]; then
        TELEGRAM_CHAT_ID="$ENV_CHAT"
        log_info "Telegram Chat ID geladen: $TELEGRAM_CHAT_ID"
    fi
fi

send_telegram_notification() {
    local header="$1"
    local message_body="$2"

    if [ "$TELEGRAM_BOT_TOKEN" = "YOUR_TELEGRAM_BOT_TOKEN_HERE" ] || [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_CHAT_ID" = "YOUR_TELEGRAM_CHAT_ID_HERE" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        log_warn "Telegram-Benachrichtigung übersprungen: Token oder Chat-ID fehlen!"
        return 0
    fi

    local full_message="${header}%0A%0A${message_body}"
    local res
    res=$(curl -s -m 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" -d "parse_mode=HTML" -d "text=${full_message}" 2>/dev/null || true)

    if echo "$res" | grep -q '"ok":true'; then
        log_success "Telegram-Meldung erfolgreich abgesetzt."
    else
        log_warn "Fehler beim Senden der Telegram-Nachricht: $res"
    fi
}

format_duration() {
    local total_seconds="$1"
    local hours=$(( total_seconds / 3600 ))
    local minutes=$(( (total_seconds % 3600) / 60 ))
    local seconds=$(( total_seconds % 60 ))
    printf "%02dh %02dm %02ds" "$hours" "$minutes" "$seconds"
}

calculate_next_0600() {
    local now
    now=$(date +%s)
    local target
    target=$(date -d "06:00:00" +%s)
    if [ "$target" -le "$now" ]; then
        target=$(date -d "tomorrow 06:00:00" +%s)
    fi
    echo "$target"
}

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   ⏰ FOX TÄGLICHER 06:00 UHR REBOOT-WECKER GESTARTET ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
log_info "Ziel-Host:              fox (HP EliteDesk 800 G1 SFF)"
log_info "Reboot-Uhrzeit:         Täglich um 06:00:00 Uhr"
log_info "Vorwarnung:             um 05:55:00 Uhr (5 Minuten vorher)"
log_info "Outer Heaven:           startet ca. 1 Minute später um 06:01:00 Uhr"

while true; do
    TARGET_REBOOT_EPOCH=$(calculate_next_0600)
    TARGET_WARN_EPOCH=$(( TARGET_REBOOT_EPOCH - WARN_BEFORE_SEC ))

    REBOOT_DATE_STR=$(date -d "@${TARGET_REBOOT_EPOCH}" '+%Y-%m-%d %H:%M:%S')
    WARN_DATE_STR=$(date -d "@${TARGET_WARN_EPOCH}" '+%Y-%m-%d %H:%M:%S')

    log_info "Nächster geplanter Reboot: $REBOOT_DATE_STR (Vorwarnung: $WARN_DATE_STR)"

    while true; do
        NOW_EPOCH=$(date +%s)
        REMAINING_TO_WARN=$(( TARGET_WARN_EPOCH - NOW_EPOCH ))

        if [ "$REMAINING_TO_WARN" -le 0 ]; then
            break
        fi

        REMAINING_TO_REBOOT=$(( TARGET_REBOOT_EPOCH - NOW_EPOCH ))
        log_info "Heartbeat: Noch $(format_duration "$REMAINING_TO_REBOOT") bis zum täglichen Reboot ($REBOOT_DATE_STR)."

        SLEEP_CHUNK=$HEARTBEAT_INTERVAL_SEC
        if [ "$REMAINING_TO_WARN" -lt "$SLEEP_CHUNK" ]; then
            SLEEP_CHUNK=$REMAINING_TO_WARN
        fi
        sleep "$SLEEP_CHUNK"
    done

    echo -e "\n${BOLD}${YELLOW}******************************************************${RESET}"
    log_warn "⚠️ REBOOT-VORWARNUNG: In 5 Minuten wird Fox neu gestartet!"
    log_warn "Geplanter Reboot-Zeitpunkt: $REBOOT_DATE_STR"
    echo -e "${BOLD}${YELLOW}******************************************************${RESET}"

    CURRENT_UPTIME=$(uptime -p 2>/dev/null || uptime)

    TG_HEADER="⏰ <b>FOX CLUSTER REBOOT VORWARNUNG</b>"
    TG_BODY="Geplanter täglicher Neustart in <b>5 Minuten</b> (um 06:00:00 Uhr).%0A%0A"
    TG_BODY+="• <b>Host:</b> <code>fox-node (192.168.188.91)</code>%0A"
    TG_BODY+="• <b>Uptime:</b> <code>${CURRENT_UPTIME}</code>%0A"
    TG_BODY+="• <b>Ablauf:</b>%0A"
    TG_BODY+="  1. <b>06:00 Uhr:</b> Fox startet neu%0A"
    TG_BODY+="  2. <b>06:01 Uhr:</b> Outer Heaven startet neu (+1 Min)%0A"
    TG_BODY+="  3. <b>Nach Boot:</b> Hermes Gateway & alle Dienste starten automatisch."

    send_telegram_notification "$TG_HEADER" "$TG_BODY"

    # Warte bis 60 Sekunden vor Reboot
    NOW_EPOCH=$(date +%s)
    REMAINING=$(( TARGET_REBOOT_EPOCH - NOW_EPOCH ))
    if [ "$REMAINING" -gt 60 ]; then
        sleep $(( REMAINING - 60 ))
        log_warn "⚠️ Finale Vorwarnung: Systemneustart in 60 Sekunden!"
        NOW_EPOCH=$(date +%s)
        REMAINING=$(( TARGET_REBOOT_EPOCH - NOW_EPOCH ))
        if [ "$REMAINING" -gt 0 ]; then
            sleep "$REMAINING"
        fi
    elif [ "$REMAINING" -gt 0 ]; then
        sleep "$REMAINING"
    fi

    echo -e "\n${BOLD}${RED}******************************************************${RESET}"
    log_alert "🚨 06:00 UHR ERREICHT! FÜHRE SYSTEMNEUSTART VON FOX AUS..."
    echo -e "${BOLD}${RED}******************************************************${RESET}"

    send_telegram_notification "⚡ <b>FOX STARTET JETZT NEU (06:00:00)</b>" "Täglicher Wartungs-Reboot läuft. Outer Heaven folgt um 06:01:00 Uhr. Bis gleich!"

    # Signalisiere Outer Heaven bevorstehenden Reboot
    if [ -f /home/fox/scripts/cluster-pre-shutdown.sh ]; then
        log_info "Signalisiere Outer Heaven bevorstehenden Reboot..."
        /home/fox/scripts/cluster-pre-shutdown.sh 2>/dev/null || true
    fi

    log_info "Dateisysteme synchronisieren (sync)..."
    sync

    log_alert "Führe Systemneustart aus..."
    systemctl reboot || reboot || sudo reboot || {
        log_alert "Fehler: Reboot-Befehl fehlgeschlagen!"
        sleep 60
    }
done
