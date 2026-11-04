#!/usr/bin/env bash
# ==============================================================================
# 👁️ Ollama Model Watcher & Dynamic Hermes Synchronizer (Host: Outer Heaven)
# ==============================================================================
# Überwacht die lokale Ollama-API (http://localhost:11434/api/ps) auf Modellwechsel.
# Sobald ein neues Modell in Ollama aktiv wird:
# 1. Führt VRAM-Flush durch (keep_alive: 0 & ollama stop), damit strikt max. 1 Modell
#    im VRAM der Sparkle Arc A770 liegt.
# 2. Ermittelt das zugehörige Modelfile in /mnt/storage-alpha/ollama_models.
# 3. Liest den Parameter 'num_ctx' aus (Fallback: 8192).
# 4. Hebt den Schreibschutz von /home/outerheaven/.hermes/config.yaml via protect-config.sh auf.
# 5. Aktualisiert Modellname und num_ctx in der config.yaml und .env.
# 6. Stellt den Schreibschutz via protect-config.sh lock (chmod 400) wieder her.
# 7. Startet den Telegram-Dienst (hermes-telegram.service) neu.
# ==============================================================================

set -uo pipefail

# --- Farbdefinitionen für Logging ---
GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

# --- Konfiguration & Pfade ---
OLLAMA_API="${OLLAMA_API:-http://localhost:11434/api/ps}"
MODELS_DIR="${MODELS_DIR:-/mnt/storage-alpha/ollama_models}"
HERMES_DIR="${HERMES_DIR:-/home/outerheaven/.hermes}"
CONFIG_FILE="${HERMES_DIR}/config.yaml"
PROTECT_SCRIPT="${HERMES_DIR}/protect-config.sh"
HERMES_SERVICE="${HERMES_SERVICE:-hermes-telegram.service}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
DEFAULT_NUM_CTX="${DEFAULT_NUM_CTX:-8192}"
VENV_PYTHON="${HERMES_DIR}/.venv/bin/python"

# Logging-Hilfsfunktionen
log_info() {
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${CYAN}INFO${RESET}] $1"
}

log_success() {
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${GREEN}OK${RESET}] $1"
}

log_warn() {
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${YELLOW}WARNUNG${RESET}] $1"
}

log_error() {
    echo -e "$(date '+%Y-%m-%d %H:%M:%S') [${RED}FEHLER${RESET}] $1" >&2
}

# Signalbehandlung für sauberes Beenden
trap 'log_info "Model Watcher wird beendet..."; exit 0' SIGTERM SIGINT

# Prüfe Python-Interpreter
PYTHON_BIN="python3"
if [ -x "$VENV_PYTHON" ]; then
    PYTHON_BIN="$VENV_PYTHON"
fi

