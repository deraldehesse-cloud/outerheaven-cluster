#!/usr/bin/env bash
# ==============================================================================
# ⚔️ OUTER HEAVEN // ZENTRALER CLUSTER-POWER & SHUTDOWN MANAGER
# ==============================================================================
# Optionen:
#   Option 1: Alle Rechner herunterfahren (fox -> deraldehesse -> outerheaven)
#   Option 2: Nur Outer Heaven herunterfahren
#   Option 3: Nur Fox herunterfahren
#   Option 4: Nur Der Alde Hesse (NAS) herunterfahren
#
# Wichtig für Autarkie:
#   Alle SSH-Befehle sind mit strikten Timeouts (-o ConnectTimeout=2) und
#   Fehlerbehandlung versehen, damit das Skript niemals blockiert, wenn ein
#   Knoten bereits offline ist.
# ==============================================================================

set -uo pipefail

FOX_HOST="fox"
NAS_HOST="deraldehesse"
OH_HOST="outerheaven"

SSH_OPTS=(
    -o ConnectTimeout=2
    -o BatchMode=yes
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
)

# Farbdefinitionen
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
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        if command -v notify-send >/dev/null 2>&1; then
            notify-send -i system-shutdown "Outer Heaven Power Control" "$1" 2>/dev/null || true
        fi
    fi
}

is_local_host() {
    local target="$1"
    local my_hostname
    my_hostname="$(hostname 2>/dev/null || true)"
    case "$target" in
        outerheaven|100.116.38.29)
            [[ "$my_hostname" =~ outerheaven|HP-Z620 ]] && return 0
            ;;
        fox|100.93.184.87)
            [[ "$my_hostname" =~ fox ]] && return 0
            ;;
        deraldehesse|100.127.247.117)
            [[ "$my_hostname" =~ deraldehesse ]] && return 0
            ;;
    esac
    return 1
}

reachable() {
    local target="$1"
    if is_local_host "$target"; then
        return 0
    fi
    ssh "${SSH_OPTS[@]}" "$target" "true" 2>/dev/null
}

shutdown_node() {
    local host="$1"
    local desc="$2"
    local pre_cmd="${3:-}"

    say "\n${BOLD}${CYAN}=== Shutdown für ${desc} (${host}) ===${RESET}"

    # Lokaler Host-Check
    if is_local_host "$host"; then
        log_info "Ausführung direkt auf lokalem Host (${host})."
        if [ -n "$pre_cmd" ]; then
            log_info "Führe Vorbereitung aus..."
            eval "$pre_cmd" 2>&1 | sed 's/^/   /' || true
        fi
        log_info "Fahre lokales System herunter..."
        sync
        sudo -n systemctl poweroff 2>/dev/null || systemctl poweroff 2>/dev/null || sudo -n shutdown -h now 2>/dev/null || shutdown -h now 2>/dev/null || {
            log_fail "Lokaler Shutdown fehlgeschlagen!"
            return 1
        }
        return 0
    fi

    # Remote Erreichbarkeits-Check (striktes 2s Timeout)
    if ! reachable "$host"; then
        log_info "${desc} (${host}) ist offline / nicht erreichbar. Überspringe ohne Wartezeit."
        return 0
    fi

    if [ -n "$pre_cmd" ]; then
        log_info "Führe Vorbereitungs-Kommandos auf ${host} aus..."
        ssh "${SSH_OPTS[@]}" "$host" "$pre_cmd" 2>&1 | sed 's/^/   /' || true
    fi

    log_info "Sende Poweroff-Befehl an ${host}..."
    ssh "${SSH_OPTS[@]}" "$host" "sync; sudo -n systemctl poweroff 2>/dev/null || systemctl poweroff 2>/dev/null || sudo -n shutdown -h now 2>/dev/null || shutdown -h now 2>/dev/null" 2>&1 | sed 's/^/   /' || true

    log_info "Prüfe Abschaltung von ${host} (max. 15s)..."
    local waited=0
    while [ $waited -lt 5 ]; do
        sleep 3
        waited=$((waited + 1))
        if ! reachable "$host"; then
            log_ok "${desc} (${host}) ist erfolgreich heruntergefahren."
            return 0
        fi
        echo -n "."
    done
    echo ""
    log_warn "${desc} (${host}) reagiert nach 15s noch auf SSH (fährt möglicherweise noch herunter)."
    return 0
}

