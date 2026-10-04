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
#   3. Rsync für Mission Control -> outerheaven (/home/outerheaven/mission-control/).
#   4. Rsync für Skripte & Configs -> fox (/home/fox/scripts/ & ~/.hermes/).
#   5. Sanfter Service-Restart via SSH (mission-control & hermes-gateway).
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

# Pfade und Ziel-Hosts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

OH_HOST="outerheaven"
OH_IP="100.116.38.29"
OH_TARGET_MC="/home/outerheaven/mission-control/"
OH_TARGET_STREAMLIT="/home/outerheaven/streamlit-apps/"

FOX_HOST="fox"
FOX_IP="100.93.184.87"
FOX_TARGET_SCRIPTS="/home/fox/scripts/"
FOX_TARGET_HERMES="/home/fox/.hermes/"

# Status-Flags für Zusammenfassung
OH_ONLINE=0
FOX_ONLINE=0
OH_SYNC_OK=0
OH_STREAMLIT_OK=0
FOX_SYNC_OK=0
OH_RESTART_OK=0
OH_DASH_RESTART_OK=0
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
# 3. RSYNC MISSION CONTROL & STREAMLIT APPS -> OUTER HEAVEN
# ==============================================================================
log_step "3/5" "Synchronisiere Mission Control & Streamlit-Apps -> outerheaven..."

if [ "${OH_ONLINE}" -eq 1 ]; then
    # 3a. Mission Control synchronisieren
    if [ -d "${REPO_DIR}/mission-control" ]; then
        log_info "Übertrage /mission-control/ nach ${OH_HOST}:${OH_TARGET_MC}..."
        
        # Rsync-Optionen: Archiv, Komprimierung, Verbose.
        # Wichtig: Secrets, .env, Session-Dateien und Output-Caches auf dem Ziel schützen!
        if rsync -avz \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            --exclude '.env' \
            --exclude 'secrets/' \
            --exclude 'outputs/' \
            --exclude 'data/object_info_cache.json' \
            "${REPO_DIR}/mission-control/" "${OH_HOST}:${OH_TARGET_MC}"; then
            log_ok "Mission Control erfolgreich nach outerheaven synchronisiert."
            OH_SYNC_OK=1
        else
            log_fail "Rsync zu outerheaven (Mission Control) fehlgeschlagen!"
        fi
    else
        log_fail "Verzeichnis '${REPO_DIR}/mission-control' existiert lokal nicht."
    fi

    # 3b. Streamlit Apps (Leitstand) synchronisieren
    if [ -d "${REPO_DIR}/streamlit-apps" ]; then
        log_info "Übertrage /streamlit-apps/ nach ${OH_HOST}:${OH_TARGET_STREAMLIT}..."
        ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" "mkdir -p ${OH_TARGET_STREAMLIT}" 2>/dev/null || true
        if rsync -avz \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            --exclude 'venv/' \
            "${REPO_DIR}/streamlit-apps/" "${OH_HOST}:${OH_TARGET_STREAMLIT}"; then
            log_ok "Streamlit-Apps (Leitstand) erfolgreich nach outerheaven synchronisiert."
            OH_STREAMLIT_OK=1
        else
            log_fail "Rsync zu outerheaven (Streamlit-Apps) fehlgeschlagen!"
        fi
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
log_step "5/5" "Triggere Daemon-Reload & sanften Service-Neustart..."

# 5a. Outer Heaven: mission-control.service neu starten
if [ "${OH_SYNC_OK}" -eq 1 ]; then
    log_info "Starte mission-control.service auf outerheaven neu..."
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" 'systemctl --user daemon-reload && systemctl --user restart mission-control.service'; then
        sleep 1
        OH_SVC_STATE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'systemctl --user is-active mission-control.service 2>/dev/null || echo "unknown"')
        if [ "${OH_SVC_STATE}" = "active" ]; then
            log_ok "mission-control.service auf outerheaven läuft (active)."
            OH_RESTART_OK=1
        else
            log_warn "mission-control.service Status auf outerheaven: ${OH_SVC_STATE}"
        fi
    else
        log_fail "Fehler beim Neustarten von mission-control.service auf outerheaven!"
    fi
fi

# 5b. Outer Heaven: hermes-dashboard.service neu starten (falls Streamlit Apps übertragen wurden)
if [ "${OH_STREAMLIT_OK}" -eq 1 ]; then
    log_info "Starte hermes-dashboard.service auf outerheaven neu..."
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${OH_HOST}" 'systemctl --user daemon-reload && systemctl --user restart hermes-dashboard.service'; then
        sleep 1
        OH_DASH_STATE=$(ssh -o BatchMode=yes -o ConnectTimeout=3 "${OH_HOST}" 'systemctl --user is-active hermes-dashboard.service 2>/dev/null || echo "unknown"')
        if [ "${OH_DASH_STATE}" = "active" ]; then
            log_ok "hermes-dashboard.service auf outerheaven läuft (active)."
            OH_DASH_RESTART_OK=1
        else
            log_warn "hermes-dashboard.service Status auf outerheaven: ${OH_DASH_STATE}"
        fi
    else
        log_fail "Fehler beim Neustarten von hermes-dashboard.service auf outerheaven!"
    fi
fi

# 5c. Fox: hermes-gateway.service neu starten (falls aktiv)
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
if [ "${OH_ONLINE}" -eq 1 ] && [ "${OH_SYNC_OK}" -eq 1 ] && [ "${OH_RESTART_OK}" -eq 1 ]; then
    echo -e " Node Outer Heaven (HP Z620): [ ${GREEN}${BOLD}ERFOLGREICH SYNCHRONISIERT & NEU GESTARTET${RESET} ]"
    if [ "${OH_STREAMLIT_OK}" -eq 1 ]; then
        if [ "${OH_DASH_RESTART_OK}" -eq 1 ]; then
            echo -e "   └── Streamlit Leitstand:   [ ${GREEN}SYNCHRONISIERT & NEU GESTARTET${RESET} ]"
        else
            echo -e "   └── Streamlit Leitstand:   [ ${GREEN}SYNCHRONISIERT${RESET} ]"
        fi
    fi
elif [ "${OH_ONLINE}" -eq 0 ]; then
    echo -e " Node Outer Heaven (HP Z620): [ ${RED}${BOLD}OFFLINE / NICHT ERREICHBAR${RESET} ]"
else
    echo -e " Node Outer Heaven (HP Z620): [ ${YELLOW}${BOLD}TEILWEISE FEHLGESCHLAGEN${RESET} ]"
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

# Warten auf Tastenabschluss
echo ""
read -p "Taste drücken zum Schließen..." || true
