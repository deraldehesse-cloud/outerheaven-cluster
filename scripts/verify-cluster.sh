#!/usr/bin/env bash
# ==============================================================================
# 🛰️ Outer Heaven Cluster Health & Verification Tool
# ==============================================================================
# Führt von Big Boss aus eine vollständige Diagnose des OuterHeaven-Clusters durch:
# - Tailscale- und SSH-Konnektivität zu 'fox' und 'outerheaven'
# - Status von Hermes-Gateway, Ollama, VRAM, RAM, Swap und Systemd-Diensten auf Fox
# - NFS-Mount-Erreichbarkeit und ComfyUI-Status
# ==============================================================================

set -u

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
BLUE="\033[94m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🛰️ OUTER HEAVEN CLUSTER DIAGNOSE & HEALTH CHECK    ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
date "+Datum: %Y-%m-%d %H:%M:%S"
echo ""

# ------------------------------------------------------------------------------
# 1. PRÜFUNG: WORKER NODE 'FOX' (Always-On Standby)
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}1. Prüfung Node 'fox' (<FOX_IP>)...${RESET}"

if ssh -o ConnectTimeout=3 -o BatchMode=yes fox "true" 2>/dev/null; then
    echo -e "  [${GREEN}ONLINE${RESET}] SSH-Verbindung zu 'fox' erfolgreich."

    # Hostname & Uptime
    FOX_INFO=$(ssh fox "hostname && uptime -p")
    echo -e "  Host: $(echo "$FOX_INFO" | head -n 1) ($(echo "$FOX_INFO" | tail -n 1))"

    # Autostart & Reboot-Persistenz
    LINGER=$(ssh fox "loginctl show-user fox -p Linger 2>/dev/null | cut -d= -f2 || echo 'no'")
    GW_EN=$(ssh fox "systemctl --user is-enabled hermes-gateway.service 2>/dev/null || echo 'disabled'")
    OLL_EN=$(ssh fox "systemctl is-enabled ollama 2>/dev/null || echo 'disabled'")
    if [ "$LINGER" = "yes" ] && [ "$GW_EN" = "enabled" ] && [ "$OLL_EN" = "enabled" ]; then
        echo -e "  Reboot-Autostart:       [${GREEN}GESICHERT${RESET}] (Linger=yes, Services enabled)"
    else
        echo -e "  Reboot-Autostart:       [${YELLOW}UNVOLLSTÄNDIG${RESET}] (Linger: $LINGER, Gateway: $GW_EN, Ollama: $OLL_EN)"
    fi

    # Hermes Gateway Status
    GW_STATUS=$(ssh fox "systemctl --user is-active hermes-gateway.service 2>/dev/null || echo 'inactive'")
    if [ "$GW_STATUS" = "active" ]; then
        echo -e "  Hermes Gateway Service: [${GREEN}AKTIV / RUNNING${RESET}]"
    else
        echo -e "  Hermes Gateway Service: [${RED}$GW_STATUS${RESET}]"
    fi

    # Network Monitor Service
    MON_STATUS=$(ssh fox "systemctl --user is-active outerheaven-monitor.service 2>/dev/null || echo 'inactive'")
    if [ "$MON_STATUS" = "active" ]; then
        echo -e "  Outer Heaven Monitor:   [${GREEN}AKTIV / RUNNING${RESET}]"
    else
        echo -e "  Outer Heaven Monitor:   [${YELLOW}$MON_STATUS${RESET}]"
    fi

    # Ollama Service & Models
    OLLAMA_STATUS=$(ssh fox "systemctl is-active ollama 2>/dev/null || echo 'inactive'")
    if [ "$OLLAMA_STATUS" = "active" ]; then
        echo -e "  Ollama Daemon:          [${GREEN}AKTIV${RESET}]"
        MODELS=$(ssh fox "ollama list 2>/dev/null | grep -E 'hermes-3-3b' || echo 'Keine Hermes-Modelle gefunden'")
        echo -e "  Installierte Modelle:\n${CYAN}$MODELS${RESET}"
        PROXY_STATUS=$(ssh fox "systemctl --user is-active ollama-tailscale-proxy.service 2>/dev/null || echo 'inactive'")
        echo -e "  Tailscale Ollama Proxy: [${GREEN}$PROXY_STATUS${RESET}] (:11434)"
    else
        echo -e "  Ollama Daemon:          [${RED}$OLLAMA_STATUS${RESET}]"
    fi

    # Speicher (RAM, Swap, VRAM)
    MEM_INFO=$(ssh fox "free -h | awk '/Mem:/ {print \$3 \" / \" \$2} /Swap:/ {print \"Swap: \" \$3 \" / \" \$2}'")
    echo -e "  Arbeitsspeicher & Swap: $MEM_INFO"

    # Lokale Festplatten
    DISK_INFO=$(ssh fox "df -h / /mnt/fox-assets 2>/dev/null | awk 'NR>1 {print \$6 \": \" \$4 \" frei (\" \$5 \" belegt)\"}'")
    echo -e "  Lokaler Speicher:\n$DISK_INFO"

    # NFS Mount Status (/mnt/storage-beta)
    echo -n "  NFS-Mount /mnt/storage-beta: "
    NFS_CHECK=$(ssh fox "timeout 2 test -d /mnt/storage-beta && echo 'MOUNTED' || echo 'OFFLINE'")
    if [ "$NFS_CHECK" = "MOUNTED" ]; then
        echo -e "[${GREEN}VERBUNDEN / ONLINE${RESET}]"
    else
        echo -e "[${YELLOW}OFFLINE / SCHLÄFT${RESET}] (Outer Heaven ist aktuell nicht verbunden)"
    fi

