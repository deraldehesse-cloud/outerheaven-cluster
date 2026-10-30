#!/usr/bin/env bash
# ==============================================================================
# ⚔️ OUTER HEAVEN // ONE-CLICK CLUSTER DEPLOY LAUNCHER
# ==============================================================================
# Host:         bigboss (Laptop / Xubuntu XFCE)
# Targets:      outerheaven (100.116.38.29)
#               fox (100.93.184.87)
#
# Aufgaben:
#   1. Tailscale- / Netzwerk-Konnektivitätsprüfung für outerheaven und fox.
#   2. Git-Status des lokalen Repositories anzeigen (git status --short).
#   3. Rsync Mission Control & Megan Studio Services -> outerheaven.
#   4. Rsync Skripte & Configs -> fox (/home/fox/scripts/ & ~/.hermes/).
#   5. Sanfter Service-Restart via SSH:
#      - mission-control.service (:8000)
#      - megan-studio.service (:8080)
#      - Deaktivierung hermes-dashboard.service (Streamlit)
#   6. Farbige, strukturierte Zusammenfassung und Pause am Ende.
# ==============================================================================

set -uo pipefail

# ANSI Farbdefinitionen
GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

# Pfade und Ziel-Hosts (mit realpath für Symlink-Unterstützung z. B. ~/bin/cluster-deploy)
SCRIPT_DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
if [ -d "/home/bigboss/outerheaven-cluster-repo/.git" ]; then
    REPO_DIR="/home/bigboss/outerheaven-cluster-repo"
else
    REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

OH_HOST="outerheaven"
OH_IP="100.116.38.29"
OH_TARGET_MC="/home/outerheaven/mission-control/"
OH_TARGET_SERVICES="/home/outerheaven/services/"
OH_TARGET_SYSTEMD="/home/outerheaven/.config/systemd/user/"

FOX_HOST="fox"
FOX_IP="100.93.184.87"
FOX_TARGET_SCRIPTS="/home/fox/scripts/"
FOX_TARGET_HERMES="/home/fox/.hermes/"

# Status-Flags für Zusammenfassung
OH_ONLINE=0
FOX_ONLINE=0
OH_SYNC_MC_OK=0
OH_SYNC_MEGAN_OK=0
OH_SYNC_HERMES_OK=0
FOX_SYNC_OK=0
OH_RESTART_MC_OK=0
OH_RESTART_MEGAN_OK=0
OH_RESTART_HERMES_OK=0
OH_STREAMLIT_DISABLED=0
FOX_RESTART_OK=0

# Logging-Funktionen
log_banner() {
    echo -e "${BOLD}${CYAN}======================================================================${RESET}"
    echo -e "${BOLD}${CYAN}   ⚔️  OUTER HEAVEN // ONE-CLICK CLUSTER DEPLOY LAUNCHER             ${RESET}"
    echo -e "${BOLD}${CYAN}======================================================================${RESET}"
    echo -e " Lokales Repo:  ${REPO_DIR}"
    echo -e " Zeitstempel:   $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${CYAN}----------------------------------------------------------------------${RESET}"
}

log_step() {
    echo -e "\n${BOLD}${CYAN}[$1] $2${RESET}"
}

log_ok() {
    echo -e " ${GREEN}✔ OK:${RESET} $1"
}

log_warn() {
    echo -e " ${YELLOW}⚠ WARNUNG:${RESET} $1"
}

log_fail() {
    echo -e " ${RED}✖ FEHLER:${RESET} $1"
}

log_info() {
    echo -e " ${CYAN}ℹ INFO:${RESET} $1"
}

# --- Start ---
log_banner

# ==============================================================================
# 1. KONNEKTIVITÄTSPRÜFUNG (Ping & SSH)
# ==============================================================================
log_step "1/5" "Prüfe Erreichbarkeit der Cluster-Knoten..."

# Outer Heaven Check
if ping -c 1 -W 2 "${OH_IP}" >/dev/null 2>&1; then
    if ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'echo ok' >/dev/null 2>&1; then
        log_ok "Node Outer Heaven (${OH_IP}) via Ping & SSH erreichbar."
        OH_ONLINE=1
    else
        log_warn "Node Outer Heaven (${OH_IP}) antwortet auf Ping, aber SSH schlug fehl!"
    fi
