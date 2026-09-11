#!/usr/bin/env bash
set -e

REPO_DIR="/home/bigboss/outerheaven-cluster-repo"
cd "$REPO_DIR" || exit 1

TASK_FILE=".cursor_tasks.md"

if [ ! -f "$TASK_FILE" ]; then
    echo "[!] Fehler: Keine $TASK_FILE im Repo gefunden. Bitte Spezifikation anlegen."
    exit 1
fi

echo "[+] Wechsle in isolierten Branch 'feature/ag-transfer'..."
git checkout -B feature/ag-transfer

AGENT_BIN=$(command -v cursor-agent || command -v agent || true)
if [ -n "$AGENT_BIN" ]; then
    echo "[✔] Führe Cursor Agent Headless aus..."
    timeout 180s "$AGENT_BIN" -p "$(cat "$TASK_FILE")" || true
else
    echo "[!] Cursor Agent CLI nicht gefunden. Öffne Cursor mit Task-Datei..."
    cursor "$REPO_DIR" "$TASK_FILE"
fi

echo "[*] Ermittle modifizierte Dateien..."
CHANGED=$(git status --porcelain | awk '{print $2}')

if [ -n "$CHANGED" ]; then
    echo "[🔍] Änderungen erkannt. Öffne VS Code zur Sichtprüfung..."
    code "$REPO_DIR" $CHANGED
    echo ""
    echo "=================================================================="
    echo "[AUDIT] Prüfe die Änderungen in VS Code."
    echo "Wenn alles passt, führe im Terminal aus:"
    echo "  git add ."
    echo "  git commit -m 'feat(cursor): implement changes from AG task'"
    echo "  git checkout main && git merge feature/ag-transfer"
    echo "=================================================================="
else
    echo "[i] Keine Dateiänderungen festgestellt."
fi
