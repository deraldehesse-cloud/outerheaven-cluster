#!/usr/bin/env bash
# ==============================================================================
# 🌌 Outer Heaven Compute Node Initialisierungs-Skript
# ==============================================================================
# Richtet die primäre Schaltzentrale 'outerheaven' ein:
# 1. Erstellt Speicherpfade (/mnt/storage-beta, /mnt/storage-alpha).
# 2. Konfiguriert die NFS-Exporte für das lokale Subnetz (192.168.188.0/24).
# 3. Bereitet die ComfyUI-Ordnerstruktur und Berechtigungen vor.
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🌌 INITIALISIERUNG COMPUTE NODE 'OUTER HEAVEN'     ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"

# 1. Speicherverzeichnisse anlegen
echo -e "\n${BOLD}[1/4] Prüfe Speicherverzeichnisse...${RESET}"
sudo mkdir -p /mnt/storage-beta /mnt/storage-alpha
sudo chown -R outerheaven:outerheaven /mnt/storage-beta /mnt/storage-alpha
echo -e "  [${GREEN}OK${RESET}] Pfade /mnt/storage-beta und /mnt/storage-alpha bereit."

# 2. NFS-Server prüfen / installieren
echo -e "\n${BOLD}[2/4] NFS-Kernel-Server Konfiguration...${RESET}"
if ! dpkg -s nfs-kernel-server >/dev/null 2>&1; then
    echo -e "  [${YELLOW}INFO${RESET}] Installiere nfs-kernel-server..."
    sudo apt update && sudo apt install -y nfs-kernel-server
fi

EXPORTS_FILE="/etc/exports"
NFS_LINE_BETA="/mnt/storage-beta 192.168.188.0/24(rw,sync,no_subtree_check,no_root_squash)"
NFS_LINE_ALPHA="/mnt/storage-alpha 192.168.188.0/24(rw,sync,no_subtree_check,no_root_squash)"

UPDATED=0
if ! grep -qF "/mnt/storage-beta" "$EXPORTS_FILE"; then
    echo "$NFS_LINE_BETA" | sudo tee -a "$EXPORTS_FILE" >/dev/null
    UPDATED=1
fi

if ! grep -qF "/mnt/storage-alpha" "$EXPORTS_FILE"; then
    echo "$NFS_LINE_ALPHA" | sudo tee -a "$EXPORTS_FILE" >/dev/null
    UPDATED=1
fi

if [ "$UPDATED" -eq 1 ]; then
    sudo exportfs -ra
    sudo systemctl restart nfs-kernel-server
    echo -e "  [${GREEN}OK${RESET}] NFS-Exporte aktualisiert und Dienst neu gestartet."
else
    echo -e "  [${GREEN}OK${RESET}] NFS-Exporte sind bereits korrekt konfiguriert."
fi

# 3. ComfyUI-Ordnerstruktur
echo -e "\n${BOLD}[3/4] ComfyUI Verzeichnis-Check...${RESET}"
COMFY_DIR="/home/outerheaven/ComfyUI"
if [ -d "$COMFY_DIR" ]; then
    mkdir -p "$COMFY_DIR/models/checkpoints" \
             "$COMFY_DIR/models/loras" \
             "$COMFY_DIR/models/vae" \
             "$COMFY_DIR/models/controlnet" \
             "$COMFY_DIR/models/upscale_models" \
             "$COMFY_DIR/output" \
             "$COMFY_DIR/input"
    echo -e "  [${GREEN}OK${RESET}] ComfyUI-Model-Pfade validiert."
else
    echo -e "  [${YELLOW}WARNUNG${RESET}] ComfyUI nicht unter $COMFY_DIR gefunden."
fi

# 4. Firewall-Freigaben für LAN
echo -e "\n${BOLD}[4/4] Netzwerk- & Port-Freigaben...${RESET}"
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow from 192.168.188.0/24 to any port nfs
    sudo ufw allow 8188/tcp comment 'ComfyUI Web'
    echo -e "  [${GREEN}OK${RESET}] UFW-Regeln für NFS und ComfyUI gesetzt."
else
    echo -e "  [${GREEN}OK${RESET}] UFW ist nicht aktiv oder nicht installiert (keine Blockierung)."
fi

echo -e "\n${BOLD}${GREEN}✔ Compute Node 'outerheaven' ist initialisiert und exportiert Speicher!${RESET}\n"
