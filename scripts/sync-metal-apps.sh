#!/bin/bash
set -e

LOCAL_APK_DIR="$HOME/Desktop/APKs"
TMP_DIR="/tmp/metal_apk_sync"
mkdir -p "$LOCAL_APK_DIR" "$TMP_DIR"

# Definition der Apps: "Anzeigename|Remote-Pfad"
APPS=(
  "PaperworkScanner|/mnt/storage-fast/build-workspace/PaperworkScanner/app/build/outputs/apk/debug/app-debug.apk"
  "MeganApp|/mnt/storage-fast/build-workspace/OpenUp/app/build/outputs/apk/debug/app-debug.apk"
  "VoiceRoadmap|/mnt/storage-fast/outerheaven-cluster-repo/apps/new-horizon-voice-app/app/build/outputs/apk/debug/app-debug.apk"
  "WebIdeCompanion|/mnt/storage-fast/outerheaven-cluster-repo/web-ide-app/app/build/outputs/apk/debug/app-debug.apk"
  "CatlordApp|/mnt/storage-fast/outerheaven-cluster-repo/apps/catlord-app/app/build/outputs/apk/debug/app-debug.apk"
)

echo "=========================================================="
echo "       Synchronisiere Android-Apps von metal-dgear        "
echo "=========================================================="

for entry in "${APPS[@]}"; do
    IFS="|" read -r APP_NAME REMOTE_PATH <<< "$entry"
    TMP_APK="$TMP_DIR/${APP_NAME}_incoming.apk"

    echo ""
    echo ">>> Prüfe: $APP_NAME ..."

    # 1. Temporär herunterladen
    if ! scp -q "metal-dgear:$REMOTE_PATH" "$TMP_APK" 2>/dev/null; then
        echo "    [-] Nicht gefunden oder Build fehlt: $REMOTE_PATH"
        continue
    fi

    SIZE=$(du -h "$TMP_APK" | cut -f1)

    # 2. Prüfen, ob bereits eine Version existiert und ob sich die Datei geändert hat
    LATEST_SYMLINK="$LOCAL_APK_DIR/${APP_NAME}.apk"
    NEW_HASH=$(sha256sum "$TMP_APK" | awk '{print $1}')
    CURRENT_HASH=""

    if [ -f "$LATEST_SYMLINK" ]; then
        CURRENT_HASH=$(sha256sum "$LATEST_SYMLINK" | awk '{print $1}')
    fi

    if [ "$NEW_HASH" == "$CURRENT_HASH" ] && [ -n "$CURRENT_HASH" ]; then
        CURRENT_FILE=$(readlink -f "$LATEST_SYMLINK")
        VERSION_TAG=$(basename "$CURRENT_FILE" .apk)
        echo "    [=] Keine Änderung: Bereits auf aktuellem Stand ($VERSION_TAG, $SIZE)."
        rm -f "$TMP_APK"
    else
        # 3. Höchste vorhandene Versionsnummer ermitteln
        LAST_NUM=0
        for f in "$LOCAL_APK_DIR/${APP_NAME}_v"*.apk; do
            if [ -f "$f" ]; then
                NUM=$(basename "$f" | sed -n "s/^${APP_NAME}_v\([0-9]*\)\.apk$/\1/p")
                if [[ "$NUM" =~ ^[0-9]+$ ]] && [ "$NUM" -gt "$LAST_NUM" ]; then
                    LAST_NUM=$NUM
                fi
            fi
        done

        NEW_NUM=$((LAST_NUM + 1))
        TARGET_APK="$LOCAL_APK_DIR/${APP_NAME}_v${NEW_NUM}.apk"

        mv "$TMP_APK" "$TARGET_APK"
        ln -sf "$TARGET_APK" "$LATEST_SYMLINK"

        # Symlinks für Abwärtskompatibilität pflegen
        if [ "$APP_NAME" == "PaperworkScanner" ]; then
            ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/paperless-app.apk"
        elif [ "$APP_NAME" == "MeganApp" ]; then
            ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/megan-app.apk"
        elif [ "$APP_NAME" == "VoiceRoadmap" ]; then
            ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/new-horizon.apk"
        elif [ "$APP_NAME" == "WebIdeCompanion" ]; then
            ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/web-ide-app.apk"
        elif [ "$APP_NAME" == "CatlordApp" ]; then
            ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/catlord-app.apk"
        fi

        echo "    [+] Neue Version erzeugt: ${APP_NAME}_v${NEW_NUM}.apk ($SIZE)"
    fi
done

rm -rf "$TMP_DIR"
echo ""
echo "=========================================================="
echo "Sync abgeschlossen! Aktuelle Dateien in ~/Desktop/APKs/:"
echo "=========================================================="
ls -l "$LOCAL_APK_DIR"/*.apk
echo ""
read -p "Drücke Enter zum Schließen..."
