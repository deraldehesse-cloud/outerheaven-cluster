#!/usr/bin/env bash
# Fährt den Cluster geordnet herunter: erst Outer Heaven (mountet Fox-Home), dann Fox.
# Aufruf: cluster-shutdown.sh [--yes]   (--yes überspringt die Bestätigung)
set -u

OH_HOST="outerheaven"
FOX_HOST="fox"
SSH_OPTS=(-o ConnectTimeout=8 -o BatchMode=yes)

say() { echo -e "$*"; }
notify() { command -v notify-send >/dev/null && notify-send "Cluster-Shutdown" "$1" 2>/dev/null; }

if [[ "${1:-}" != "--yes" ]]; then
  MSG="Outer Heaven und Fox jetzt herunterfahren?\n\nReihenfolge: Outer Heaven → Fox"
  if command -v zenity >/dev/null; then
    zenity --question --title="Cluster herunterfahren" --text="$MSG" --ok-label="Herunterfahren" --cancel-label="Abbrechen" || exit 0
  else
    read -r -p "Cluster herunterfahren? [j/N] " a; [[ "$a" =~ ^[jJyY]$ ]] || exit 0
  fi
fi

reachable() { ssh "${SSH_OPTS[@]}" "$1" true 2>/dev/null; }

shutdown_node() {
  local host="$1" pre="$2"
  if ! reachable "$host"; then
    say " ⚠ $host nicht erreichbar (schon aus?) – übersprungen."
    return 1
  fi
  say " → $host: Dienste stoppen & herunterfahren ..."
  # sudo -n: nur wenn passwortloses sudo konfiguriert ist; Fallback systemctl poweroff (polkit)
  ssh "${SSH_OPTS[@]}" "$host" "$pre sync; sudo -n systemctl poweroff || systemctl poweroff" 2>&1 | sed 's/^/   /'
  # Warten bis der Host wirklich weg ist (max. 90 s)
  for _ in $(seq 1 30); do
    sleep 3
    reachable "$host" || { say " ✔ $host ist heruntergefahren."; return 0; }
  done
  say " ✖ $host antwortet nach 90 s noch – Shutdown evtl. nicht erlaubt (sudo/polkit prüfen)."
  return 2
}

say "=== Cluster-Shutdown ==="
shutdown_node "$OH_HOST" "systemctl --user stop mission-control.service megan-studio.service 2>/dev/null;"
rc_oh=$?
shutdown_node "$FOX_HOST" ""
rc_fox=$?

if [[ $rc_oh -le 1 && $rc_fox -le 1 ]]; then
  notify "Cluster ist heruntergefahren."
  say "Fertig."
else
  notify "Shutdown unvollständig – Terminal prüfen."
  say "Mindestens ein Node konnte nicht heruntergefahren werden."
  read -r -p "Enter zum Schließen ..." _
fi
