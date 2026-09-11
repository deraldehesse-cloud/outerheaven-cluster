#!/usr/bin/env bash
# ==============================================================================
# 🚀 Outer Heaven - Cursor Dispatcher & Handoff Script
# ==============================================================================
# Pfad: scripts/handoff-to-cursor.sh
# Zweck:
#   1. Liest .cursor_task.md (oder .cursor_tasks.md) aus.
#   2. Extrahiert Ziel-Dateien und verifiziert Git-Schutz.
#   3. Wechselt/erstellt dedizierten Branch: feature/ag-transfer.
#   4. Sichert uncommitted Changes im Branch.
#   5. Öffnet Cursor auf dem Laptop mit Repo-Pfad, Ziel-Dateien und Anweisung:
#      cursor /home/bigboss/outerheaven-cluster-repo/ $TARGET_FILES
# ==============================================================================

set -euo pipefail

# ANSI Color Codes
GREEN=$'\033[92m'
RED=$'\033[91m'
YELLOW=$'\033[93m'
BLUE=$'\033[94m'
CYAN=$'\033[96m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

# Defaults & Config
REPO_DEFAULT="/home/bigboss/outerheaven-cluster-repo"
DEFAULT_COMMIT_MSG="chore(ag): prepare handoff for cursor"
TARGET_BRANCH="feature/ag-transfer"
TASK_FILE=".cursor_task.md"

DRY_RUN=false
NO_OPEN=false
COMMIT_MSG=""

# Helper: Print Usage
usage() {
    cat <<USAGE
${BOLD}Nutzung:${RESET} $0 [OPTIONEN] [COMMIT_MESSAGE]

${BOLD}Beschreibung:${RESET}
  Liest die Architekten-Spezifikation aus .cursor_task.md, sichert den
  Git-Branch '${TARGET_BRANCH}' und übergibt die Implementierungsaufgabe
  an Cursor auf dem Laptop.

${BOLD}Optionen:${RESET}
  -n, --dry-run          Testlauf ohne permanente Git-Aktionen oder Cursor-Start.
  --no-open              Git-Aktionen durchführen, aber Cursor nicht starten.
  -m, --message <msg>    Individuelle Git-Commit-Nachricht.
  -h, --help             Zeigt diese Hilfe an.

${BOLD}Beispiele:${RESET}
  $0
  $0 "feat(cluster): spec for worker queue redesign"
  $0 --dry-run
USAGE
    exit 0
}

# Parse Arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-open)
            NO_OPEN=true
            shift
            ;;
        -m|--message)
            if [[ -n "${2:-}" ]]; then
                COMMIT_MSG="$2"
                shift 2
            else
                echo -e "${RED}Fehler: --message benötigt ein Argument.${RESET}" >&2
                exit 1
            fi
            ;;
        -h|--help)
            usage
            ;;
        *)
            if [[ -z "$COMMIT_MSG" ]]; then
                COMMIT_MSG="$1"
                shift
            else
                echo -e "${RED}Unbekanntes Argument: $1${RESET}" >&2
                exit 1
            fi
            ;;
    esac
done

COMMIT_MSG="${COMMIT_MSG:-$DEFAULT_COMMIT_MSG}"

# Resolve Repository Root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -d "${REPO_ROOT}/.git" ]]; then
    echo -e "${RED}Fehler: '${REPO_ROOT}' ist kein Git-Repository!${RESET}" >&2
    exit 1
fi

cd "${REPO_ROOT}"

echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   🚀 OUTER HEAVEN - DISPATCH HANDOFF TO CURSOR       ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
date "+Datum: %Y-%m-%d %H:%M:%S"
echo -e "Repository: ${BOLD}${REPO_ROOT}${RESET}"
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}${BOLD}[MODUS] DRY-RUN AKTIV - Keine permanenten Änderungen!${RESET}"
fi
echo ""

# ------------------------------------------------------------------------------
# 1. TASK-DATEI PRÜFEN & AUSLESEN
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}1. Lese Anweisung aus Task-Datei...${RESET}"

# Falls .cursor_task.md nicht existiert, prüfe Fallback .cursor_tasks.md
if [[ ! -f "${REPO_ROOT}/${TASK_FILE}" && -f "${REPO_ROOT}/.cursor_tasks.md" ]]; then
    TASK_FILE=".cursor_tasks.md"
fi