shutdown_fox() {
    local pre_fox="systemctl --user stop hermes-gateway.service 2>/dev/null; sync"
    shutdown_node "$FOX_HOST" "Fox (Mini-PC / Bot)" "$pre_fox"
}

shutdown_nas() {
    local pre_nas="sync"
    shutdown_node "$NAS_HOST" "Der Alde Hesse (NAS / Storage)" "$pre_nas"
}

shutdown_outerheaven() {
    local pre_oh="systemctl --user stop mission-control.service megan-studio.service comfyui.service 2>/dev/null; sync"
    shutdown_node "$OH_HOST" "Outer Heaven (Compute Server)" "$pre_oh"
}

shutdown_all() {
    say "${BOLD}${YELLOW}Fahre gesamten Cluster geordnet herunter:${RESET}"
    say "Reihenfolge: Fox → Der Alde Hesse (NAS) → Outer Heaven\n"

    # 1. Fox
    shutdown_fox
    sleep 2

    # 2. NAS (Der Alde Hesse)
    shutdown_nas
    sleep 2

    # 3. Outer Heaven
    shutdown_outerheaven

    notify "Cluster-Shutdown Sequenz abgeschlossen."
    log_ok "Cluster-Shutdown Sequenz vollständig abgearbeitet."
}

gui_menu() {
    local choice=""

    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-${WAYLAND_DISPLAY:-}}" ]; then
        choice=$(zenity --list \
            --title="⚡ Outer Heaven Cluster Power Manager" \
            --text="Wähle eine Shutdown-Option:" \
            --radiolist \
            --column="Auswahl" --column="ID" --column="Beschreibung" \
            TRUE "1" "Option 1: Alle Rechner herunterfahren (Fox -> NAS -> Outer Heaven)" \
            FALSE "2" "Option 2: Nur Outer Heaven herunterfahren (Compute Server)" \
            FALSE "3" "Option 3: Nur Fox herunterfahren (Mini-PC / Bot)" \
            FALSE "4" "Option 4: Nur Der Alde Hesse (NAS / Storage) herunterfahren" \
            --height=320 --width=620 2>/dev/null) || exit 0
    elif command -v yad >/dev/null 2>&1 && [ -n "${DISPLAY:-${WAYLAND_DISPLAY:-}}" ]; then
        choice=$(yad --title="⚡ Outer Heaven Cluster Power Manager" \
            --text="Wähle eine Shutdown-Option:" \
            --list --radiolist \
            --column="Auswahl" --column="ID" --column="Beschreibung" \
            TRUE "1" "Option 1: Alle Rechner herunterfahren (Fox -> NAS -> Outer Heaven)" \
            FALSE "2" "Option 2: Nur Outer Heaven herunterfahren (Compute Server)" \
            FALSE "3" "Option 3: Nur Fox herunterfahren (Mini-PC / Bot)" \
            FALSE "4" "Option 4: Nur Der Alde Hesse (NAS / Storage) herunterfahren" \
            --height=320 --width=620 --separator="" 2>/dev/null) || exit 0
    else
        say "${BOLD}${CYAN}======================================================${RESET}"
        say "${BOLD}${CYAN}   ⚡ OUTER HEAVEN CLUSTER POWER MANAGER              ${RESET}"
        say "${BOLD}${CYAN}======================================================${RESET}"
        say "1) Option 1: Alle Rechner herunterfahren (Fox → NAS → Outer Heaven)"
        say "2) Option 2: Nur Outer Heaven herunterfahren (Compute Server)"
        say "3) Option 3: Nur Fox herunterfahren (Mini-PC / Bot)"
        say "4) Option 4: Nur Der Alde Hesse (NAS / Storage) herunterfahren"
        say "5) Abbrechen"
        echo ""
        read -r -p "Auswahl [1-5]: " choice
        case "$choice" in
            1|2|3|4) ;;
            *) say "Abgebrochen."; exit 0 ;;
        esac
    fi

    [ -z "$choice" ] && exit 0

    local confirm_msg=""
    case "$choice" in
        1) confirm_msg="Möchtest du wirklich ALLE Rechner (Fox, NAS, Outer Heaven) herunterfahren?" ;;
        2) confirm_msg="Möchtest du wirklich Outer Heaven (Compute Server) herunterfahren?" ;;
        3) confirm_msg="Möchtest du wirklich Fox herunterfahren?" ;;
        4) confirm_msg="Möchtest du wirklich Der Alde Hesse (NAS) herunterfahren?" ;;
        *) exit 0 ;;
    esac

    if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-${WAYLAND_DISPLAY:-}}" ]; then
        zenity --question --title="Herunterfahren bestätigen" --text="$confirm_msg" --ok-label="Herunterfahren" --cancel-label="Abbrechen" || exit 0
    elif command -v yad >/dev/null 2>&1 && [ -n "${DISPLAY:-${WAYLAND_DISPLAY:-}}" ]; then
        yad --title="Herunterfahren bestätigen" --text="$confirm_msg" --button="Abbrechen:1" --button="Herunterfahren:0" || exit 0
    else
        read -r -p "$confirm_msg [j/N]: " ans
        [[ "$ans" =~ ^[jJyY]$ ]] || { say "Abgebrochen."; exit 0; }
    fi

    run_option "$choice"
}

