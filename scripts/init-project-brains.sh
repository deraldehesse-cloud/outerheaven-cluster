#!/usr/bin/env bash
# ==============================================================================
# 🧠 Projekt-Gehirn Rollout & Initialisierungs-Tool
# ==============================================================================
# Scannt /mnt/storage-beta nach Projektverzeichnissen und initialisiert
# für jeden Projektordner ein standardisiertes _PROJECT_BRAIN.md, falls noch
# keines existiert.
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

TARGET_HOST="fox"
REMOTE_STORAGE="/mnt/storage-beta"
TEMPLATE_FILE="$(dirname "$0")/../PROJECT_BRAIN_TEMPLATE.md"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🧠 PROJEKT-GEHIRN INITIALISIERUNG AUF STORAGE-BETA ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"

# 1. Konnektivität & NFS-Mount prüfen
echo -e "\n${BOLD}[1/2] Prüfe Erreichbarkeit von $REMOTE_STORAGE via $TARGET_HOST...${RESET}"

if ! ssh -o ConnectTimeout=3 -o BatchMode=yes "$TARGET_HOST" "true" 2>/dev/null; then
    echo -e "  [${RED}FEHLER${RESET}] Node '$TARGET_HOST' ist nicht erreichbar!"
    exit 1
fi

IS_MOUNTED=$(ssh "$TARGET_HOST" "timeout 3 test -d '$REMOTE_STORAGE' && echo 'YES' || echo 'NO'")

if [ "$IS_MOUNTED" != "YES" ]; then
    echo -e "  [${YELLOW}OFFLINE${RESET}] $REMOTE_STORAGE ist aktuell nicht erreichbar."
    echo -e "  Grund: Outer Heaven schläft oder der NFS-Server ist offline."
    echo -e "  ${YELLOW}Aktion abgebrochen. Bitte starte Outer Heaven, bevor du Projekt-Gehirne initialisierst.${RESET}"
    exit 0
fi

echo -e "  [${GREEN}ONLINE${RESET}] $REMOTE_STORAGE ist eingehängt und erreichbar."

# 2. Ordner scannen & Brains anlegen
echo -e "\n${BOLD}[2/2] Scanne Projektordner und initialisiere Projekt-Gehirne...${RESET}"

PROJECT_DIRS=$(ssh "$TARGET_HOST" "find '$REMOTE_STORAGE' -maxdepth 1 -mindepth 1 -type d ! -name '.*' -printf '%f\n'")

if [ -z "$PROJECT_DIRS" ]; then
    echo -e "  [${YELLOW}INFO${RESET}] Keine Unterordner in $REMOTE_STORAGE gefunden."
    exit 0
fi

TODAY=$(date "+%Y-%m-%d")

while IFS= read -r DIRNAME; do
    [ -z "$DIRNAME" ] && continue
    echo -n "  Projekt '$DIRNAME': "

    BRAIN_EXISTS=$(ssh "$TARGET_HOST" "test -f '$REMOTE_STORAGE/$DIRNAME/_PROJECT_BRAIN.md' && echo 'YES' || echo 'NO'")

    if [ "$BRAIN_EXISTS" = "YES" ]; then
        echo -e "[${GREEN}BEREITS VORHANDEN${RESET}]"
    else
        echo -n "Erstelle _PROJECT_BRAIN.md... "
        ssh "$TARGET_HOST" "cat << 'EOF' > '$REMOTE_STORAGE/$DIRNAME/_PROJECT_BRAIN.md'
# 🧠 Projekt-Gehirn: $DIRNAME

---

## 🎯 1. Projekt-Fokus & Zweck
* **Ziel des Projekts:** [Zweck dieses Ordners definieren]
* **Primäre Deliverables:** [Ergebnisse und Artefakte]
* **Verantwortlicher Node:** Outer Heaven / Fox

---

## 📊 2. Aktueller Status & Meilensteine
* **Status:** 🟡 In Arbeit
* **Letztes Update:** $TODAY
* **Aktuelle Phase:** Initialisierung
* **Nächste konkrete Schritte:**
  1. Anforderungen und Rohdaten definieren
  2. Erste Tests / Berechnungen starten

---

## 🧩 3. Benötigte Assets, Modelle & Werkzeuge
* **ComfyUI Checkpoints:** [Checkpoints auf /mnt/fox-assets]
* **LoRAs / Embeddings:** [LoRAs angeben]
* **Workflows:** [Workflow-Dateien]

---

## 📜 4. Spezifische Arbeitsregeln & Einschränkungen
* **Unantastbare Dateien:** Keine
* **Sprachregel:** Sämtliche Dokumentationen und Notizen erfolgen auf Deutsch.

---

## 📝 5. Changelog & Arbeits-Historie
* **$TODAY (Antigravity):** Projekt-Gehirn initialisiert.
EOF"
        echo -e "[${GREEN}NEU ANGELEGT${RESET}]"
    fi
done <<< "$PROJECT_DIRS"

echo -e "\n${BOLD}${GREEN}✔ Projekt-Gehirn Initialisierung abgeschlossen!${RESET}\n"
