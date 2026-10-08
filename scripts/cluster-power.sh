#!/usr/bin/env bash
# ==============================================================================
# ⚔️ OUTER HEAVEN // MODULARES CLUSTER-POWER & SHUTDOWN TOOL
# ==============================================================================
# Aufruf:
#   ./cluster-power.sh --all              (Fährt Outer Heaven & Fox geordnet herunter)
#   ./cluster-power.sh --node outerheaven (Fährt nur Outer Heaven herunter)
#   ./cluster-power.sh --node fox         (Fährt nur Fox herunter)
#   ./cluster-power.sh                    (Öffnet interaktives GUI-Menü via zenity/yad)
#
# Optionen:
#   -y, --yes                             (Überspringt Bestätigungsdialoge)
# ==============================================================================

set -uo pipefail

OH_HOST="outerheaven"
OH_IP="100.116.38.29"
FOX_HOST="fox"
FOX_IP="100.93.184.87"
SSH_OPTS=(-o ConnectTimeout=6 -o BatchMode=yes)

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

say() { echo -e "$*"; }
log_ok() { say " ${GREEN}✔ OK:${RESET} $1"; }
log_warn() { say " ${YELLOW}⚠ WARNUNG:${RESET} $1"; }
log_fail() { say " ${RED}✖ FEHLER:${RESET} $1"; }
log_info() { say " ${CYAN}ℹ INFO:${RESET} $1"; }

notify() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "Outer Heaven Power-Management" "$1" 2>/dev/null || true
    fi
}

reachable() {
    local target="$1"
    ssh "${SSH_OPTS[@]}" "$target" true 2>/dev/null
}

shutdown_node() {
    local host="$1"
    local desc="$2"
    local pre_cmd="${3:-}"

    say "\n${BOLD}${CYAN}=== Shutdown-Prozess für ${desc} (${host}) ===${RESET}"
    if ! reachable "$host"; then
        log_warn "${desc} (${host}) ist nicht per SSH erreichbar (bereits ausgeschaltet?)."
        return 0
    fi

    if [ -n "$pre_cmd" ]; then
        log_info "Führe Vorbereitungs-Kommandos auf ${host} aus..."
        ssh "${SSH_OPTS[@]}" "$host" "$pre_cmd" 2>&1 | sed 's/^/   /' || true
    fi

    log_info "Sende Poweroff-Signal an ${host}..."
    ssh "${SSH_OPTS[@]}" "$host" "sync; sudo -n systemctl poweroff 2>/dev/null || systemctl poweroff 2>/dev/null || sudo -n shutdown -h now 2>/dev/null || shutdown -h now 2>/dev/null" 2>&1 | sed 's/^/   /' || true

    log_info "Warte auf Abschalten von ${host} (max. 60s)..."
    local waited=0
    while [ $waited -lt 20 ]; do
        sleep 3
        waited=$((waited + 1))
        if ! reachable "$host"; then
            log_ok "${desc} (${host}) ist erfolgreich heruntergefahren."
            return 0
        fi
        echo -n "."
    done
    echo ""
    log_fail "${desc} (${host}) antwortet nach 60s immer noch per SSH!"
    return 1
}

shutdown_outerheaven() {
    local pre_oh="systemctl --user stop mission-control.service megan-studio.service comfyui.service 2>/dev/null;"
    shutdown_node "$OH_HOST" "Outer Heaven (HP Z620)" "$pre_oh"
}

shutdown_fox() {
    local pre_fox="systemctl --user stop hermes-gateway.service 2>/dev/null;"
    shutdown_node "$FOX_HOST" "Fox Mini-PC" "$pre_fox"
}

shutdown_all() {
    say "${BOLD}${YELLOW}Fahre gesamten Cluster geordnet herunter (Outer Heaven → Fox)...${RESET}"
    local rc_oh=0
    local rc_fox=0

    shutdown_outerheaven || rc_oh=$?
    sleep 2
    shutdown_fox || rc_fox=$?

    if [ $rc_oh -eq 0 ] && [ $rc_fox -eq 0 ]; then
        notify "Outer Heaven & Fox erfolgreich heruntergefahren."
        log_ok "Gesamter Cluster ist heruntergefahren."
    else
        notify "Cluster-Shutdown unvollständig – bitte prüfen!"
        log_warn "Mindestens ein Knoten konnte nicht bestätigt heruntergefahren werden."
    fi
}