if [[ ! -f "${REPO_ROOT}/${TASK_FILE}" ]]; then
    echo -e "${YELLOW}Keine Task-Datei '${TASK_FILE}' im Repo-Root gefunden.${RESET}"
    echo -e "Erstelle Basis-Vorlage: ${BOLD}${REPO_ROOT}/${TASK_FILE}${RESET}"
    
    if [[ "$DRY_RUN" == false ]]; then
        cat << 'TEMPLATE_EOF' > "${REPO_ROOT}/${TASK_FILE}"
# 🎯 Cursor Task Specification (Outer Heaven)

## 📁 Ziel-Dateien
<!-- Liste hier die zu bearbeitenden oder neu anzulegenden Dateien auf -->
- `scripts/verify-cluster.sh`

## 🧠 Spezifikation & Logik
<!-- Präzise Anweisungen für Cursors Composer (Strg + I) -->
- **Ziel:** 
- **Logik:**
- **Typisierung & Schnittstellen:**
- **Tests & Verifikation:**

TEMPLATE_EOF
        echo -e "  [${GREEN}ERSTELLT${RESET}] Vorlage '${TASK_FILE}' angelegt. Bitte Spezifikation eintragen."
    else
        echo -e "  [DRY-RUN] Würde Vorlage '${TASK_FILE}' anlegen."
    fi
fi