else
    echo -e "  [${RED}OFFLINE${RESET}] 'fox' ist über SSH/Tailscale nicht erreichbar!"
fi

echo ""

# ------------------------------------------------------------------------------
# 2. PRÜFUNG: COMPUTE NODE 'OUTER HEAVEN' (On-Demand)
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}2. Prüfung Node 'outerheaven' (<OUTERHEAVEN_IP>)...${RESET}"

if ssh -o ConnectTimeout=3 -o BatchMode=yes outerheaven "true" 2>/dev/null; then
    echo -e "  [${GREEN}ONLINE${RESET}] SSH-Verbindung zu 'outerheaven' erfolgreich."
    OH_INFO=$(ssh outerheaven "hostname && uptime -p")
    echo -e "  Host: $(echo "$OH_INFO" | head -n 1) ($(echo "$OH_INFO" | tail -n 1))"

    # Hermes Telegram Service Check
    TG_STATUS=$(ssh outerheaven "systemctl --user is-active hermes-telegram.service 2>/dev/null || echo 'inactive'")
    if [ "$TG_STATUS" = "active" ]; then
        echo -e "  Hermes Telegram Service: [${GREEN}AKTIV / RUNNING${RESET}] (Bigbossheaven_bot)"
    else
        echo -e "  Hermes Telegram Service: [${YELLOW}$TG_STATUS${RESET}]"
    fi

    # Ollama Service & Models
    OLLAMA_OH=$(ssh outerheaven "systemctl is-active ollama 2>/dev/null || echo 'inactive'")
    if [ "$OLLAMA_OH" = "active" ]; then
        echo -e "  Ollama Daemon:          [${GREEN}AKTIV${RESET}]"
        MODELS_OH=$(ssh outerheaven "ollama list 2>/dev/null | grep -E 'deepseek-r1' || echo 'Kein deepseek-r1 gefunden'")
        echo -e "  Installierte Modelle:\n${CYAN}$MODELS_OH${RESET}"
    else
        echo -e "  Ollama Daemon:          [${RED}$OLLAMA_OH${RESET}]"
    fi

    # ComfyUI Port Check
    COMFY_PORT=$(ssh outerheaven "ss -tuln | grep -q ':8188 ' && echo 'RUNNING' || echo 'STOPPED'")
    if [ "$COMFY_PORT" = "RUNNING" ]; then
        echo -e "  ComfyUI Server (:8188): [${GREEN}AKTIV / BEREIT${RESET}]"
    else
        echo -e "  ComfyUI Server (:8188): [${YELLOW}BEENDET / STANDBY${RESET}]"
    fi

    # Mission Control Dashboard Check
    MC_STATUS=$(ssh outerheaven "systemctl --user is-active mission-control.service 2>/dev/null || echo 'inactive'")
    if [ "$MC_STATUS" = "active" ]; then
        MC_BIND=$(ssh outerheaven "ss -tuln | grep ':8080 ' | awk '{print \$5}' | tr '\n' ' '")
        echo -e "  Mission Control (:8080):[${GREEN}AKTIV / RUNNING${RESET}] (Binding: $MC_BIND)"
    else
        echo -e "  Mission Control (:8080):[${YELLOW}$MC_STATUS${RESET}]"
    fi

    # NFS Server
    NFS_SRV=$(ssh outerheaven "systemctl is-active nfs-kernel-server 2>/dev/null || echo 'inactive'")
    echo -e "  NFS Kernel Server:      [$NFS_SRV]"

    # Secrets Check
    SEC_CHECK=$(ssh outerheaven "test -f /mnt/storage-alpha/secrets/telegram_outerheaven.token && stat -c %a /mnt/storage-alpha/secrets/telegram_outerheaven.token 2>/dev/null || echo 'missing'")
    MC_PW_CHECK=$(ssh outerheaven "test -f /mnt/storage-alpha/secrets/dashboard_password.txt && stat -c %a /mnt/storage-alpha/secrets/dashboard_password.txt 2>/dev/null || echo 'missing'")
    MC_KEY_CHECK=$(ssh outerheaven "test -f /mnt/storage-alpha/secrets/cluster_api.key && stat -c %a /mnt/storage-alpha/secrets/cluster_api.key 2>/dev/null || echo 'missing'")
    if [ "$SEC_CHECK" = "600" ] && [ "$MC_PW_CHECK" = "600" ] && [ "$MC_KEY_CHECK" = "600" ]; then
        echo -e "  Cluster Secrets (Alpha):[${GREEN}GESICHERT (alle chmod 600)${RESET}]"
    else
        echo -e "  Cluster Secrets (Alpha):[${YELLOW}Token: $SEC_CHECK, PW: $MC_PW_CHECK, Key: $MC_KEY_CHECK${RESET}]"
    fi
else
    echo -e "  [${YELLOW}STANDBY / AUSGESCHALTET${RESET}] 'outerheaven' schläft plangemäß (On-Demand Compute)."
fi

echo ""
echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${GREEN}   Diagnose abgeschlossen.${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
