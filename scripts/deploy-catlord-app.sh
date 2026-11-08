#!/usr/bin/env bash
set -e

echo "=========================================="
echo "🐾 Metal-Dgear Catlord Studio App - ADB Deployer"
echo "=========================================="

LOCAL_APK_DIR="$HOME/Desktop/APKs"
APK_PATH="$LOCAL_APK_DIR/catlord-app.apk"
REMOTE_APK="/mnt/storage-fast/outerheaven-cluster-repo/apps/catlord-app/app/build/outputs/apk/debug/app-debug.apk"
APP_ID="com.outerheaven.catlord"

mkdir -p "$LOCAL_APK_DIR"

# Falls lokale APK fehlt oder aktualisiert werden soll, synchronisiere von metal-dgear
echo "Prüfe APK von metal-dgear..."
if scp -q "metal-dgear:$REMOTE_APK" "$LOCAL_APK_DIR/CatlordApp_incoming.apk" 2>/dev/null; then
    mv -f "$LOCAL_APK_DIR/CatlordApp_incoming.apk" "$LOCAL_APK_DIR/CatlordApp_latest.apk"
    ln -sf "$LOCAL_APK_DIR/CatlordApp_latest.apk" "$APK_PATH"
    echo "Aktuelle Catlord Studio APK von metal-dgear synchronisiert."
fi

if [ ! -f "$APK_PATH" ]; then
    echo "❌ Keine Catlord Studio APK gefunden unter: $APK_PATH"
    notify-send -u critical "Catlord Studio" "APK nicht gefunden!" 2>/dev/null || true
    exit 1
fi

echo "Prüfe ADB-Verbindung zum Smartphone..."
adb wait-for-device
echo "Gerät erkannt!"

echo "Installiere $APK_PATH via ADB..."
adb install -r -t -g "$APK_PATH"

echo "Starte MainActivity auf dem Zielgerät..."
adb shell am start -n "$APP_ID/.MainActivity" 2>/dev/null || true

echo "=========================================="
echo "✅ Catlord Studio erfolgreich installiert & gestartet!"
echo "=========================================="
notify-send "Catlord Studio" "Erfolgreich auf Handy installiert!" 2>/dev/null || true
echo "Fertig! Fenster schließt in 3 Sekunden..."
sleep 3
exit 0
