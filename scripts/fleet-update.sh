#!/bin/bash

# Nodes gemäß ~/.ssh/config inklusive NAS (deraldehesse)
REMOTE_NODES=("outerheaven" "fox" "deraldehesse" "metal-dgear" "rex" "ray")
TMP_DIR=$(mktemp -d /tmp/fleet-update-XXXXXX)
PIDS=()

APT_CMD='sudo apt update && sudo apt upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" && sudo apt autoremove -y'

echo "=========================================================="
echo "          FLEET UPDATE: PARALLELE SYSTEM-WARTUNG          "
echo "=========================================================="
echo ""

# 1. bigboss lokal
printf " [+] %-14s ist ONLINE -> Update im Hintergrund gestartet.\n" "bigboss"
(
    if eval "$APT_CMD"; then
        echo "OK" > "$TMP_DIR/bigboss.status"
    else
        echo "FAILED" > "$TMP_DIR/bigboss.status"
    fi
) > "$TMP_DIR/bigboss.log" 2>&1 &
PIDS+=($!)

# 2. Remote-Nodes
for node in "${REMOTE_NODES[@]}"; do
    if ssh -o BatchMode=yes -o ConnectTimeout=3 "$node" true 2>/dev/null; then
        printf " [+] %-14s ist ONLINE -> Update im Hintergrund gestartet.\n" "$node"
        (
            if ssh "$node" "$APT_CMD"; then
                echo "OK" > "$TMP_DIR/$node.status"
            else
                echo "FAILED" > "$TMP_DIR/$node.status"
            fi
        ) > "$TMP_DIR/$node.log" 2>&1 &
        PIDS+=($!)
    else
        printf " [-] %-14s ist OFFLINE -> Übersprungen.\n" "$node"
        echo "OFFLINE" > "$TMP_DIR/$node.status"
    fi
done

echo ""
echo "Alle Jobs laufen parallel. Warte auf Fertigstellung..."
echo "----------------------------------------------------------"

for pid in "${PIDS[@]}"; do
    wait "$pid"
done

echo ""
echo "=========================================================="
echo "                     ABSCHLUSS-REPORT                     "
echo "=========================================================="

ALL_NODES=("bigboss" "${REMOTE_NODES[@]}")
for node in "${ALL_NODES[@]}"; do
    status="UNKNOWN"
    [ -f "$TMP_DIR/$node.status" ] && status=$(cat "$TMP_DIR/$node.status")

    case "$status" in
        "OK")
            printf " [  OK   ]  %-14s : Erfolgreich aktualisiert.\n" "$node"
            ;;
        "OFFLINE")
            printf " [OFFLINE]  %-14s : Nicht erreichbar (übersprungen).\n" "$node"
            ;;
        "FAILED")
            printf " [FEHLER ]  %-14s : Fehlgeschlagen! Log: %s/%s.log\n" "$node" "$TMP_DIR" "$node"
            ;;
        *)
            printf " [   ?   ]  %-14s : Unbekannter Status.\n" "$node"
            ;;
    esac
done

echo "=========================================================="
echo "Detaillierte Logs liegen unter: $TMP_DIR"
echo ""
if [ -t 0 ]; then
    read -p "Drücke [Enter], um dieses Fenster zu schließen..."
fi
