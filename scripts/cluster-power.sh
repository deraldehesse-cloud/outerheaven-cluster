#!/usr/bin/env bash
# ======================================================================
# Cluster Power Management für Sebastian (Shutdown, Cleanup & Reboot)
# Host Laptop: Big Boss (User: bigboss)
# ======================================================================

# Linux-Äquivalent zum Windows DISM/Component-Cleanup
LINUX_CLEAN="sudo DEBIAN_FRONTEND=noninteractive apt-get autoremove -y && sudo apt-get clean && sudo journalctl --vacuum-time=3d"

CHOICE=$(zenity --list --title="Outer Heaven Cluster Power Management" \
  --column="Nr." --column="Aktion" \
  "1" "Kompletten Cluster BEREINIGEN & HERUNTERFAHREN (OH -> Rex/Ray -> Fox -> NAS)" \
  "2" "Kompletten Cluster NEUSTARTEN (Reboot aller aktiven Knoten)" \
  "3" "Nur Outer Heaven bereinigen & herunterfahren" \
  "4" "Nur Outer Heaven neustarten" \
  "5" "Nur Fox bereinigen & herunterfahren (Paperless / Research Node)" \
  "6" "Nur Fox neustarten" \
  "7" "Nur Thin Clients (Rex & Ray) bereinigen & herunterfahren" \
  "8" "Nur Der Alde Hesse herunterfahren (NAS / Storage)" \
  "9" "Nur Der Alde Hesse neustarten" \
  --height=430 --width=620 2>/dev/null)

case "$CHOICE" in
  1)
    echo "Schritt A: Cleanup & Shutdown Outer Heaven..."
    ssh -o ConnectTimeout=3 outerheaven "$LINUX_CLEAN; sudo poweroff" || true

    echo "Schritt B: Cleanup & Shutdown Thin Clients (falls online)..."
    ssh -o ConnectTimeout=2 rex "$LINUX_CLEAN; sudo poweroff" 2>/dev/null || echo "Rex offline oder übersprungen"
    ssh -o ConnectTimeout=2 ray "$LINUX_CLEAN; sudo poweroff" 2>/dev/null || echo "Ray offline oder übersprungen"

    echo "Schritt C: Cleanup & Shutdown Fox..."
    ssh -o ConnectTimeout=3 fox "$LINUX_CLEAN; sudo poweroff" || true

    echo "Schritt D: Fahre Der Alde Hesse (NAS) herunter..."
    ssh -o ConnectTimeout=3 deraldehesse "sudo poweroff" || true

    zenity --info --text="Cleanup durchgeführt und Shutdown-Signal an alle Nodes gesendet." --timeout=4 2>/dev/null
    ;;
  2)
    echo "Starte Outer Heaven neu..."
    ssh -o ConnectTimeout=3 outerheaven "sudo reboot" 2>/dev/null || true
    echo "Starte Thin Clients neu (falls online)..."
    ssh -o ConnectTimeout=2 rex "sudo reboot" 2>/dev/null || true
    ssh -o ConnectTimeout=2 ray "sudo reboot" 2>/dev/null || true
    echo "Starte Fox neu..."
    ssh -o ConnectTimeout=3 fox "sudo reboot" 2>/dev/null || true
    echo "Starte Der Alde Hesse neu..."
    ssh -o ConnectTimeout=3 deraldehesse "sudo reboot" 2>/dev/null || true
    zenity --info --text="Reboot-Signal an alle aktiven Nodes gesendet." --timeout=3 2>/dev/null
    ;;
  3)
    echo "Cleanup & Shutdown Outer Heaven..."
    ssh -o ConnectTimeout=3 outerheaven "$LINUX_CLEAN; sudo poweroff" || true
    zenity --info --text="Outer Heaven bereinigt und heruntergefahren." --timeout=3 2>/dev/null
    ;;
  4)
    echo "Starte Outer Heaven neu..."
    ssh -o ConnectTimeout=3 outerheaven "sudo reboot" || true
    zenity --info --text="Outer Heaven startet neu." --timeout=3 2>/dev/null
    ;;
  5)
    echo "Cleanup & Shutdown Fox..."
    ssh -o ConnectTimeout=3 fox "$LINUX_CLEAN; sudo poweroff" || true
    zenity --info --text="Fox bereinigt und heruntergefahren." --timeout=3 2>/dev/null
    ;;
  6)
    echo "Starte Fox neu..."
    ssh -o ConnectTimeout=3 fox "sudo reboot" || true
    zenity --info --text="Fox startet neu." --timeout=3 2>/dev/null
    ;;
  7)
    echo "Cleanup & Shutdown Thin Clients..."
    ssh -o ConnectTimeout=2 rex "$LINUX_CLEAN; sudo poweroff" 2>/dev/null || echo "Rex offline"
    ssh -o ConnectTimeout=2 ray "$LINUX_CLEAN; sudo poweroff" 2>/dev/null || echo "Ray offline"
    zenity --info --text="Thin Clients bereinigt und abgeschaltet." --timeout=3 2>/dev/null
    ;;
  8)
    echo "Fahre Der Alde Hesse (NAS) herunter..."
    ssh -o ConnectTimeout=3 deraldehesse "sudo poweroff" || true
    zenity --info --text="Der Alde Hesse (NAS) fährt herunter." --timeout=3 2>/dev/null
    ;;
  9)
    echo "Starte Der Alde Hesse (NAS) neu..."
    ssh -o ConnectTimeout=3 deraldehesse "sudo reboot" || true
    zenity --info --text="Der Alde Hesse (NAS) startet neu." --timeout=3 2>/dev/null
    ;;
esac