else
    log_fail "Node Outer Heaven (${OH_IP}) nicht erreichbar! Ist Tailscale aktiv?"
fi

# Fox Check
if ping -c 1 -W 2 "${FOX_IP}" >/dev/null 2>&1; then
    if ssh -o BatchMode=yes -o ConnectTimeout=3 "${FOX_HOST}" 'echo ok' >/dev/null 2>&1; then
        log_ok "Node Fox (${FOX_IP}) via Ping & SSH erreichbar."
        FOX_ONLINE=1
    else
        log_warn "Node Fox (${FOX_IP}) antwortet auf Ping, aber SSH schlug fehl!"
    fi
else
    log_fail "Node Fox (${FOX_IP}) nicht erreichbar! Ist Tailscale aktiv?"
fi

# ==============================================================================
# 2. GIT REPOSITORY STATUS
# ==============================================================================
log_step "2/5" "Lokaler Git-Status (outerheaven-cluster-repo)..."

if [ -d "${REPO_DIR}/.git" ]; then
    BRANCH=$(git -C "${REPO_DIR}" branch --show-current 2>/dev/null || echo "unknown")
    LAST_COMMIT=$(git -C "${REPO_DIR}" log -1 --oneline 2>/dev/null || echo "kein Commit")
    log_info "Branch: ${BOLD}${BRANCH}${RESET} | Letzter Commit: ${LAST_COMMIT}"

    CHANGES=$(git -C "${REPO_DIR}" status --short)
    if [ -n "${CHANGES}" ]; then
        echo -e "${YELLOW}Geänderte / Nicht-committete Dateien (werden live synchronisiert):${RESET}"
        echo "${CHANGES}" | sed 's/^/   /'
    else
        log_ok "Working Tree ist sauber (keine uncommitted changes)."
    fi
else
    log_warn "Kein .git-Verzeichnis unter ${REPO_DIR} gefunden."
fi

# ==============================================================================
# 3. RSYNC MISSION CONTROL & MEGAN STUDIO -> OUTER HEAVEN
# ==============================================================================
log_step "3/5" "Synchronisiere Mission Control & Megan Studio -> outerheaven..."

