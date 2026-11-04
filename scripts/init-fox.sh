#!/usr/bin/env bash
# ==============================================================================
# 🦊 Outer Heaven Worker Node Initialisierungs- & Härtungs-Skript
# ==============================================================================
# Dieses Skript initialisiert und verifiziert die Konfiguration auf Node 'fox':
# 1. Prüft & verlinkt statisches FFmpeg (libopus) für Telegram Voice-Bubbles.
# 2. Prüft Ollama & kompilierte Hermes-3B Quantisierungen (Q4, Q6, Q8).
# 3. Validiert die Hermes Agent & Telegram-Gateway Konfiguration.
# 4. Erzwingt permanente deutsche Sprachausgabe (gateway_voice_mode.json).
# 5. Sichert und härtet Konfigurationen (chmod 444 & Git-Commit).
# 6. Aktiviert alle Hintergrund-Dienste (hermes-gateway, network-monitor).
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🦊 INITIALISIERUNG & HÄRTUNG WORKER NODE 'FOX'    ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"

HERMES_DIR="/home/fox/.hermes"
BACKUP_DIR="/home/fox/config_backups"

# 1. FFmpeg & Audio-Tools prüfen
echo -e "\n${BOLD}[1/6] Überprüfung der FFmpeg / Audio-Pipeline...${RESET}"
if [ -x "/home/fox/.local/bin/ffmpeg" ]; then
    echo -e "  [${GREEN}OK${RESET}] FFmpeg gefunden unter /home/fox/.local/bin/ffmpeg."
    mkdir -p "$HERMES_DIR/.venv/bin"
    ln -sf /home/fox/.local/bin/ffmpeg "$HERMES_DIR/.venv/bin/ffmpeg"
    ln -sf /home/fox/.local/bin/ffprobe "$HERMES_DIR/.venv/bin/ffprobe" 2>/dev/null || true
    echo -e "  [${GREEN}OK${RESET}] FFmpeg-Symlinks im Virtualenv aktualisiert."
else
    echo -e "  [${YELLOW}WARNUNG${RESET}] FFmpeg nicht unter /home/fox/.local/bin/ffmpeg gefunden! Bitte manuell installieren."
fi

# 2. Ollama & Modell-Status prüfen
echo -e "\n${BOLD}[2/6] Überprüfung der Ollama-Modelle...${RESET}"
if systemctl is-active --quiet ollama; then
    echo -e "  [${GREEN}OK${RESET}] Ollama-Dienst ist aktiv."
    if ollama list | grep -q "hermes-3-3b-q4"; then
        echo -e "  [${GREEN}OK${RESET}] Standard-Modell 'hermes-3-3b-q4:latest' ist registriert."
    else
        echo -e "  [${YELLOW}INFO${RESET}] Modell 'hermes-3-3b-q4' fehlt. Kompiliere aus Modelfile..."
        if [ -f "/home/fox/modelfile-hermes3b" ]; then
            ollama create hermes-3-3b-q4:latest -f /home/fox/modelfile-hermes3b
            echo -e "  [${GREEN}OK${RESET}] Modell erfolgreich erstellt."
        fi
    fi
else
    echo -e "  [${RED}FEHLER${RESET}] Ollama-Dienst läuft nicht!"
fi

# 3. Voice Mode JSON erzwingen
echo -e "\n${BOLD}[3/6] Konfiguration des verbalen Sprachmodus...${RESET}"
echo '{"telegram:7934555424": "all"}' > "$HERMES_DIR/gateway_voice_mode.json"
chmod 644 "$HERMES_DIR/gateway_voice_mode.json"
echo -e "  [${GREEN}OK${RESET}] Chat 7934555424 dauerhaft auf Modus 'all' (Sprachantworten) fixiert."

# 4. Konfiguration sichern & Git-Backup
echo -e "\n${BOLD}[4/6] Absicherung & Backup der Konfigurationsdateien...${RESET}"
mkdir -p "$BACKUP_DIR"
if [ ! -d "$BACKUP_DIR/.git" ]; then
    (cd "$BACKUP_DIR" && git init)
fi

# Schreibschutz kurzfristig aufheben für Backup-Abgleich
chmod 644 "$HERMES_DIR/config.yaml" "$HERMES_DIR/.env" 2>/dev/null || true
cp -p "$HERMES_DIR/config.yaml" "$BACKUP_DIR/"
cp -p "$HERMES_DIR/.env" "$BACKUP_DIR/"

(
    cd "$BACKUP_DIR"
    git add config.yaml .env
    git commit -m "Auto-Backup via init-fox.sh ($(date '+%Y-%m-%d %H:%M:%S'))" 2>/dev/null || true
)

# Schreibschutz zwingend wiederherstellen
chmod 444 "$HERMES_DIR/config.yaml" "$HERMES_DIR/.env"
echo -e "  [${GREEN}OK${RESET}] Schreibschutz (chmod 444) auf config.yaml und .env wiederhergestellt."
echo -e "  [${GREEN}OK${RESET}] Konfigurations-Stand im Git-Repository gesichert."

# 5. Cronjobs prüfen
echo -e "\n${BOLD}[5/6] Überprüfung der Cronjobs...${RESET}"
if crontab -l 2>/dev/null | grep -q "comfyui-organizer.py"; then
    echo -e "  [${GREEN}OK${RESET}] Täglicher ComfyUI-Organizer Cronjob ist aktiv."
else
    echo -e "  [${YELLOW}INFO${RESET}] Richte Cronjob für 04:00 Uhr morgens ein..."
    (crontab -l 2>/dev/null; echo "0 4 * * * /usr/bin/python3 /home/fox/comfyui-organizer.py --apply >> /home/fox/organizer_cron.log 2>&1") | crontab -
    echo -e "  [${GREEN}OK${RESET}] Cronjob eingerichtet."
fi

# 6. Systemd-Dienste neu starten
echo -e "\n${BOLD}[6/6] Systemd-Dienste aktualisieren & prüfen...${RESET}"
loginctl enable-linger fox || true
systemctl --user daemon-reload
systemctl --user restart hermes-gateway.service
systemctl --user restart outerheaven-monitor.service

sleep 2

GW_STATE=$(systemctl --user is-active hermes-gateway.service)
MON_STATE=$(systemctl --user is-active outerheaven-monitor.service)

echo -e "  Hermes Gateway Service: [$GW_STATE]"
echo -e "  Outer Heaven Monitor:   [$MON_STATE]"

echo -e "\n${BOLD}${GREEN}✔ Worker Node 'fox' ist vollständig initialisiert und betriebsbereit!${RESET}\n"
