#!/usr/bin/env bash
# cluster-power.sh - Graceful Power Management für das Outer Heaven Cluster

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
    echo "Fahre Fox herunter..."
    ssh -o ConnectTimeout=2 fox "sudo shutdown -h now" 2>/dev/null || true
    echo "Fahre Der Alde Hesse herunter..."
    ssh -o ConnectTimeout=2 deraldehesse "sudo shutdown -h now" 2>/dev/null || true
    echo "Fahre Outer Heaven herunter..."
    ssh -o ConnectTimeout=2 outerheaven "sudo shutdown -h now" 2>/dev/null || true
    ;;
  2)
    ssh -o ConnectTimeout=2 outerheaven "sudo shutdown -h now"
    ;;
  3)
    ssh -o ConnectTimeout=2 fox "sudo shutdown -h now"
    ;;
  4)
    ssh -o ConnectTimeout=2 deraldehesse "sudo shutdown -h now"
    ;;
  5)
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