gui_menu() {
    local choice=""
    if command -v zenity >/dev/null 2>&1; then
        choice=$(zenity --list \
            --title="⚔️ Outer Heaven Power Control" \
            --text="Wähle eine Aktion für das Cluster Power-Management:" \
            --radiolist \
            --column="Auswahl" --column="Modus" --column="Beschreibung" \
            TRUE "all" "Gesamten Cluster herunterfahren (Outer Heaven → Fox)" \
            FALSE "outerheaven" "Nur Outer Heaven herunterfahren (HP Z620)" \
            FALSE "fox" "Nur Fox herunterfahren (Mini-PC)" \
            --height=320 --width=540 2>/dev/null) || exit 0
    elif command -v yad >/dev/null 2>&1; then
        choice=$(yad --title="⚔️ Outer Heaven Power Control" \
            --text="Wähle eine Aktion für das Cluster Power-Management:" \
            --list --radiolist \
            --column="Auswahl" --column="Modus" --column="Beschreibung" \
            TRUE "all" "Gesamten Cluster herunterfahren (Outer Heaven → Fox)" \
            FALSE "outerheaven" "Nur Outer Heaven herunterfahren (HP Z620)" \
            FALSE "fox" "Nur Fox herunterfahren (Mini-PC)" \
            --height=320 --width=540 --separator="" 2>/dev/null) || exit 0
    else
        say "${BOLD}${CYAN}=== Outer Heaven Power Management ===${RESET}"
        say "1) Gesamten Cluster herunterfahren (Outer Heaven → Fox)"
        say "2) Nur Outer Heaven herunterfahren (HP Z620)"
        say "3) Nur Fox herunterfahren (Mini-PC)"
        say "4) Abbrechen"
        read -r -p "Auswahl [1-4]: " num
        case "$num" in
            1) choice="all" ;;
            2) choice="outerheaven" ;;
            3) choice="fox" ;;
            *) exit 0 ;;
        esac
    fi

    [ -z "$choice" ] && exit 0

    local confirm_msg=""
    case "$choice" in
        all) confirm_msg="Möchtest du wirklich den GESAMTEN Cluster (Outer Heaven UND Fox) herunterfahren?" ;;
        outerheaven) confirm_msg="Möchtest du wirklich Outer Heaven (HP Z620) herunterfahren?" ;;
        fox) confirm_msg="Möchtest du wirklich Fox herunterfahren?" ;;
        *) exit 0 ;;
    esac

    if command -v zenity >/dev/null 2>&1; then
        zenity --question --title="Herunterfahren bestätigen" --text="$confirm_msg" --ok-label="Herunterfahren" --cancel-label="Abbrechen" || exit 0
    elif command -v yad >/dev/null 2>&1; then
        yad --title="Herunterfahren bestätigen" --text="$confirm_msg" --button="Abbrechen:1" --button="Herunterfahren:0" || exit 0
    else
        read -r -p "$confirm_msg [j/N]: " a
        [[ "$a" =~ ^[jJyY]$ ]] || exit 0
    fi

    run_action "$choice"
}

run_action() {
    local target="$1"
    case "$target" in
        all)
            shutdown_all
            ;;
        outerheaven)
            shutdown_outerheaven
            notify "Outer Heaven ist heruntergefahren."
            ;;
        fox)
            shutdown_fox
            notify "Fox ist heruntergefahren."
            ;;
        *)
            log_fail "Unbekannter Node: $target"
            exit 1
            ;;
    esac
}

# --- CLI Argument Parsing ---
ACTION=""
SKIP_CONFIRM=0

while [ $# -gt 0 ]; do
    case "$1" in
        --all)
            ACTION="all"
            shift
            ;;
        --node)
            if [ -n "${2:-}" ]; then
                ACTION="$2"
                shift 2
            else
                log_fail "--node erfordert einen Knotennamen (outerheaven oder fox)"
                exit 1
            fi
            ;;
        -y|--yes)
            SKIP_CONFIRM=1
            shift
            ;;
        -h|--help)
            say "Verwendung: $0 [OPTIONEN]"
            say "Optionen:"
            say "  --all                 Fährt Outer Heaven & Fox geordnet herunter"
            say "  --node <Name>         Fährt nur den angegebenen Knoten herunter (outerheaven|fox)"
            say "  -y, --yes             Bestätigungsdialog überspringen"
            say "  -h, --help            Zeigt diese Hilfe an"
            say ""
            say "Wird das Skript ohne Optionen aufgerufen, öffnet sich ein interaktives GUI-Menü."
            exit 0
            ;;
        *)
            log_fail "Unbekannte Option: $1"
            say "Rufe '$0 --help' für weitere Informationen auf."
            exit 1
            ;;
    esac
done

if [ -z "$ACTION" ]; then
    gui_menu
else
    if [ "$SKIP_CONFIRM" -eq 0 ]; then
        read -r -p "Soll '$ACTION' wirklich heruntergefahren werden? [j/N]: " ans
        [[ "$ans" =~ ^[jJyY]$ ]] || { say "Abgebrochen."; exit 0; }
    fi
    run_action "$ACTION"
fi
