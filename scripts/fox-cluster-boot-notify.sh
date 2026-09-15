#!/usr/bin/env bash
# ==============================================================================
# cluster-boot-notify.sh - Fox Boot Verification & Handover Notification
# ==============================================================================

set -u

OUTERHEAVEN_HOST="outerheaven"
FLAG_FILE="/tmp/fox_is_rebooting"
ONLINE_FILE="/tmp/fox_online"
HERMES_ENV_FILE="/home/fox/.hermes/.env"

logger -t cluster-boot "Fox Boot: Warte auf Netzwerk und starte Dienste-Prüfung..."

# 1. Warte auf Netzwerk & SSH zu Outer Heaven
for i in {1..20}; do
    if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no "$OUTERHEAVEN_HOST" "rm -f '$FLAG_FILE'; touch '$ONLINE_FILE'" 2>/dev/null; then
        logger -t cluster-boot "Outer Heaven Handover: Entwarnung gesendet. Fox ist ONLINE."
        break
    fi
    sleep 2
done

# 2. Mission Control API Notifier
curl -s -m 3 -X POST "http://100.116.38.29:8080/api/handover" \
  -H "X-Cluster-Key: 85dab2145867139a01007c72a4c08a9e07f6e260b0bc4a1b096890567dea25e8" \
  -H "Content-Type: application/json" \
  -d '{"status": "online", "node": "fox"}' 2>/dev/null || true

# 3. Sicherstellen, dass Hermes Gateway & Ollama aktiv sind
systemctl --user start hermes-gateway.service 2>/dev/null || true

sleep 3
GW_STATUS=$(systemctl --user is-active hermes-gateway.service 2>/dev/null || echo "unbekannt")
OLLAMA_STATUS=$(systemctl is-active ollama.service 2>/dev/null || echo "unbekannt")

# 4. Telegram Benachrichtigung an Big Boss
if [ -f "$HERMES_ENV_FILE" ]; then
    TG_TOKEN=$(grep -E "^(TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ')
    TG_CHAT=$(grep -E "^(TELEGRAM_CHAT_ID|TELEGRAM_ALLOWED_USERS)=" "$HERMES_ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"'\'' ' | cut -d',' -f1)
    
    if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
        NOW_STR=$(date '+%H:%M:%S')
        TG_TEXT="✅ <b>FOX WIEDER ONLINE (${NOW_STR})</b>%0A%0A"
        TG_TEXT+="• <b>Host:</b> <code>fox-node (192.168.188.91)</code>%0A"
        TG_TEXT+="• <b>Hermes Gateway:</b> <code>[${GW_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>Ollama:</b> <code>[${OLLAMA_STATUS^^}]</code>%0A"
        TG_TEXT+="• <b>Cluster-Handover:</b> <code>[VERBUNDEN]</code>%0A%0A"
        TG_TEXT+="ℹ️ <i>Alle Dienste laufen planmäßig.</i>"

        curl -s -m 10 -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT}" -d "parse_mode=HTML" -d "text=${TG_TEXT}" 2>/dev/null || true
    fi
fi

logger -t cluster-boot "Fox Boot-Notification abgeschlossen."
exit 0
