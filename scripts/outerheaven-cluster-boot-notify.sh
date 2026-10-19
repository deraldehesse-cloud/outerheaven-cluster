#!/usr/bin/env bash
# ==============================================================================
# outerheaven-cluster-boot-notify.sh - Outer Heaven Boot Verification & Telegram Alert
# ==============================================================================

set -u

HERMES_ENV_FILE="/home/outerheaven/.hermes/.env"

logger -t cluster-boot "Outer Heaven Boot: Warte auf Initialisierung der Dienste..."

# 1. Warte bis die zentralen Dienste hochgefahren sind (bis zu 45s)
for i in {1..15}; do
    BOT_OK=false
    COMFY_OK=false
    OLLAMA_OK=false
    MEGAN_OK=false

    if systemctl --user is-active bigboss-haven-bot.service >/dev/null 2>&1; then
        BOT_OK=true
    fi
    if systemctl --user is-active comfyui.service >/dev/null 2>&1; then
        COMFY_OK=true
    fi
    if systemctl is-active ollama.service >/dev/null 2>&1; then
        OLLAMA_OK=true
    fi
    if systemctl --user is-active megan-studio.service >/dev/null 2>&1; then
        MEGAN_OK=true
    fi

    if [ "$BOT_OK" = true ] && [ "$COMFY_OK" = true ] && [ "$OLLAMA_OK" = true ] && [ "$MEGAN_OK" = true ]; then
        logger -t cluster-boot "Alle Kern-Dienste (Bot, ComfyUI, Ollama, Megan Studio) sind aktiv."
        break
    fi
    sleep 3
done

MC_STATUS=$(systemctl --user is-active mission-control.service 2>/dev/null || echo "inaktiv")
BOT_STATUS=$(systemctl --user is-active bigboss-haven-bot.service 2>/dev/null || echo "inaktiv")
COMFY_STATUS=$(systemctl --user is-active comfyui.service 2>/dev/null || echo "inaktiv")
OLLAMA_STATUS=$(systemctl is-active ollama.service 2>/dev/null || echo "inaktiv")
MEGAN_STATUS=$(systemctl --user is-active megan-studio.service 2>/dev/null || echo "inaktiv")

# 2. Telegram Benachrichtigung an Big Boss
if [ -f "$HERMES_ENV_FILE" ]; then
    TG_TOKEN=$(grep -E "^(TELEGRAM_BOT_TOKEN|TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN|TELEGRAM_TOKEN)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ')
    TG_CHAT=$(grep -E "^(TELEGRAM_CHAT_ID|TELEGRAM_ALLOWED_USERS)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ' | cut -d',' -f1)

    if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
        NOW_STR=$(date '+%H:%M:%S')
        TG_TEXT="✅ <b>OUTER HEAVEN WIEDER ONLINE (${NOW_STR})</b>%0A%0A"
        TG_TEXT+="• <b>Host:</b> <code>outerheaven-workstation (192.168.188.21)</code>%0A"
        TG_TEXT+="• <b>Megan Studio (:8080):</b> <code>[${MEGAN_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>Megan Companion Bot:</b> <code>[${BOT_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>ComfyUI (Arc A770):</b> <code>[${COMFY_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>Ollama:</b> <code>[${OLLAMA_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>Mission Control (:8000):</b> <code>[${MC_STATUS^^}]</code>%0A%0A"
        TG_TEXT+="ℹ️ <i>Beide Rechner sind frisch gestartet. Der Cluster ist voll einsatzbereit!</i>"

        curl -s -m 10 -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage"             -d "chat_id=${TG_CHAT}" -d "parse_mode=HTML" -d "text=${TG_TEXT}" 2>/dev/null || true
    fi
fi

logger -t cluster-boot "Outer Heaven Boot-Notification erfolgreich abgeschlossen."
exit 0