run_option() {
    local opt="$1"
    case "$opt" in
        1|all)
            shutdown_all
            ;;
        2|outerheaven)
            shutdown_outerheaven
            notify "Outer Heaven heruntergefahren."
            ;;
        3|fox)
            shutdown_fox
            notify "Fox heruntergefahren."
            ;;
        4|nas|deraldehesse)
            shutdown_nas
            notify "Der Alde Hesse (NAS) heruntergefahren."
            ;;
        *)
            log_fail "Ungültige Option: $opt"
            exit 1
            ;;
    esac
}

# --- CLI Parameter Parsing ---
ACTION=""
SKIP_CONFIRM=0

while [ $# -gt 0 ]; do
    case "$1" in
        --all|-1)
            ACTION="1"
            shift
            ;;
        --outerheaven|-2)
            ACTION="2"
            shift
            ;;
        --fox|-3)
            ACTION="3"
            shift
            ;;
        --nas|--deraldehesse|-4)
            ACTION="4"
            shift
            ;;
        --node)
            if [ -n "${2:-}" ]; then
                case "$2" in
                    all) ACTION="1" ;;
                    outerheaven) ACTION="2" ;;
                    fox) ACTION="3" ;;
                    nas|deraldehesse) ACTION="4" ;;
                    *) log_fail "Unbekannter Knoten: $2"; exit 1 ;;
                esac
                shift 2
            else
                log_fail "--node erfordert einen Knotennamen"
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
            say "  --all, -1               Option 1: Alle Rechner herunterfahren (Fox -> NAS -> Outer Heaven)"
            say "  --outerheaven, -2       Option 2: Nur Outer Heaven herunterfahren"
            say "  --fox, -3               Option 3: Nur Fox herunterfahren"
            say "  --nas, -4               Option 4: Nur Der Alde Hesse (NAS) herunterfahren"
            say "  --node <Name>           Knoten wählen (all|outerheaven|fox|deraldehesse)"
            say "  -y, --yes               Bestätigungsdialog überspringen"
            say "  -h, --help              Zeigt diese Hilfe an"
            say ""
            say "Ohne Optionen startet das interaktive Menü (GUI oder Terminal)."
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
        read -r -p "Soll Option '$ACTION' wirklich ausgeführt werden? [j/N]: " ans
        [[ "$ans" =~ ^[jJyY]$ ]] || { say "Abgebrochen."; exit 0; }
    fi
    run_option "$ACTION"
fi