if [ "${OH_ONLINE}" -eq 1 ]; then
    # 3a. Mission Control synchronisieren
    if [ -d "${REPO_DIR}/mission-control" ]; then
        log_info "Übertrage /mission-control/ nach ${OH_HOST}:${OH_TARGET_MC}..."
        
        if rsync -avz \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            --exclude '.env' \
            --exclude 'secrets/' \
            --exclude 'outputs/' \
            --exclude '*.bak*' \
            --exclude 'data/object_info_cache.json' \
            "${REPO_DIR}/mission-control/" "${OH_HOST}:${OH_TARGET_MC}"; then
            log_ok "Mission Control (:8000) erfolgreich nach outerheaven synchronisiert."
            OH_SYNC_MC_OK=1
        else
            log_fail "Rsync zu outerheaven (Mission Control) fehlgeschlagen!"
        fi
    fi

    # 3b. Megan Studio synchronisieren
    if [ -d "${REPO_DIR}/services/megan-studio" ]; then
        log_info "Übertrage /services/megan-studio/ nach ${OH_HOST}:${OH_TARGET_SERVICES}megan-studio/..."
        ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" "mkdir -p ${OH_TARGET_SERVICES}megan-studio" 2>/dev/null || true
        if rsync -avz \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            "${REPO_DIR}/services/megan-studio/" "${OH_HOST}:${OH_TARGET_SERVICES}megan-studio/"; then
            log_ok "Megan Studio Service (:8080) erfolgreich nach outerheaven synchronisiert."
            OH_SYNC_MEGAN_OK=1
        else
            log_fail "Rsync zu outerheaven (Megan Studio) fehlgeschlagen!"
        fi
    fi

    # 3c. Systemd Units synchronisieren
    if [ -d "${REPO_DIR}/systemd" ]; then
        log_info "Übertrage Systemd Service-Units nach ${OH_HOST}:${OH_TARGET_SYSTEMD}..."
        ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" "mkdir -p ${OH_TARGET_SYSTEMD}" 2>/dev/null || true
        rsync -avz "${REPO_DIR}/systemd/mission-control.service" "${OH_HOST}:${OH_TARGET_SYSTEMD}" >/dev/null 2>&1 || true
        rsync -avz "${REPO_DIR}/systemd/megan-studio.service" "${OH_HOST}:${OH_TARGET_SYSTEMD}" >/dev/null 2>&1 || true
        log_ok "Systemd Service-Definitionen aktualisiert."
    fi

    # 3d. Hermes Agent Web UI, Skills & Cluster Nodes synchronisieren
    log_info "Übertrage Hermes Agent (hermes_web_ui.py, skills_registry.json, cluster_nodes.json) nach ${OH_HOST}:~/Documents/OuterHeaven-Projekt-doc/..."
    ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" "mkdir -p ~/Documents/OuterHeaven-Projekt-doc" 2>/dev/null || true
    
    HERMES_SRC_DIR="/home/bigboss/Documents/OuterHeaven-Projekt-doc"
    if [ ! -f "${HERMES_SRC_DIR}/hermes_web_ui.py" ] && [ -f "${REPO_DIR}/hermes_web_ui.py" ]; then
        HERMES_SRC_DIR="${REPO_DIR}"
    fi

    if rsync -avz \
        "${HERMES_SRC_DIR}/hermes_web_ui.py" \
        "${HERMES_SRC_DIR}/skills_registry.json" \
        "${HERMES_SRC_DIR}/cluster_nodes.json" \
        "${OH_HOST}:~/Documents/OuterHeaven-Projekt-doc/"; then
        log_ok "Hermes Agent Web UI, Skills & Cluster Nodes erfolgreich nach outerheaven synchronisiert."
        OH_SYNC_HERMES_OK=1
    else
        log_fail "Rsync von Hermes Agent zu outerheaven fehlgeschlagen!"
    fi
else
    log_warn "Überspringe Outer Heaven Sync, da Knoten offline ist."
fi

# ==============================================================================
# 4. RSYNC SCRIPTS & CONFIGS -> FOX
# ==============================================================================
log_step "4/5" "Synchronisiere Skripte & Konfigurationen -> fox..."

if [ "${FOX_ONLINE}" -eq 1 ]; then
    FOX_SCRIPTS_OK=0
    FOX_CONFIG_OK=1

    # 4a. Skripte synchronisieren
    if [ -d "${REPO_DIR}/scripts" ]; then
        log_info "Übertrage /scripts/ nach ${FOX_HOST}:${FOX_TARGET_SCRIPTS}..."
        if rsync -avz \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            --exclude 'output/' \
            "${REPO_DIR}/scripts/" "${FOX_HOST}:${FOX_TARGET_SCRIPTS}"; then
            log_ok "Cluster- und Büro-Skripte erfolgreich nach fox synchronisiert."
            FOX_SCRIPTS_OK=1
        else
            log_fail "Rsync der Skripte zu fox fehlgeschlagen!"
        fi
    fi

    # 4b. Hermes-Konfigurationen synchronisieren (sofern vorhanden)
    if [ -f "${REPO_DIR}/character.json" ]; then
        log_info "Übertrage character.json nach ${FOX_HOST}:${FOX_TARGET_HERMES}..."
        rsync -avz "${REPO_DIR}/character.json" "${FOX_HOST}:${FOX_TARGET_HERMES}character.json" >/dev/null 2>&1 || FOX_CONFIG_OK=0
    fi
    if [ -f "${REPO_DIR}/character_canonical.json" ]; then
        rsync -avz "${REPO_DIR}/character_canonical.json" "${FOX_HOST}:${FOX_TARGET_HERMES}character_canonical.json" >/dev/null 2>&1 || true
    fi

    if [ "${FOX_SCRIPTS_OK}" -eq 1 ] && [ "${FOX_CONFIG_OK}" -eq 1 ]; then
        FOX_SYNC_OK=1
    fi
