#!/usr/bin/env bash
set -e

# Java-Pfad ermitteln
if [ -d "/home/bigboss/android-studio/jbr" ]; then
    export JAVA_HOME="/home/bigboss/android-studio/jbr"
elif [ -d "/usr/lib/jvm/default-java" ]; then
    export JAVA_HOME="/usr/lib/jvm/default-java"
fi

if [ -n "$JAVA_HOME" ]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

echo "=========================================="
echo "⚡ Hermes Agent - 1-Klick ADB Deployer"
echo "=========================================="
echo "Warte auf Android-Gerät per ADB..."
adb wait-for-device
echo "Gerät erkannt!"

if [ -d "/home/bigboss/AndroidStudioProjects/HermesAgentApp" ]; then
    echo "Starte Gradle Debug-Build für HermesAgentApp..."
    cd /home/bigboss/AndroidStudioProjects/HermesAgentApp
    if ./gradlew assembleDebug; then
        cp -f app/build/outputs/apk/debug/app-debug.apk /home/bigboss/Desktop/APKs/hermes-app.apk 2>/dev/null || true
    else
        echo "Warnung: Build fehlgeschlagen - verwende existierendes APK falls vorhanden..."
    fi
fi

APK_PATH="/home/bigboss/Desktop/hermes-app.apk"
if [ ! -f "$APK_PATH" ]; then
    APK_PATH="/home/bigboss/AndroidStudioProjects/HermesAgentApp/app/build/outputs/apk/debug/app-debug.apk"
fi

if [ -f "$APK_PATH" ]; then
    echo "Installiere Hermes Agent ($APK_PATH)..."
    adb install -r "$APK_PATH"
    echo "=========================================="
    echo "✅ Erfolgreich auf dem Smartphone installiert!"
    echo "=========================================="
    notify-send "Hermes Agent" "Erfolgreich auf Handy installiert!"
    sleep 3
    exit 0
else
    echo "❌ Fehler: APK nicht gefunden ($APK_PATH)!"
    notify-send -u critical "Hermes Agent" "APK nicht gefunden!"
    exit 1
fi
