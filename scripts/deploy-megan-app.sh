#!/usr/bin/env bash
set -e

# Java-Pfad automatisch ermitteln (Android Studio JBR oder System-Java)
if [ -d "/home/bigboss/android-studio/jbr" ]; then
    export JAVA_HOME="/home/bigboss/android-studio/jbr"
elif [ -d "/opt/android-studio/jbr" ]; then
    export JAVA_HOME="/opt/android-studio/jbr"
elif [ -d "/usr/lib/jvm/default-java" ]; then
    export JAVA_HOME="/usr/lib/jvm/default-java"
else
    # Fallback: Suche erstes passendes JDK
    AS_JBR=$(find /home/bigboss -maxdepth 4 -type d -name "jbr" 2>/dev/null | head -n 1)
    if [ -n "$AS_JBR" ]; then
        export JAVA_HOME="$AS_JBR"
    fi
fi

if [ -n "$JAVA_HOME" ]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

echo "=========================================="
echo "📱 Megan App - 1-Klick ADB Deployer"
echo "=========================================="
echo "JAVA_HOME gesetzt auf: $JAVA_HOME"
echo "Prüfe ADB-Verbindung..."
adb wait-for-device
echo "Gerät erkannt!"

# Ins Projektverzeichnis wechseln
if [ -d "/home/bigboss/AndroidStudioProjects/OpenUp" ]; then
    cd /home/bigboss/AndroidStudioProjects/OpenUp
elif [ -d "/home/bigboss/megan-mobile-companion" ]; then
    cd /home/bigboss/megan-mobile-companion
else
    echo "Fehler: Projektordner nicht gefunden!"
    exit 1
fi

echo "Starte Gradle Debug-Build..."
if ! ./gradlew assembleDebug; then
    if [ -f "app/build/outputs/apk/debug/app-debug.apk" ]; then
        echo "Warnung: Build fehlgeschlagen oder offline - verwende existierendes APK..."
    else
        echo "Fehler: Build fehlgeschlagen und kein existierendes APK gefunden!"
        exit 1
    fi
fi

echo "Installiere Megan App via ADB..."
MEGAN_APK="$(pwd)/app/build/outputs/apk/debug/app-debug.apk"
ln -sf "$MEGAN_APK" /home/bigboss/Desktop/APKs/megan-app.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/megan-app.apk /home/bigboss/Desktop/megan-app.apk 2>/dev/null || true
cp -f "$MEGAN_APK" /home/bigboss/outerheaven-cluster-repo/mission-control/static/megan.apk 2>/dev/null || true
adb install -r -d -g "$MEGAN_APK"

echo "=========================================="
echo "✅ Erfolgreich auf dem Smartphone installiert!"
echo "=========================================="
notify-send "Megan App" "Erfolgreich auf Handy installiert!"

echo "Fertig! Fenster schließt in 3 Sekunden..."
sleep 3
exit 0
