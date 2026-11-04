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
echo -e "${BOLD}${BLUE}1. Prüfung Node 'fox' (100.93.184.87)...${RESET}"

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

    # Hermes Agent Service (:8081)
    HERMES_AGENT_STATUS=$(ssh fox "systemctl --user is-active hermes-agent.service 2>/dev/null || echo 'inactive'")
    if [ "$HERMES_AGENT_STATUS" = "active" ]; then
        HERMES_BIND=$(ssh fox "ss -tuln | grep ':8081 ' | awk '{print \$5}' | tr '\n' ' '")
        echo -e "  Hermes Agent (:8081):   [${GREEN}AKTIV / RUNNING${RESET}] (Binding: $HERMES_BIND)"
    else
        echo -e "  Hermes Agent (:8081):   [${YELLOW}$HERMES_AGENT_STATUS${RESET}]"
    fi

    # Fox Research Agent Service (:5005)
    RESEARCH_STATUS=$(ssh fox "systemctl --user is-active fox-research-agent.service 2>/dev/null || echo 'inactive'")
    if [ "$RESEARCH_STATUS" = "active" ]; then
        RESEARCH_BIND=$(ssh fox "ss -tuln | grep ':5005 ' | awk '{print \$5}' | tr '\n' ' '")
        echo -e "  Fox Research Agent (:5005):[${GREEN}AKTIV / RUNNING${RESET}] (Binding: $RESEARCH_BIND)"
    else
        echo -e "  Fox Research Agent (:5005):[${YELLOW}$RESEARCH_STATUS${RESET}]"
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

    # Power Management (On-Demand via cluster-power.sh / WOL-Ready)
    WOL_ETH=$(ssh fox "sudo ethtool eno1 2>/dev/null | awk '/^[[:space:]]*Wake-on:[[:space:]]/ {print \$2}' || echo 'n/a'")
    RTC_WAKE=$(ssh fox "cat /sys/class/rtc/rtc0/device/power/wakeup 2>/dev/null || echo 'n/a'")
    OLD_CRON=$(ssh fox "sudo crontab -l 2>/dev/null | grep -c 'fox-power-schedule.sh' || echo '0'")
    if [ "$OLD_CRON" -eq "0" ]; then
        echo -e "  Power-Modus:            [${GREEN}ON-DEMAND (Entkoppelt)${RESET}] (WOL eno1: $WOL_ETH | RTC: $RTC_WAKE | Alter 00:00-Cron: Deaktiviert)"
    else
        echo -e "  Power-Modus:            [${YELLOW}ALTER CRON NOCH AKTIV${RESET}] (fox-power-schedule.sh sollte aus Root-Crontab entfernt werden)"
    fi

else
    echo -e "  [${RED}OFFLINE${RESET}] 'fox' ist über SSH/Tailscale nicht erreichbar!"
fi

echo ""

# ------------------------------------------------------------------------------
# 2. PRÜFUNG: COMPUTE NODE 'OUTER HEAVEN' (On-Demand)
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}2. Prüfung Node 'outerheaven' (100.116.38.29)...${RESET}"

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
        MODELS_OH=$(ssh outerheaven "ollama list 2>/dev/null | grep -E 'megan|deepseek-r1' || echo 'Keine Modelle gefunden'")
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

    # Mission Control Dashboard Check (:8000)
    MC_STATUS=$(ssh outerheaven "systemctl --user is-active mission-control.service 2>/dev/null || echo 'inactive'")
    if [ "$MC_STATUS" = "active" ]; then
        MC_BIND=$(ssh outerheaven "ss -tuln | grep ':8000 ' | awk '{print \$5}' | tr '\n' ' '")
        echo -e "  Mission Control (:8000):[${GREEN}AKTIV / RUNNING${RESET}] (Binding: $MC_BIND)"
    else
        echo -e "  Mission Control (:8000):[${YELLOW}$MC_STATUS${RESET}]"
    fi

    # Megan Studio Autonomous Service Check (:8080)
    MEGAN_STATUS=$(ssh outerheaven "systemctl --user is-active megan-studio.service 2>/dev/null || echo 'inactive'")
    if [ "$MEGAN_STATUS" = "active" ]; then
        MEGAN_BIND=$(ssh outerheaven "ss -tuln | grep ':8080 ' | awk '{print \$5}' | tr '\n' ' '")
        echo -e "  Megan Studio (:8080):   [${GREEN}AKTIV / RUNNING${RESET}] (Binding: $MEGAN_BIND)"
    else
        echo -e "  Megan Studio (:8080):   [${YELLOW}$MEGAN_STATUS${RESET}]"
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

    # Wake-on-LAN für Fox (10:00 Uhr Cronjob)
    OH_WOL_CRON=$(ssh outerheaven "crontab -l 2>/dev/null | grep -q 'wakeonlan' && echo 'AKTIV' || echo 'FEHLT'")
    OH_WOL_BIN=$(ssh outerheaven "command -v wakeonlan >/dev/null 2>&1 && echo 'INSTALLIERT' || echo 'NICHT GEFUNDEN'")
    if [ "$OH_WOL_CRON" = "AKTIV" ] && [ "$OH_WOL_BIN" = "INSTALLIERT" ]; then
        echo -e "  WOL-Sender für Fox:     [${GREEN}AKTIV / BEREIT${RESET}] (Täglich um 10:00 Uhr -> ec:b1:d7:52:35:56)"
    else
        echo -e "  WOL-Sender für Fox:     [${YELLOW}WARNUNG${RESET}] (Binary: $OH_WOL_BIN, Cron: $OH_WOL_CRON)"
    fi
else
    echo -e "  [${YELLOW}STANDBY / AUSGESCHALTET${RESET}] 'outerheaven' schläft plangemäß (On-Demand Compute)."
fi

echo ""
echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${GREEN}   Diagnose abgeschlossen.${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
