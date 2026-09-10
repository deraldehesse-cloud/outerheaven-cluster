#!/usr/bin/env bash
# cluster-boot-notify.sh - Signal Outer Heaven that Fox is back online
set -u

OUTERHEAVEN_HOST="outerheaven"
FLAG_FILE="/tmp/fox_is_rebooting"
ONLINE_FILE="/tmp/fox_online"

logger -t cluster-boot "Fox Handover: Warte auf Netzwerk und melde Fox ONLINE an Outer Heaven..."

for i in {1..20}; do
    if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no "$OUTERHEAVEN_HOST" "rm -f '$FLAG_FILE'; touch '$ONLINE_FILE'" 2>/dev/null; then
        logger -t cluster-boot "Outer Heaven Handover: Entwarnung erfolgreich gesendet. Fox ist ONLINE."
        break
    fi
    sleep 2
done

# Also notify Mission Control API
curl -s -m 3 -X POST "http://<OUTERHEAVEN_IP>:8080/api/handover"   -H "X-Cluster-Key: <CLUSTER_API_KEY>"   -H "Content-Type: application/json"   -d '{"status": "online", "node": "fox"}' 2>/dev/null || true

exit 0
