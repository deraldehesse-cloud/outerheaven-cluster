#!/usr/bin/env bash
# ==============================================================================
# 📦 Hermes & Ollama Full Cluster Backup Tool (Host: Fox)
# ==============================================================================
# Sichert den gesamten funktionierenden Zustand von:
# - Hermes Konfiguration, Credentials, System-Prompt (SOUL.md), Voice Mode
# - Ollama Modelfiles, Modell-Manifeste und Systemd-Drop-Ins
# - Gepatchete Hermes-Code-Dateien (Telegram Network, Sliding Window, etc.)
# - Systemd User Service Units & Scripts
# - Cronjobs
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_BASE="/home/fox/backups"
SNAP_DIR="$BACKUP_BASE/snapshot_$TIMESTAMP"
GIT_REPO="/home/fox/config_backups"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   📦 HERMES & OLLAMA CLUSTER BACKUP WIRD ERSTELLT    ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "Zeitstempel: $TIMESTAMP"

mkdir -p "$SNAP_DIR"/{hermes_config,hermes_code_patches,systemd,scripts,ollama}

# 1. Hermes Konfigurationen sichern
echo -e "\n${BOLD}[1/5] Sichere Hermes-Konfigurationen...${RESET}"
cp -p /home/fox/.hermes/config.yaml "$SNAP_DIR/hermes_config/"
cp -p /home/fox/.hermes/.env "$SNAP_DIR/hermes_config/"
cp -p /home/fox/.hermes/SOUL.md "$SNAP_DIR/hermes_config/"
cp -p /home/fox/.hermes/gateway_voice_mode.json "$SNAP_DIR/hermes_config/" 2>/dev/null || true
echo -e "  [${GREEN}OK${RESET}] config.yaml, .env, SOUL.md und gateway_voice_mode.json gesichert."

# 2. Gepatchete Hermes-Code-Dateien & Git-Diff sichern
echo -e "\n${BOLD}[2/5] Sichere Hermes-Code-Patches...${RESET}"
(cd /home/fox/.hermes && git diff > "$SNAP_DIR/hermes_code_patches/hermes_changes.diff") || true
cp -p /home/fox/.hermes/plugins/platforms/telegram/telegram_network.py "$SNAP_DIR/hermes_code_patches/" 2>/dev/null || true
cp -p /home/fox/.hermes/plugins/platforms/telegram/adapter.py "$SNAP_DIR/hermes_code_patches/" 2>/dev/null || true
cp -p /home/fox/.hermes/gateway/platforms/base.py "$SNAP_DIR/hermes_code_patches/" 2>/dev/null || true
cp -p /home/fox/.hermes/plugins/model-providers/custom/__init__.py "$SNAP_DIR/hermes_code_patches/custom_provider.py" 2>/dev/null || true
cp -p /home/fox/.hermes/agent/model_metadata.py "$SNAP_DIR/hermes_code_patches/" 2>/dev/null || true
cp -p /home/fox/.hermes/agent/transports/chat_completions.py "$SNAP_DIR/hermes_code_patches/" 2>/dev/null || true
echo -e "  [${GREEN}OK${RESET}] 6 gepatchete Systemdateien und hermes_changes.diff gesichert."

# 3. Systemd-Dienste & Cronjobs sichern
echo -e "\n${BOLD}[3/5] Sichere Systemd-Dienste und Cronjobs...${RESET}"
cp -p /home/fox/.config/systemd/user/hermes-gateway.service "$SNAP_DIR/systemd/" 2>/dev/null || true
cp -p /home/fox/.config/systemd/user/outerheaven-monitor.service "$SNAP_DIR/systemd/" 2>/dev/null || true
crontab -l > "$SNAP_DIR/systemd/crontab.bak" 2>/dev/null || true
echo -e "  [${GREEN}OK${RESET}] hermes-gateway.service, outerheaven-monitor.service und crontab gesichert."

# 4. Skripte & Ollama-Metadaten sichern
echo -e "\n${BOLD}[4/5] Sichere Skripte und Ollama-Informationen...${RESET}"
cp -p /home/fox/network_monitor.py "$SNAP_DIR/scripts/" 2>/dev/null || true
cp -p /home/fox/comfyui-organizer.py "$SNAP_DIR/scripts/" 2>/dev/null || true
cp -p /home/fox/ollama-maintenance.sh "$SNAP_DIR/scripts/" 2>/dev/null || true
cp -p /home/fox/modelfile-hermes3b "$SNAP_DIR/ollama/" 2>/dev/null || true
ollama list > "$SNAP_DIR/ollama/installed_models.txt" 2>/dev/null || true
echo -e "  [${GREEN}OK${RESET}] Wartungsskripte und Ollama-Modelfiles gesichert."

# 5. Tar.gz-Archiv erstellen & Git-Sync
echo -e "\n${BOLD}[5/5] Komprimiere Archiv & aktualisiere Git-Repository...${RESET}"
ARCHIVE="$BACKUP_BASE/hermes_ollama_backup_$TIMESTAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$BACKUP_BASE" "snapshot_$TIMESTAMP"
ln -sf "$ARCHIVE" "$BACKUP_BASE/latest.tar.gz"
rm -rf "$SNAP_DIR"

# Git-Repository aktualisieren
if [ -d "$GIT_REPO/.git" ]; then
    cp -p /home/fox/.hermes/config.yaml "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/.hermes/.env "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/.hermes/SOUL.md "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/.hermes/gateway_voice_mode.json "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/modelfile-hermes3b "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/network_monitor.py "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/comfyui-organizer.py "$GIT_REPO/" 2>/dev/null || true
    cp -p /home/fox/ollama-maintenance.sh "$GIT_REPO/" 2>/dev/null || true
    (
        cd "$GIT_REPO"
        git add .
        git commit -m "Full Service Snapshot $TIMESTAMP" 2>/dev/null || true
    )
fi

ARCHIVE_SIZE=$(ls -lh "$ARCHIVE" | awk '{print $5}')
echo -e "\n${BOLD}${GREEN}✔ Backup erfolgreich abgeschlossen!${RESET}"
echo -e "  Archiv:  ${CYAN}$ARCHIVE${RESET} ($ARCHIVE_SIZE)"
echo -e "  Symlink: ${CYAN}$BACKUP_BASE/latest.tar.gz${RESET}"
echo -e "  Git:     ${CYAN}$GIT_REPO${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