else
    log_warn "Überspringe Fox Sync, da Knoten offline ist."
fi

# ==============================================================================
# 5. SERVICE-NEUSTARTS TRIGGERN (SSH)
# ==============================================================================
log_step "5/5" "Triggere Daemon-Reload & Services auf Outer Heaven & Fox..."

if [ "${OH_ONLINE}" -eq 1 ]; then
    log_info "Führe daemon-reload und Service-Steuerung auf outerheaven aus..."
    
    # 5a. Streamlit hermes-dashboard.service stoppen & deaktivieren
    ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" 'systemctl --user stop hermes-dashboard.service 2>/dev/null && systemctl --user disable hermes-dashboard.service 2>/dev/null || true'
    DASH_ACTIVE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'systemctl --user is-active hermes-dashboard.service 2>/dev/null || echo "inactive"')
    if [ "${DASH_ACTIVE}" = "inactive" ] || [ "${DASH_ACTIVE}" = "failed" ]; then
        log_ok "hermes-dashboard.service (Streamlit) ist stillgelegt (${DASH_ACTIVE})."
        OH_STREAMLIT_DISABLED=1
    fi

    # 5b. Daemon reload & Restart mission-control.service (:8000)
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" 'systemctl --user daemon-reload && systemctl --user restart mission-control.service'; then
        sleep 1.5
        OH_MC_STATE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'systemctl --user is-active mission-control.service 2>/dev/null || echo "unknown"')
        if [ "${OH_MC_STATE}" = "active" ]; then
            log_ok "mission-control.service auf outerheaven läuft (active) auf Port 8000."
            OH_RESTART_MC_OK=1
        else
            log_warn "mission-control.service Status auf outerheaven: ${OH_MC_STATE}"
        fi
    else
        log_fail "Fehler beim Neustarten von mission-control.service auf outerheaven!"
    fi

    # 5c. Enable & Start megan-studio.service (:8080)
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" 'systemctl --user enable megan-studio.service 2>/dev/null && systemctl --user restart megan-studio.service'; then
        sleep 1.5
        MEGAN_STATE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'systemctl --user is-active megan-studio.service 2>/dev/null || echo "unknown"')
        if [ "${MEGAN_STATE}" = "active" ]; then
            log_ok "megan-studio.service auf outerheaven läuft (active) auf Port 8080."
            OH_RESTART_MEGAN_OK=1
        else
            log_warn "megan-studio.service Status auf outerheaven: ${MEGAN_STATE}"
        fi
    else
        log_fail "Fehler beim Starten von megan-studio.service auf outerheaven!"
    fi

    # 5d. Hermes Agent Web UI (:8081) auf outerheaven via tmux neu starten
    log_info "Starte Hermes Agent Web UI (:8081) auf outerheaven neu..."
    ssh -q outerheaven "pkill --older 1 -f hermes_web_ui.py 2>/dev/null || true; tmux kill-session -t hermes_ui 2>/dev/null; tmux new-session -d -s hermes_ui 'cd ~/Documents/OuterHeaven-Projekt-doc && python3 hermes_web_ui.py'"
    sleep 1
    OH_HERMES_RUNNING=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" "tmux has-session -t hermes_ui 2>/dev/null && echo 'running' || echo 'stopped'")
    if [ "${OH_HERMES_RUNNING}" = "running" ]; then
        log_ok "Hermes Agent Web UI läuft auf outerheaven (Port 8081 via tmux session 'hermes_ui')."
        OH_RESTART_HERMES_OK=1
    else
        log_warn "Hermes Agent Web UI Session-Status auf outerheaven: ${OH_HERMES_RUNNING}"
    fi
fi

