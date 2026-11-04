#!/bin/bash
REMOTE="fox"
TARGET_DIR="/home/fox/scripts"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v notify-send >/dev/null && notify-send "Hermes Deploy" "Pushe Update auf Fox..."

# 1. Dateien rüberschieben
scp "$LOCAL_DIR/hermes_web_ui.py" "$LOCAL_DIR/cluster_nodes.json" "$LOCAL_DIR/skills_registry.json" "$LOCAL_DIR/hermes_skills.json" "$REMOTE:$TARGET_DIR/"

# 2. Service via systemd neu starten
ssh $REMOTE "systemctl --user restart hermes-agent.service"

command -v notify-send >/dev/null && notify-send "Hermes Deploy" "Update erfolgreich! Server läuft auf Fox (:8081)."
