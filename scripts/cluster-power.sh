#!/usr/bin/env bash
# cluster-power.sh - Graceful Power Management für das Outer Heaven Cluster

# Megan verabschiedet sich vor dem Herunterfahren/Neustart von Outer Heaven
# (Push über WebSocket, mit ~50 % Chance noch ein Feierabend-Schnappschuss; wartet bis zu ~75 s auf das Bild)
megan_farewell() {
  echo "Megan verabschiedet sich..."
  ssh -o ConnectTimeout=3 -o BatchMode=yes outerheaven \
    "curl -s -m 100 -X POST 'http://127.0.0.1:8080/api/system/shutdown-warning?wait=75'" >/dev/null 2>&1 || true
}

CHOICE=$(zenity --list --title="Cluster Power Management" \
  --column="Nr." --column="Aktion" \
  "1" "Alle Rechner herunterfahren (Cluster Shutdown)" \
  "2" "Nur Outer Heaven herunterfahren" \
  "3" "Nur Fox herunterfahren" \
  "4" "Nur Der Alde Hesse (NAS) herunterfahren" \
  "5" "Outer Heaven neu starten (Reboot)" \
  "6" "Fox neu starten (Reboot)" \
  "7" "Der Alde Hesse (NAS) neu starten (Reboot)" \
  --height=360 --width=520)

case "$CHOICE" in
  1)
    megan_farewell
    echo "Fahre Fox herunter..."
    ssh -o ConnectTimeout=2 fox "sudo shutdown -h now" 2>/dev/null || true
    echo "Fahre Der Alde Hesse herunter..."
    ssh -o ConnectTimeout=2 deraldehesse "sudo shutdown -h now" 2>/dev/null || true
    echo "Fahre Outer Heaven herunter..."
    ssh -o ConnectTimeout=2 outerheaven "sudo shutdown -h now" 2>/dev/null || true
    ;;
  2)
    megan_farewell
    ssh -o ConnectTimeout=2 outerheaven "sudo shutdown -h now"
    ;;
  3)
    ssh -o ConnectTimeout=2 fox "sudo shutdown -h now"
    ;;
  4)
    ssh -o ConnectTimeout=2 deraldehesse "sudo shutdown -h now"
    ;;
  5)
    megan_farewell
    echo "Starte Outer Heaven neu..."
    ssh -o ConnectTimeout=2 outerheaven "sudo reboot"
    ;;
  6)
    echo "Starte Fox neu..."
    ssh -o ConnectTimeout=2 fox "sudo reboot"
    ;;
  7)
    echo "Starte Der Alde Hesse neu..."
    ssh -o ConnectTimeout=2 deraldehesse "sudo reboot"
    ;;
esac