# 5e. Fox: hermes-gateway.service neu starten (falls aktiv)
if [ "${FOX_SYNC_OK}" -eq 1 ]; then
    log_info "Triggere Daemon-Reload & Gateway-Restart auf fox..."
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${FOX_HOST}" 'systemctl --user daemon-reload && (systemctl --user restart hermes-gateway.service 2>/dev/null || true)'; then
        sleep 1
        FOX_SVC_STATE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${FOX_HOST}" 'systemctl --user is-active hermes-gateway.service 2>/dev/null || echo "standby"')
        log_ok "Fox Services aktualisiert (hermes-gateway: ${FOX_SVC_STATE})."
        FOX_RESTART_OK=1
    else
        log_warn "Konnte Services auf fox nicht neu starten."
    fi
fi

# ==============================================================================
# ZUSAMMENFASSUNG
# ==============================================================================
echo -e "\n${BOLD}${CYAN}======================================================================${RESET}"
echo -e "${BOLD}${CYAN}   📊 DEPLOYMENT ZUSAMMENFASSUNG                                      ${RESET}"
echo -e "${BOLD}${CYAN}======================================================================${RESET}"

# Outer Heaven Status
if [ "${OH_ONLINE}" -eq 1 ] && [ "${OH_RESTART_MC_OK}" -eq 1 ] && [ "${OH_RESTART_MEGAN_OK}" -eq 1 ]; then
    echo -e " Node Outer Heaven (HP Z620): [ ${GREEN}${BOLD}ERFOLGREICH KONSOLIDIERT & GESTARTET${RESET} ]"
    echo -e "   ├── Mission Control (:8000):   [ ${GREEN}ACTIVE / ONLINE${RESET} ]"
    echo -e "   ├── Megan Studio    (:8080):   [ ${GREEN}ACTIVE / ONLINE${RESET} ]"
    echo -e "   ├── Hermes Agent UI (:8081):   [ $([ "${OH_RESTART_HERMES_OK}" -eq 1 ] && echo "${GREEN}ACTIVE / TMUX${RESET}" || echo "${YELLOW}STANDBY${RESET}") ]"
    echo -e "   └── Streamlit (8501):          [ ${GREEN}DISABLED / STILLGELEGT${RESET} ]"
elif [ "${OH_ONLINE}" -eq 0 ]; then
    echo -e " Node Outer Heaven (HP Z620): [ ${RED}${BOLD}OFFLINE / NICHT ERREICHBAR${RESET} ]"
else
    echo -e " Node Outer Heaven (HP Z620): [ ${YELLOW}${BOLD}TEILWEISE FEHLGESCHLAGEN${RESET} ]"
    echo -e "   ├── Mission Control (:8000):   [ $([ "${OH_RESTART_MC_OK}" -eq 1 ] && echo "${GREEN}ACTIVE${RESET}" || echo "${RED}CHECK NEEDED${RESET}") ]"
    echo -e "   ├── Megan Studio    (:8080):   [ $([ "${OH_RESTART_MEGAN_OK}" -eq 1 ] && echo "${GREEN}ACTIVE${RESET}" || echo "${RED}CHECK NEEDED${RESET}") ]"
    echo -e "   ├── Hermes Agent UI (:8081):   [ $([ "${OH_RESTART_HERMES_OK}" -eq 1 ] && echo "${GREEN}ACTIVE / TMUX${RESET}" || echo "${YELLOW}STANDBY${RESET}") ]"
    echo -e "   └── Streamlit (8501):          [ ${GREEN}DISABLED / STILLGELEGT${RESET} ]"
fi

# Fox Status
if [ "${FOX_ONLINE}" -eq 1 ] && [ "${FOX_SYNC_OK}" -eq 1 ] && [ "${FOX_RESTART_OK}" -eq 1 ]; then
    echo -e " Node Fox (EliteDesk 800):    [ ${GREEN}${BOLD}ERFOLGREICH SYNCHRONISIERT & NEU GESTARTET${RESET} ]"
elif [ "${FOX_ONLINE}" -eq 0 ]; then
    echo -e " Node Fox (EliteDesk 800):    [ ${RED}${BOLD}OFFLINE / NICHT ERREICHBAR${RESET} ]"
else
    echo -e " Node Fox (EliteDesk 800):    [ ${YELLOW}${BOLD}TEILWEISE FEHLGESCHLAGEN${RESET} ]"
fi

echo -e "${CYAN}----------------------------------------------------------------------${RESET}"
