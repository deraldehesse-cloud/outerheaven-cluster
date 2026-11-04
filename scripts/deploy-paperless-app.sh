#!/usr/bin/env bash
set -e

# Java-Pfad automatisch ermitteln (Android Studio JBR oder System-Java)
if [ -d "/home/bigboss/android-studio/jbr" ]; then
    export JAVA_HOME="/home/bigboss/android-studio/jbr"
elif [ -d "/opt/android-studio/jbr" ]; then
    export JAVA_HOME="/opt/android-studio/jbr"
elif [ -d "/snap/android-studio/current/jbr" ]; then
    export JAVA_HOME="/snap/android-studio/current/jbr"
elif [ -d "/usr/lib/jvm/default-java" ]; then
    export JAVA_HOME="/usr/lib/jvm/default-java"
else
    AS_JBR=$(find /home/bigboss /snap/android-studio -maxdepth 4 -type d -name "jbr" 2>/dev/null | head -n 1)
    if [ -n "$AS_JBR" ]; then
        export JAVA_HOME="$AS_JBR"
    fi
fi

if [ -n "$JAVA_HOME" ]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

echo "=========================================="
echo "📄 Paperless App (OpenUp) - 1-Click ADB Deployer"
echo "=========================================="
echo "JAVA_HOME gesetzt auf: ${JAVA_HOME:-System-Default}"
echo "Prüfe ADB-Verbindung..."
adb wait-for-device
echo "Gerät erkannt!"

PROJECT_DIR="/home/bigboss/AndroidStudioProjects/PaperworkScanner"
APK_OUTPUT="$PROJECT_DIR/app/build/outputs/apk/debug/app-debug.apk"
APP_ID="com.example.paperwork_scanner"

if [ -d "$PROJECT_DIR" ]; then
    cd "$PROJECT_DIR"
    echo "Starte sauberen Gradle Debug-Build (clean assembleDebug)..."
    ./gradlew clean assembleDebug
    cp -f "$APK_OUTPUT" /home/bigboss/Desktop/paperless-app.apk 2>/dev/null || true
    cp -f "$APK_OUTPUT" /home/bigboss/Desktop/OpenUp-Setup.apk 2>/dev/null || true
    cp -f "$APK_OUTPUT" /home/bigboss/Desktop/APKs/paperless-app.apk 2>/dev/null || true
fi

if [ -f "$APK_OUTPUT" ]; then
    APK_PATH="$APK_OUTPUT"
elif [ -f "/home/bigboss/Desktop/paperless-app.apk" ]; then
    APK_PATH="/home/bigboss/Desktop/paperless-app.apk"
elif [ -f "/home/bigboss/Desktop/OpenUp-Setup.apk" ]; then
    APK_PATH="/home/bigboss/Desktop/OpenUp-Setup.apk"
else
    echo "❌ Keine Paperless APK gefunden!"
    notify-send -u critical "Paperless App" "APK nicht gefunden!" 2>/dev/null || true
    exit 1
fi

echo "Installiere $APK_PATH via ADB (inkl. Berechtigungsvergabe)..."
adb install -r -t -g "$APK_PATH"

echo "Setze Laufzeit-Berechtigungen für $APP_ID..."
for PERM in \
    android.permission.CAMERA \
    android.permission.READ_MEDIA_IMAGES \
    android.permission.POST_NOTIFICATIONS \
    android.permission.READ_EXTERNAL_STORAGE \
    android.permission.WRITE_EXTERNAL_STORAGE; do
    adb shell pm grant "$APP_ID" "$PERM" 2>/dev/null || true
done

echo "Starte MainActivity auf dem Zielgerät..."
adb shell am start -n "$APP_ID/.MainActivity"

echo "=========================================="
echo "✅ Paperless App erfolgreich gebaut, installiert & gestartet!"
echo "=========================================="
notify-send "Paperless App" "Erfolgreich auf Handy installiert & gestartet!" 2>/dev/null || true
echo "Fertig! Fenster schließt in 3 Sekunden..."
sleep 3
exit 0