# ------------------------------------------------------------------------------
# 1. Aktuelles aktives Modell von Ollama abfragen
# ------------------------------------------------------------------------------
get_active_model() {
    local response
    response=$(curl -s --connect-timeout 3 --max-time 5 "$OLLAMA_API" 2>/dev/null || true)

    if [ -z "$response" ]; then
        return 1
    fi

    echo "$response" | "$PYTHON_BIN" -c '
import sys, json
try:
    data = json.load(sys.stdin)
    models = data.get("models", [])
    if models and isinstance(models, list):
        print(models[0].get("name", models[0].get("model", "")))
except Exception:
    pass
' 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# 2. VRAM-Flush & Single-Model-Regel für Intel Sparkle Arc A770
# ------------------------------------------------------------------------------
flush_vram() {
    local old_model="$1"
    local new_model="$2"

    log_info "🧹 [VRAM-Flush] Bereinige VRAM der Arc A770 (Single-Model-Regel)..."

    # Altes Modell mit keep_alive: 0 explizit entladen
    if [ -n "$old_model" ] && [ "$old_model" != "$new_model" ]; then
        log_info "Entlade voriges Modell '$old_model' via keep_alive: 0..."
        curl -s -X POST "http://localhost:11434/api/generate" \
            -H "Content-Type: application/json" \
            -d "{\"model\": \"$old_model\", \"keep_alive\": 0}" >/dev/null 2>&1 || true
        ollama stop "$old_model" >/dev/null 2>&1 || true
    fi

    # Prüfen, ob noch andere Modelle im VRAM verweilen
    local active_list
    active_list=$(curl -s --connect-timeout 2 "http://localhost:11434/api/ps" 2>/dev/null | "$PYTHON_BIN" -c '
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get("models", []):
        print(m.get("name", m.get("model", "")))
except Exception:
    pass
' 2>/dev/null || true)

    for m in $active_list; do
        if [ -n "$m" ] && [ "$m" != "$new_model" ]; then
            log_info "Entlade inaktives Modell '$m' aus VRAM..."
            curl -s -X POST "http://localhost:11434/api/generate" \
                -H "Content-Type: application/json" \
                -d "{\"model\": \"$m\", \"keep_alive\": 0}" >/dev/null 2>&1 || true
            ollama stop "$m" >/dev/null 2>&1 || true
        fi
    done

    sleep 1
    log_success "[VRAM-Flush] VRAM erfolgreich bereinigt. Nur '$new_model' ist aktiv."
}

# ------------------------------------------------------------------------------
# 3. Modelfile lokalisieren
# ------------------------------------------------------------------------------
find_modelfile() {
    local model_name="$1"
    local model_base="${model_name%:*}"
    local candidate=""

    if ! timeout 3 test -d "$MODELS_DIR" 2>/dev/null; then
        log_warn "Verzeichnis '$MODELS_DIR' ist nicht erreichbar."
        return 1
    fi

    local candidates=(
        "$MODELS_DIR/$model_name"
        "$MODELS_DIR/$model_base"
        "$MODELS_DIR/Modelfile-$model_name"
        "$MODELS_DIR/Modelfile-$model_base"
        "$MODELS_DIR/modelfile-$model_name"
        "$MODELS_DIR/modelfile-$model_base"
        "$MODELS_DIR/${model_base}.modelfile"
        "$MODELS_DIR/${model_base}.Modelfile"
        "$MODELS_DIR/$model_name/Modelfile"
        "$MODELS_DIR/$model_name/modelfile"
        "$MODELS_DIR/$model_base/Modelfile"
        "$MODELS_DIR/$model_base/modelfile"
    )

    for candidate in "${candidates[@]}"; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

# ------------------------------------------------------------------------------
# 4. num_ctx aus Modelfile extrahieren
# ------------------------------------------------------------------------------
extract_num_ctx() {
    local modelfile="$1"
    local num_ctx=""

    num_ctx=$(grep -E '^\s*PARAMETER\s+num_ctx\s+' "$modelfile" 2>/dev/null | awk '{print $3}' | tr -d '\r\n' || true)

    if [ -n "$num_ctx" ] && [[ "$num_ctx" =~ ^[0-9]+$ ]]; then
        echo "$num_ctx"
    else
        echo "$DEFAULT_NUM_CTX"
    fi
}

# ------------------------------------------------------------------------------
# 5. config.yaml & .env aktualisieren (mit protect-config.sh unlock / lock)
# ------------------------------------------------------------------------------
update_hermes_config() {
    local model_name="$1"
    local num_ctx="$2"

    log_info "Aktualisiere Hermes-Konfiguration auf Modell '$model_name' (num_ctx: $num_ctx)..."

    # Schreibschutz aufheben
    if [ -x "$PROTECT_SCRIPT" ]; then
        log_info "Entsperre config.yaml via protect-config.sh unlock..."
        "$PROTECT_SCRIPT" unlock >/dev/null 2>&1 || chmod 600 "$CONFIG_FILE"
    else
        chmod 600 "$CONFIG_FILE" 2>/dev/null || true
    fi

    local temp_file="${CONFIG_FILE}.tmp.$$"

    "$PYTHON_BIN" -c '
import sys, yaml
src_path = "'"$CONFIG_FILE"'"
dst_path = "'"$temp_file"'"
model_name = "'"$model_name"'"
num_ctx = int("'"$num_ctx"'")

try:
    with open(src_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "model" not in data or not isinstance(data["model"], dict):
        data["model"] = {}

    data["model"]["default"] = model_name
    data["model"]["context_length"] = num_ctx
    if "options" in data["model"] and isinstance(data["model"]["options"], dict):
        data["model"]["options"]["num_ctx"] = num_ctx

    with open(dst_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    sys.exit(0)
except Exception as e:
    sys.stderr.write(f"PyYAML Fehler: {e}\n")
    sys.exit(1)
'

    if [ -s "$temp_file" ]; then
        cp -p "$CONFIG_FILE" "${CONFIG_FILE}.bak" 2>/dev/null || true
        mv "$temp_file" "$CONFIG_FILE"
    else
        rm -f "$temp_file"
        log_error "Aktualisierung von config.yaml fehlgeschlagen: temp_file war leer."
        return 1
    fi

    # Auch .env OPENAI_API_MODEL aktualisieren
    if [ -f "$HERMES_DIR/.env" ]; then
        sed -i "s/^OPENAI_API_MODEL=.*/OPENAI_API_MODEL=$model_name/" "$HERMES_DIR/.env" 2>/dev/null || true
    fi

    # Schreibschutz reaktivieren via protect-config.sh lock (chmod 400)
    if [ -x "$PROTECT_SCRIPT" ]; then
        log_info "Sichere config.yaml via protect-config.sh lock..."
        "$PROTECT_SCRIPT" lock >/dev/null 2>&1 || chmod 400 "$CONFIG_FILE"
    else
        chmod 400 "$CONFIG_FILE" 2>/dev/null || true
    fi

    log_success "config.yaml erfolgreich aktualisiert und mit chmod 400 geschützt."
    return 0
}

# ------------------------------------------------------------------------------
# 6. Hermes Telegram Dienst neu starten
# ------------------------------------------------------------------------------
restart_agent_service() {
    log_info "Trigger Dienst-Neustart: $HERMES_SERVICE..."

    # 1. Über User-Session (falls Umgebungsvariablen gesetzt)
    if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        systemctl --user restart "$HERMES_SERVICE" 2>/dev/null || true
    fi

    # 2. Gezielt via runuser als outerheaven
    if [ "$(id -u)" -eq 0 ] && id "outerheaven" >/dev/null 2>&1; then
        runuser -u outerheaven -- systemctl --user restart "$HERMES_SERVICE" 2>/dev/null || true
    elif [ "$(whoami)" = "outerheaven" ]; then
        systemctl --user restart "$HERMES_SERVICE" 2>/dev/null || true
    fi

    sleep 2
    log_success "Dienst '$HERMES_SERVICE' wurde erfolgreich neu gestartet."
}

# ------------------------------------------------------------------------------
# 7. Hauptüberwachungsschleife
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}======================================================${RESET}"
echo -e "${BOLD}${CYAN}   👁️ OLLAMA MODEL WATCHER (OUTER HEAVEN)            ${RESET}"
echo -e "${BOLD}${CYAN}======================================================${RESET}"
log_info "Überwache Ollama-API: $OLLAMA_API (Intervall: ${POLL_INTERVAL}s)"
log_info "Hermes Verzeichnis:   $HERMES_DIR"
log_info "Hermes Konfiguration: $CONFIG_FILE"
log_info "Dienst-Trigger:       $HERMES_SERVICE"

LAST_MODEL=""
if [ -f "$CONFIG_FILE" ]; then
    LAST_MODEL=$("$PYTHON_BIN" -c '
import sys, yaml
try:
    with open("'"$CONFIG_FILE"'", "r") as f:
        cfg = yaml.safe_load(f)
        print(cfg.get("model", {}).get("default", ""))
except Exception:
    pass
' 2>/dev/null || true)
fi

log_info "Initialer Modellstand: '${LAST_MODEL:-<nicht definiert>}'"

while true; do
    CURRENT_MODEL=$(get_active_model || true)

    if [ -n "$CURRENT_MODEL" ]; then
        if [ "$CURRENT_MODEL" != "$LAST_MODEL" ]; then
            echo -e "\n${BOLD}${CYAN}------------------------------------------------------${RESET}"
            log_info "⚡ MODELLWECHSEL ERKANNT: '$LAST_MODEL' -> '$CURRENT_MODEL'"
            echo -e "${BOLD}${CYAN}------------------------------------------------------${RESET}"

            # VRAM-Flush durchführen (Single-Model-Regel)
            flush_vram "$LAST_MODEL" "$CURRENT_MODEL"

            # Modelfile suchen & num_ctx extrahieren
            MODELFILE=$(find_modelfile "$CURRENT_MODEL" || true)
            if [ -n "$MODELFILE" ] && [ -f "$MODELFILE" ]; then
                log_success "Modelfile gefunden: $MODELFILE"
                NUM_CTX=$(extract_num_ctx "$MODELFILE")
            else
                log_warn "Kein spezifisches Modelfile gefunden. Verwende Fallback num_ctx = $DEFAULT_NUM_CTX"
                NUM_CTX="$DEFAULT_NUM_CTX"
            fi

            # Konfiguration aktualisieren & Dienst neu starten
            if update_hermes_config "$CURRENT_MODEL" "$NUM_CTX"; then
                restart_agent_service
                LAST_MODEL="$CURRENT_MODEL"
                log_success "Synchronisation für '$CURRENT_MODEL' abgeschlossen."
            else
                log_error "Fehler bei Konfigurationsaktualisierung. Dienst wird nicht neu gestartet."
            fi
        fi
    fi

    sleep "$POLL_INTERVAL"
done
