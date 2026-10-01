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
echo -e "${BOLD}${CYAN}   💾  OUTER HEAVEN // CHECKPOINT & SAVEGAME MANAGER                 ${RESET}"
echo -e "${BOLD}${CYAN}======================================================================${RESET}"
echo -e " Lokales Repo:  ${BOLD}${REPO_DIR}${RESET}"
echo -e " Branch:        ${BOLD}${MAGENTA}${CURRENT_BRANCH}${RESET}"
echo -e " Zeitstempel:   ${TIMESTAMP}"
echo -e "${CYAN}----------------------------------------------------------------------${RESET}"

# 0. Outer Heaven ComfyUI Checkpoints & Speicherauslastung
echo -e "${BOLD}${CYAN}📦 ComfyUI Checkpoints & Speicherauslastung (Outer Heaven):${RESET}"
OH_CP_REMOTE="/home/outerheaven/ComfyUI/models/checkpoints/"
OH_CP_LOCAL="/mnt/storage-beta/models (comfy)/checkpoints"

if [ -d "${OH_CP_LOCAL}" ]; then
    DISK_INFO=$(df -h "${OH_CP_LOCAL}" | awk 'NR==2 {print "Gesamt: " $2 ", Belegt: " $3 " (" $5 "), Frei: " $4}')
    CP_COUNT=$(find "${OH_CP_LOCAL}" -maxdepth 2 -type f \( -name "*.safetensors" -o -name "*.ckpt" \) 2>/dev/null | wc -l)
    echo -e " Pfad (Remote): ${BOLD}${OH_CP_REMOTE}${RESET}"
    echo -e " Pfad (NFS):    ${BOLD}${OH_CP_LOCAL}${RESET}"
    echo -e " Speicher:      ${GREEN}${DISK_INFO}${RESET}"
    echo -e " Checkpoints:   ${BOLD}${CP_COUNT} Modelle gefunden${RESET}"
    
    # Zeige die wichtigsten aktiven Checkpoints
    echo -e " Top-Modelle:   ${CYAN}$(ls -1 "${OH_CP_LOCAL}"/*.safetensors 2>/dev/null | head -n 4 | xargs -n 1 basename | paste -sd ', ' -)${RESET}"
else
    echo -e " ${YELLOW}⚠ NFS-Pfad ${OH_CP_LOCAL} nicht eingehängt. Prüfe via SSH...${RESET}"
    if ssh -o BatchMode=yes -o ConnectTimeout=3 outerheaven 'true' 2>/dev/null; then
        REMOTE_DISK=$(ssh -o BatchMode=yes -o ConnectTimeout=3 outerheaven 'df -h /mnt/storage-beta 2>/dev/null | awk "NR==2 {print \$2 \" gesamt, \" \$4 \" frei (\" \$5 \" belegt)\"}"' 2>/dev/null || echo "k.A.")
        echo -e " Remote-Speicher (/mnt/storage-beta): ${GREEN}${REMOTE_DISK}${RESET}"
        echo -e " Pfad: ${BOLD}${OH_CP_REMOTE}${RESET}"
    else
        echo -e " ${RED}✖ Outer Heaven offline oder SSH nicht erreichbar.${RESET}"
    fi
fi
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
echo -e " [${BOLD}${YELLOW}O${RESET}]  ComfyUI Checkpoints-Ordner im Dateimanager öffnen"
echo -e " [${BOLD}${YELLOW}L${RESET}]  Alle ComfyUI Checkpoint-Dateien detailliert auflisten"
echo -e " [${BOLD}${YELLOW}R${RESET}]  Rollback auf letzten Git-Checkpoint (Wiederherstellung)"
echo -e " [${BOLD}Enter${RESET}] Beenden & Fenster schließen"
echo -e "${CYAN}----------------------------------------------------------------------${RESET}"
read -rp "Auswahl: " -n 1 ACTION
echo ""

if [[ "${ACTION}" =~ ^[Oo]$ ]]; then
    if [ -d "${OH_CP_LOCAL}" ]; then
        echo -e "${GREEN}Öffne ${OH_CP_LOCAL}...${RESET}"
        xdg-open "${OH_CP_LOCAL}" >/dev/null 2>&1 &
    else
        echo -e "${RED}NFS-Pfad ${OH_CP_LOCAL} nicht verfügbar.${RESET}"
    fi
elif [[ "${ACTION}" =~ ^[Ll]$ ]]; then
    echo -e "\n${BOLD}${CYAN}Aktuelle ComfyUI Checkpoints auf Outer Heaven:${RESET}"
    if [ -d "${OH_CP_LOCAL}" ]; then
        ls -lh "${OH_CP_LOCAL}" | grep -E '\.(safetensors|ckpt)$' | awk '{print $9, "("$5")"}'
    else
        ssh outerheaven 'ls -lh /home/outerheaven/ComfyUI/models/checkpoints/' | grep -E '\.(safetensors|ckpt)$' | awk '{print $9, "("$5")"}'
    fi
elif [[ "${ACTION}" =~ ^[Rr]$ ]]; then
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
