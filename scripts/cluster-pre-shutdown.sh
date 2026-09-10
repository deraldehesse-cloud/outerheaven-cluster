#!/usr/bin/env bash
# cluster-pre-shutdown.sh - Signal Outer Heaven before Fox reboots/shuts down
set -u

OUTERHEAVEN_HOST="outerheaven"
FLAG_FILE="/tmp/fox_is_rebooting"
ONLINE_FILE="/tmp/fox_online"

logger -t cluster-shutdown "Fox Handover: Signalisiere Outer Heaven bevorstehenden Shutdown/Reboot..."

# 1. Direct SSH handover signal to Outer Heaven
ssh -o ConnectTimeout=4 -o BatchMode=yes -o StrictHostKeyChecking=no "$OUTERHEAVEN_HOST" "touch '$FLAG_FILE'; rm -f '$ONLINE_FILE'" 2>/dev/null || true

# 2. REST API call to Mission Control on Outer Heaven
curl -s -m 3 -X POST "http://<OUTERHEAVEN_IP>:8080/api/handover"   -H "X-Cluster-Key: <CLUSTER_API_KEY>"   -H "Content-Type: application/json"   -d '{"status": "rebooting", "node": "fox"}' 2>/dev/null || true

logger -t cluster-shutdown "Outer Heaven erfolgreich über Shutdown benachrichtigt."
exit 0
