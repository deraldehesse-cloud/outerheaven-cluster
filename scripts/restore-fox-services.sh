#!/usr/bin/env bash
# ==============================================================================
# ♻️ Hermes & Ollama Full Cluster Restore Tool (Host: Fox)
# ==============================================================================
# Stellt den gesicherten Zustand aus einem tar.gz-Backup wieder her:
# 1. Stellt Hermes-Konfigurationen und Credentials wieder her.
# 2. Wendet alle Code-Patches (Telegram Network, Sliding Window) wieder an.
# 3. Richtet Systemd User Services und Cronjobs wieder ein.
# 4. Prüft & kompiliert Ollama-Modelle bei Bedarf.
# 5. Startet alle Dienste neu.
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

BACKUP_FILE="${1:-/home/fox/backups/latest.tar.gz}"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   ♻️ HERMES & OLLAMA SYSTEM-WIEDERHERSTELLUNG        ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo -e "${RED}Fehler: Backup-Datei '$BACKUP_FILE' existiert nicht!${RESET}"
    exit 1
fi

echo -e "Verwende Archiv: ${CYAN}$BACKUP_FILE${RESET}"

TMP_DIR=$(mktemp -d /tmp/hermes_restore_XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

tar -xzf "$BACKUP_FILE" -C "$TMP_DIR"
RESTORE_ROOT=$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)

# 1. Hermes-Konfigurationen wiederherstellen
echo -e "\n${BOLD}[1/5] Stelle Hermes-Konfigurationen wieder her...${RESET}"
HERMES_DIR="/home/fox/.hermes"
mkdir -p "$HERMES_DIR"

chmod 644 "$HERMES_DIR/config.yaml" "$HERMES_DIR/.env" 2>/dev/null || true
cp -p "$RESTORE_ROOT/hermes_config/"* "$HERMES_DIR/" 2>/dev/null || true
chmod 444 "$HERMES_DIR/config.yaml" "$HERMES_DIR/.env"
echo -e "  [${GREEN}OK${RESET}] config.yaml, .env, SOUL.md und voice mode wiederhergestellt (chmod 444 aktiv)."

# 2. Code-Patches wiederherstellen
echo -e "\n${BOLD}[2/5] Stelle gepatchete Code-Dateien wieder her...${RESET}"
if [ -d "$RESTORE_ROOT/hermes_code_patches" ]; then
    cp -p "$RESTORE_ROOT/hermes_code_patches/telegram_network.py" "$HERMES_DIR/plugins/platforms/telegram/" 2>/dev/null || true
    cp -p "$RESTORE_ROOT/hermes_code_patches/adapter.py" "$HERMES_DIR/plugins/platforms/telegram/" 2>/dev/null || true
    cp -p "$RESTORE_ROOT/hermes_code_patches/base.py" "$HERMES_DIR/gateway/platforms/" 2>/dev/null || true
    cp -p "$RESTORE_ROOT/hermes_code_patches/custom_provider.py" "$HERMES_DIR/plugins/model-providers/custom/__init__.py" 2>/dev/null || true
    cp -p "$RESTORE_ROOT/hermes_code_patches/model_metadata.py" "$HERMES_DIR/agent/" 2>/dev/null || true
    cp -p "$RESTORE_ROOT/hermes_code_patches/chat_completions.py" "$HERMES_DIR/agent/transports/" 2>/dev/null || true
    echo -e "  [${GREEN}OK${RESET}] Code-Patches erfolgreich eingespielt."
fi

# 3. Systemd-Dienste & Cronjobs wiederherstellen
echo -e "\n${BOLD}[3/5] Stelle Systemd-Units und Cronjobs wieder her...${RESET}"
SYSTEMD_USER_DIR="/home/fox/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cp -p "$RESTORE_ROOT/systemd/"*.service "$SYSTEMD_USER_DIR/" 2>/dev/null || true

if [ -f "$RESTORE_ROOT/systemd/crontab.bak" ]; then
    crontab "$RESTORE_ROOT/systemd/crontab.bak" 2>/dev/null || true
    echo -e "  [${GREEN}OK${RESET}] Cronjobs wiederhergestellt."
fi

loginctl enable-linger fox || true
systemctl --user daemon-reload
echo -e "  [${GREEN}OK${RESET}] Systemd User Units geladen und Linger aktiviert."

# 4. Skripte & Ollama prüfen
echo -e "\n${BOLD}[4/5] Stelle Skripte & Ollama-Modelle wieder her...${RESET}"
cp -p "$RESTORE_ROOT/scripts/"* /home/fox/ 2>/dev/null || true
chmod +x /home/fox/*.py /home/fox/*.sh 2>/dev/null || true

if [ -f "$RESTORE_ROOT/ollama/modelfile-hermes3b" ]; then
    cp -p "$RESTORE_ROOT/ollama/modelfile-hermes3b" /home/fox/
    if ! ollama list 2>/dev/null | grep -q "hermes-3-3b-q4"; then
        echo -e "  [${YELLOW}INFO${RESET}] Kompiliere hermes-3-3b-q4:latest neu..."
        ollama create hermes-3-3b-q4:latest -f /home/fox/modelfile-hermes3b
    fi
fi
echo -e "  [${GREEN}OK${RESET}] Skripte und Ollama-Modelle verifiziert."

# 5. Dienste starten & Status prüfen
echo -e "\n${BOLD}[5/5] Starte Dienste neu & prüfe Status...${RESET}"
systemctl --user restart hermes-gateway.service
systemctl --user restart outerheaven-monitor.service

sleep 2

GW_STATE=$(systemctl --user is-active hermes-gateway.service 2>/dev/null || echo 'unbekannt')
MON_STATE=$(systemctl --user is-active outerheaven-monitor.service 2>/dev/null || echo 'unbekannt')

echo -e "  Hermes Gateway: [$GW_STATE]"
echo -e "  Network Monitor: [$MON_STATE]"

echo -e "\n${BOLD}${GREEN}✔ Wiederherstellung erfolgreich abgeschlossen!${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
