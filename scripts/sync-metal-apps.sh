#!/bin/bash
set -e

LOCAL_APK_DIR="$HOME/Desktop/APKs"
TMP_DIR="/tmp/metal_apk_sync"
mkdir -p "$LOCAL_APK_DIR" "$TMP_DIR"

# Migration bestehender Legacy-Versionen (_v1, _v2 ...) auf das 1.0.x-Schema (_v1.0.x)
for legacy_file in "$LOCAL_APK_DIR"/*_v[0-9].apk "$LOCAL_APK_DIR"/*_v[0-9][0-9].apk; do
    if [ -f "$legacy_file" ]; then
        BASE_NAME=$(basename "$legacy_file" .apk)
        APP_PREFIX=$(echo "$BASE_NAME" | sed -n "s/\(.*\)_v[0-9]*$/\1/p")
        V_NUM=$(echo "$BASE_NAME" | sed -n "s/.*_v\([0-9]*\)$/\1/p")
        if [ -n "$APP_PREFIX" ] && [ -n "$V_NUM" ]; then
            if [ "$APP_PREFIX" == "CatlordApp" ] || [ "$APP_PREFIX" == "VoiceRoadmap" ] || [ "$APP_PREFIX" == "PaperworkScanner" ]; then
                TARGET_SEMVER="$LOCAL_APK_DIR/${APP_PREFIX}_v1.0.0.apk"
            else
                TARGET_SEMVER="$LOCAL_APK_DIR/${APP_PREFIX}_v1.0.${V_NUM}.apk"
            fi
            if [ ! -f "$TARGET_SEMVER" ]; then
                echo "Migration: $(basename "$legacy_file") -> $(basename "$TARGET_SEMVER")"
                mv "$legacy_file" "$TARGET_SEMVER"
            else
                rm -f "$legacy_file"
            fi
        fi
    fi
done

# Definition der Apps: "Anzeigename|Remote-Pfad"
APPS=(
  "PaperworkScanner|/mnt/storage-fast/build-workspace/PaperworkScanner/app/build/outputs/apk/debug/app-debug.apk"
  "MeganApp|/mnt/storage-fast/build-workspace/OpenUp/app/build/outputs/apk/debug/app-debug.apk"
  "VoiceRoadmap|/mnt/storage-fast/outerheaven-cluster-repo/apps/new-horizon-voice-app/app/build/outputs/apk/debug/app-debug.apk"
  "WebIdeCompanion|/mnt/storage-fast/outerheaven-cluster-repo/web-ide-app/app/build/outputs/apk/debug/app-debug.apk"
  "CatlordApp|/mnt/storage-fast/outerheaven-cluster-repo/apps/catlord-app/app/build/outputs/apk/debug/app-debug.apk"
)

echo "=========================================================="
echo "    Synchronisiere Android-Apps von metal-dgear (1.0.x)   "
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
        # 3. Höchste vorhandene Versionsnummer (_v1.0.X) ermitteln
        LAST_PATCH=-1
        for f in "$LOCAL_APK_DIR/${APP_NAME}_v1.0."*.apk; do
            if [ -f "$f" ]; then
                PATCH=$(basename "$f" | sed -n "s/^${APP_NAME}_v1\.0\.\([0-9]*\)\.apk$/\1/p")
                if [[ "$PATCH" =~ ^[0-9]+$ ]] && [ "$PATCH" -gt "$LAST_PATCH" ]; then
                    LAST_PATCH=$PATCH
                fi
            fi
        done

        if [ "$LAST_PATCH" -ge 0 ]; then
            NEW_PATCH=$((LAST_PATCH + 1))
        else
            NEW_PATCH=0
        fi

        TARGET_APK="$LOCAL_APK_DIR/${APP_NAME}_v1.0.${NEW_PATCH}.apk"

        mv "$TMP_APK" "$TARGET_APK"
        ln -sf "$TARGET_APK" "$LATEST_SYMLINK"

        # Symlinks für Abwärtskompatibilität und bequemen Zugriff pflegen
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

        echo "    [+] Neue Version erzeugt: ${APP_NAME}_v1.0.${NEW_PATCH}.apk ($SIZE)"
    fi
done

rm -rf "$TMP_DIR"
echo ""
echo "=========================================================="
echo "Sync abgeschlossen! Aktuelle Dateien in ~/Desktop/APKs/:"
echo "=========================================================="
ls -l "$LOCAL_APK_DIR"/*.apk
echo ""
read -t 5 -p "Fenster schließt automatisch oder drücke Enter..." || true
echo ""
