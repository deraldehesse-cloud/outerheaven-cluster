#!/usr/bin/env bash
# ==============================================================================
# 💾 OUTER HEAVEN // ONE-CLICK GIT CHECKPOINT (SAVEGAME)
# ==============================================================================
# Host:         bigboss (Laptop / Ubuntu Studio)
# Repo:         /home/bigboss/outerheaven-cluster-repo/
#
# Aufgaben:
#   1. Prüft, ob im Repository ungespeicherte Änderungen vorliegen (git status --porcelain).
#   2. Wenn Änderungen vorhanden sind:
#      - Fragt nach einer optionalen Notiz oder nutzt automatischen Zeitstempel.
#      - Sichert alles via 'git add -A' und 'git commit'.
#      - Bestätigt in grün: "✔ Checkpoint erfolgreich gesichert".
#   3. Wenn keine Änderungen vorhanden sind:
#      - Meldet: "ℹ Repository ist sauber. Aktueller Stand ist gesichert."
#   4. Bietet Rollback-Option [R] an (git restore / reset).
#   5. Hält das Terminalfenster bis zum Tastendruck offen.
# ==============================================================================

set -uo pipefail

# ANSI Farbcodes
GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
MAGENTA="\033[95m"
BOLD="\033[1m"
RESET="\033[0m"

# Pfade ermitteln
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}" || {
    echo -e "${BOLD}${RED}Fehler: Konnte Verzeichnis ${REPO_DIR} nicht betreten.${RESET}"
    read -rp "Drücke [Enter] zum Beenden..."
    exit 1
}

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unbekannt')"
TIMESTAMP="$(date '+%Y-%m-%d_%H:%M:%S')"

clear 2>/dev/null || true
echo -e "${BOLD}${CYAN}======================================================================${RESET}"
echo -e "${BOLD}${CYAN}   💾  OUTER HEAVEN // ONE-CLICK GIT CHECKPOINT (SAVEGAME)            ${RESET}"
echo -e "${BOLD}${CYAN}======================================================================${RESET}"
echo -e " Lokales Repo:  ${BOLD}${REPO_DIR}${RESET}"
echo -e " Branch:        ${BOLD}${MAGENTA}${CURRENT_BRANCH}${RESET}"
echo -e " Zeitstempel:   ${TIMESTAMP}"
echo -e "${CYAN}----------------------------------------------------------------------${RESET}"

# 1. Prüfe auf Änderungen im Repo
CHANGES="$(git status --porcelain)"

if [[ -n "${CHANGES}" ]]; then
    echo -e "${BOLD}${YELLOW}⚡ Ungespeicherte Änderungen erkannt:${RESET}\n"
    git status --short
    echo ""
    echo -e "${CYAN}----------------------------------------------------------------------${RESET}"
    echo -e "${BOLD}Möchtest du dem Checkpoint eine kurze Notiz beifügen?${RESET}"
    read -rp "Notiz [Enter für Standard-Zeitstempel]: " USER_NOTE

    if [[ -n "${USER_NOTE// }" ]]; then
        COMMIT_MSG="Checkpoint vor Task: ${TIMESTAMP} - ${USER_NOTE}"
    else
        COMMIT_MSG="Checkpoint vor Task: ${TIMESTAMP}"
    fi

    echo -e "\n${BOLD}${CYAN}⏳ Erstelle Checkpoint...${RESET}"
    git add -A
    
    if git commit -m "${COMMIT_MSG}" > /dev/null 2>&1; then
        COMMIT_HASH="$(git rev-parse --short HEAD)"
        echo -e "${BOLD}${GREEN}✔ Checkpoint erfolgreich gesichert: [${COMMIT_HASH}] ${COMMIT_MSG}${RESET}"
    else
        echo -e "${BOLD}${RED}✘ Fehler beim Erstellen des Commits!${RESET}"
    fi

    echo -e "\n${BOLD}Aktuelle Historie (letzte 3 Checkpoints):${RESET}"
    git log --oneline -n 3
else
    echo -e "${BOLD}${GREEN}ℹ Repository ist sauber. Aktueller Stand ist gesichert.${RESET}"
    echo -e "Letzter Checkpoint: ${BOLD}$(git log -n 1 --oneline 2>/dev/null || echo 'Keine Commits')${RESET}"
fi

echo -e "\n${CYAN}======================================================================${RESET}"
echo -e "${BOLD}Aktionen:${RESET}"
echo -e " [${BOLD}${YELLOW}R${RESET}]  Rollback auf letzten Checkpoint (Wiederherstellung)"
echo -e " [${BOLD}Enter${RESET}] Beenden & Fenster schließen"
echo -e "${CYAN}----------------------------------------------------------------------${RESET}"
read -rp "Auswahl: " -n 1 ACTION
echo ""

if [[ "${ACTION}" =~ ^[Rr]$ ]]; then
    echo -e "\n${BOLD}${YELLOW}⚠️  ROLLBACK-MENÜ:${RESET}"
    echo -e " 1) Letzten Commit rückgängig machen (git reset --hard HEAD~1)"
    echo -e " 2) Alle ungespeicherten Dateien verwerfen (git restore . && git clean -fd)"
    echo -e " 3) Abbrechen"
    read -rp "Wähle Option [1-3]: " ROLLBACK_OPT
    
    case "${ROLLBACK_OPT}" in
        1)
            LAST_COMMIT="$(git log -n 1 --oneline)"
            read -rp "Wirklich letzten Commit (${LAST_COMMIT}) verwerfen? [j/N]: " CONFIRM
            if [[ "${CONFIRM}" =~ ^[jJyY]$ ]]; then
                git reset --hard HEAD~1
                echo -e "${BOLD}${GREEN}✔ Erfolgreich zurückgesetzt auf: $(git log -n 1 --oneline)${RESET}"
            else
                echo -e "${YELLOW}Rollback abgebrochen.${RESET}"
            fi
            ;;
        2)
            read -rp "Wirklich alle ungespeicherten lokalen Änderungen verwerfen? [j/N]: " CONFIRM_RESTORE
            if [[ "${CONFIRM_RESTORE}" =~ ^[jJyY]$ ]]; then
                git restore . 2>/dev/null || true
                git clean -fd 2>/dev/null || true
                echo -e "${BOLD}${GREEN}✔ Arbeitsverzeichnis sauber wiederhergestellt.${RESET}"
            else
                echo -e "${YELLOW}Vorgang abgebrochen.${RESET}"
            fi
            ;;
        *)
            echo -e "${YELLOW}Keine Aktion durchgeführt.${RESET}"
            ;;
    esac
fi

echo -e "\n${BOLD}${CYAN}Savegame-Manager beendet.${RESET}"
read -rp "Drücke [Enter] zum Schließen des Fensters..."
