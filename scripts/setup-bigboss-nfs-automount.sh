#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Setup NFS On-Demand Automount on bigboss (Laptop)
# Mounts /mnt/storage-beta from outerheaven (100.116.38.29)
# ==============================================================================

if [ "$EUID" -ne 0 ]; then
    echo "[!] Dieses Skript muss mit Root-Rechten (sudo) ausgeführt werden."
    exit 1
fi

REMOTE_SERVER="100.116.38.29"
REMOTE_EXPORT="/mnt/storage-beta"
MOUNT_POINT="/mnt/storage-beta"
FSTAB_LINE="100.116.38.29:/mnt/storage-beta /mnt/storage-beta nfs noauto,x-systemd.automount,x-systemd.idle-timeout=60,timeo=14,intr,_netdev 0 0"

echo "[1/5] Prüfe nfs-common Installation..."
if ! dpkg -l | grep -q "nfs-common"; then
    echo "      Installiere nfs-common..."
    apt update -qq && apt install -y -qq nfs-common
else
    echo "      [OK] nfs-common ist bereits installiert."
fi

echo "[2/5] Stelle lokalen Mountpoint sicher..."
if [ ! -d "$MOUNT_POINT" ]; then
    echo "      Erstelle $MOUNT_POINT..."
    mkdir -p "$MOUNT_POINT"
else
    echo "      [OK] $MOUNT_POINT existiert bereits."
fi

echo "[3/5] Konfiguriere /etc/fstab..."
if grep -q "100.116.38.29:/mnt/storage-beta" /etc/fstab; then
    echo "      [OK] Eintrag ist bereits in /etc/fstab vorhanden."
else
    echo "      Erstelle Backup /etc/fstab.bak..."
    cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d_%H%M%S)"
    echo "      Füge Automount-Eintrag hinzu..."
    echo "" >> /etc/fstab
    echo "# Outer Heaven storage-beta NFS On-Demand Automount (Tailscale)" >> /etc/fstab
    echo "$FSTAB_LINE" >> /etc/fstab
    echo "      [OK] Eintrag erfolgreich hinzugefügt."
fi

echo "[4/5] Lade systemd Konfiguration neu..."
systemctl daemon-reload
systemctl restart "mnt-storage\x2dbeta.automount"

echo "[5/5] Teste Zugriff auf /mnt/storage-beta/projekte/..."
if ls -la /mnt/storage-beta/projekte/ > /dev/null 2>&1; then
    echo "      [✔] ERFOLGREICH: /mnt/storage-beta/projekte/ ist erreichbar!"
    ls -lh /mnt/storage-beta/projekte/
else
    echo "      [!] Warnung: Zugriff fehlgeschlagen. Prüfe Status der Unit:"
    systemctl status "mnt-storage\x2dbeta.automount" --no-pager || true
    exit 1
fi

echo "[✔] NFS-Automount Einrichtung erfolgreich abgeschlossen!"