# Extrahiere Ziel-Dateien aus .cursor_task.md
PARSED_FILES=()
if [[ -f "${REPO_ROOT}/${TASK_FILE}" ]]; then
    # 1. Versuch: Extraktion aus der Sektion 'Ziel-Dateien' / 'Target Files' / 'Dateien'
    EXTRACTED_RAW=$(awk '
      tolower($0) ~ /^##.*(ziel-dateien|target files|dateien|files)/ { in_section=1; next }
      in_section && /^##/ { in_section=0 }
      in_section && /^[[:space:]]*[-*][[:space:]]+/ {
        line = $0
        sub(/^[[:space:]]*[-*][[:space:]]+/, "", line)
        gsub(/[`"'\'' ]/, "", line)
        if (length(line) > 0) print line
      }
    ' "${REPO_ROOT}/${TASK_FILE}")

    # 2. Versuch: Falls Sektion leer, suche nach allen Backtick-Pfaden im Dokument
    if [[ -z "$EXTRACTED_RAW" ]]; then
        EXTRACTED_RAW=$(grep -oE '`[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+`' "${REPO_ROOT}/${TASK_FILE}" | tr -d '`' || true)
    fi

    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        # Kommentare, Platzhalter und Task-Datei selbst überspringen
        case "$f" in
            '<!--'*|*'-->') continue ;;
        esac
        if [[ "$f" == "$TASK_FILE" || "$f" == ".cursor_tasks.md" ]]; then
            continue
        fi
        PARSED_FILES+=("$f")
    done <<< "$EXTRACTED_RAW"
fi

# Entferne Duplikate
UNIQUE_TARGET_FILES=()
for f in "${PARSED_FILES[@]:-}"; do
    [[ -z "$f" ]] && continue
    already=false
    for u in "${UNIQUE_TARGET_FILES[@]:-}"; do
        if [[ "$u" == "$f" ]]; then
            already=true
            break
        fi
    done
    if [[ "$already" == false ]]; then
        UNIQUE_TARGET_FILES+=("$f")
    fi
done

echo -e "  Task-Datei (Anweisung):  ${GREEN}${TASK_FILE}${RESET}"
if [[ ${#UNIQUE_TARGET_FILES[@]} -gt 0 ]]; then
    echo -e "  Extrahierte Zieldateien (${#UNIQUE_TARGET_FILES[@]}):"
    for f in "${UNIQUE_TARGET_FILES[@]}"; do
        if [[ -f "${REPO_ROOT}/${f}" ]]; then
            echo -e "    - ${CYAN}${f}${RESET} [existiert]"
        else
            echo -e "    - ${YELLOW}${f}${RESET} [neu/zu erstellen]"
        fi
    done
else
    echo -e "  ${YELLOW}Hinweis: Keine spezifischen Zieldateien in '${TASK_FILE}' definiert.${RESET}"
fi
echo ""

# ------------------------------------------------------------------------------
# 2. GIT-SCHUTZ: .gitignore PRÜFUNG
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}2. Prüfe Git-Schutz für ${TASK_FILE}...${RESET}"
if git check-ignore -q "${TASK_FILE}"; then
    echo -e "  [${GREEN}OK${RESET}] '${TASK_FILE}' wird durch .gitignore ignoriert."
else
    echo -e "  [${YELLOW}WARN${RESET}] '${TASK_FILE}' ist nicht in .gitignore. Ergänze .gitignore..."
    if [[ "$DRY_RUN" == false ]]; then
        echo -e "\n# Cursor Dispatcher Task Notes\n${TASK_FILE}" >> "${REPO_ROOT}/.gitignore"
        echo -e "  [${GREEN}ERLEDIGT${RESET}] In .gitignore eingetragen."
    else
        echo -e "  [DRY-RUN] Würde '${TASK_FILE}' an .gitignore anhängen."
    fi
fi
echo ""

# ------------------------------------------------------------------------------
# 3. GIT-STATUS & BRANCHING
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}3. Bereite Git-Branch '${TARGET_BRANCH}' vor...${RESET}"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
UNCOMMITTED_CHANGES="$(git status --porcelain)"

echo -e "  Aktueller Branch:   ${BOLD}${CURRENT_BRANCH}${RESET}"
if [[ -n "$UNCOMMITTED_CHANGES" ]]; then
    CHANGE_COUNT=$(echo "$UNCOMMITTED_CHANGES" | wc -l)
    echo -e "  Uncommitted:        ${YELLOW}${CHANGE_COUNT} geänderte/neue Datei(en)${RESET}"
else
    echo -e "  Uncommitted:        ${GREEN}Keine (Working Tree sauber)${RESET}"
fi

if [[ "$DRY_RUN" == false ]]; then
    git checkout -B "${TARGET_BRANCH}"
    echo -e "  [${GREEN}OK${RESET}] Auf Branch '${TARGET_BRANCH}' gewechselt."

    if [[ -n "$UNCOMMITTED_CHANGES" ]]; then
        echo -e "  [GIT] Stage Änderungen ('git add -A')..."
        git add -A
        echo -e "  [GIT] Committe Änderungen: \"${COMMIT_MSG}\"..."
        git commit -m "${COMMIT_MSG}"
        echo -e "  [${GREEN}OK${RESET}] Commit erfolgreich erstellt."
    fi
else
    echo -e "  [DRY-RUN] Würde ausführen: git checkout -B ${TARGET_BRANCH}"
    if [[ -n "$UNCOMMITTED_CHANGES" ]]; then
        echo -e "  [DRY-RUN] Würde ausführen: git add -A && git commit -m \"${COMMIT_MSG}\""
    fi
fi
echo ""

# ------------------------------------------------------------------------------
# 4. CURSOR-START MIT ANWEISUNG & ZIELDATEIEN
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}4. Öffne Cursor mit Zieldateien & Anweisung...${RESET}"

# Zusammenstellung von $TARGET_FILES:
# Die Anweisung (.cursor_task.md) wird direkt mitgeladen, gefolgt von den Zieldateien
TARGET_FILES=("${TASK_FILE}")
for f in "${UNIQUE_TARGET_FILES[@]}"; do
    TARGET_FILES+=("${f}")
done

CURSOR_CMD=("cursor" "${REPO_ROOT}/" "${TARGET_FILES[@]}")

if ! command -v cursor >/dev/null 2>&1; then
    echo -e "  [${YELLOW}WARN${RESET}] Cursor-CLI 'cursor' wurde nicht im PATH gefunden."
    echo -e "  Befehl zum manuellen Starten:"
    echo -e "  ${BOLD}${CURSOR_CMD[*]}${RESET}"
elif [[ "$NO_OPEN" == true ]]; then
    echo -e "  [INFO] Cursor-Start übersprungen (--no-open angegeben)."
    echo -e "  Manueller Befehl:"
    echo -e "  ${BOLD}${CURSOR_CMD[*]}${RESET}"
elif [[ "$DRY_RUN" == true ]]; then
    echo -e "  [DRY-RUN] Würde folgenden Befehl ausführen:"
    echo -e "  ${BOLD}${CURSOR_CMD[*]}${RESET}"
else
    echo -e "  [EXEC] Starte: ${BOLD}${CURSOR_CMD[*]}${RESET}"
    "${CURSOR_CMD[@]}" &
    CURSOR_PID=$!
    echo -e "  [${GREEN}OK${RESET}] Cursor wurde erfolgreich gestartet (PID: ${CURSOR_PID})."
fi

echo ""
echo -e "${BOLD}${GREEN}======================================================${RESET}"
echo -e "${BOLD}${GREEN}   ✅ HANDOFF ERFOLGREICH AN CURSOR ÜBERGEBEN!       ${RESET}"
echo -e "${BOLD}${GREEN}======================================================${RESET}"
echo -e "Cursor übernimmt nun das Coden auf bigboss."
exit 0
