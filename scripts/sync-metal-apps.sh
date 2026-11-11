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

# Definition der Apps: "Anzeigename|Remote-Pfad" (Megan App als Prio 1)
APPS=(
  "MeganApp|/mnt/storage-fast/build-workspace/OpenUp/app/build/outputs/apk/debug/app-debug.apk"
  "PaperworkScanner|/mnt/storage-fast/build-workspace/PaperworkScanner/app/build/outputs/apk/debug/app-debug.apk"
  "VoiceRoadmap|/mnt/storage-fast/outerheaven-cluster-repo/apps/new-horizon-voice-app/app/build/outputs/apk/debug/app-debug.apk"
  "WebIdeCompanion|/mnt/storage-fast/outerheaven-cluster-repo/web-ide-app/app/build/outputs/apk/debug/app-debug.apk"
  "CatlordApp|/mnt/storage-fast/outerheaven-cluster-repo/apps/catlord-app/app/build/outputs/apk/debug/app-debug.apk"
)

echo "=========================================================="
echo "    Synchronisiere Android-Apps von metal-dgear (1.0.x)   "
echo "=========================================================="

UPDATED_APKS=()

for entry in "${APPS[@]}"; do
    IFS="|" read -r APP_NAME REMOTE_PATH <<< "$entry"
    TMP_APK="$TMP_DIR/${APP_NAME}_incoming.apk"

    echo ""
    echo ">>> Prüfe: $APP_NAME ..."

    # Bei MeganApp: Prüfe auch lokalen Build auf bigboss
    LOCAL_BUILT=""
    if [ "$APP_NAME" == "MeganApp" ]; then
        if [ -f "/home/bigboss/megan-mobile-companion/app/build/outputs/apk/debug/app-debug.apk" ]; then
            LOCAL_BUILT="/home/bigboss/megan-mobile-companion/app/build/outputs/apk/debug/app-debug.apk"
        elif [ -f "/home/bigboss/AndroidStudioProjects/OpenUp/app/build/outputs/apk/debug/app-debug.apk" ]; then
            LOCAL_BUILT="/home/bigboss/AndroidStudioProjects/OpenUp/app/build/outputs/apk/debug/app-debug.apk"
        fi
    fi

    # 1. Herunterladen von metal-dgear oder lokalen Build verwenden
    FETCHED=0
    if [ -n "$LOCAL_BUILT" ]; then
        cp -f "$LOCAL_BUILT" "$TMP_APK"
        FETCHED=1
        # Synchronisiere lokalen Build nach metal-dgear für Konsistenz
        scp -q "$LOCAL_BUILT" "metal-dgear:$REMOTE_PATH" 2>/dev/null || true
    fi

    if [ "$FETCHED" -eq 0 ]; then
        if ! scp -q "metal-dgear:$REMOTE_PATH" "$TMP_APK" 2>/dev/null; then
            echo "    [-] Nicht gefunden oder Build fehlt: $REMOTE_PATH"
            continue
        fi
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
        TARGET_APK="$CURRENT_FILE"
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
        UPDATED_APKS+=("$LATEST_SYMLINK")
        echo "    [+] Neue Version erzeugt: ${APP_NAME}_v1.0.${NEW_PATCH}.apk ($SIZE)"
    fi

    # Symlinks für Abwärtskompatibilität und bequemen Zugriff pflegen (APKs-Ordner & Desktop)
    if [ "$APP_NAME" == "PaperworkScanner" ]; then
        ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/paperless-app.apk"
        ln -sf "$LOCAL_APK_DIR/paperless-app.apk" "$HOME/Desktop/paperless-app.apk" 2>/dev/null || true
    elif [ "$APP_NAME" == "MeganApp" ]; then
        ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/megan-app.apk"
        ln -sf "$LOCAL_APK_DIR/megan-app.apk" "$HOME/Desktop/megan-app.apk" 2>/dev/null || true
        ln -sf "$LATEST_SYMLINK" "$HOME/Desktop/MeganApp.apk" 2>/dev/null || true
    elif [ "$APP_NAME" == "VoiceRoadmap" ]; then
        ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/new-horizon.apk"
        ln -sf "$LOCAL_APK_DIR/new-horizon.apk" "$HOME/Desktop/new-horizon.apk" 2>/dev/null || true
        ln -sf "$LATEST_SYMLINK" "$HOME/Desktop/VoiceRoadmap.apk" 2>/dev/null || true
    elif [ "$APP_NAME" == "WebIdeCompanion" ]; then
        ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/web-ide-app.apk"
        ln -sf "$LOCAL_APK_DIR/web-ide-app.apk" "$HOME/Desktop/web-ide-app.apk" 2>/dev/null || true
    elif [ "$APP_NAME" == "CatlordApp" ]; then
        ln -sf "$TARGET_APK" "$LOCAL_APK_DIR/catlord-app.apk"
        ln -sf "$LOCAL_APK_DIR/catlord-app.apk" "$HOME/Desktop/catlord-app.apk" 2>/dev/null || true
    fi
done

rm -rf "$TMP_DIR"

# 4. ADB-Geräte prüfen & direkt aktualisieren (-r -d)
CONNECTED_DEVICES=$(adb devices 2>/dev/null | grep -w "device" | awk '{print $1}')
if [ -n "$CONNECTED_DEVICES" ]; then
    echo ""
    echo "=========================================================="
    echo "  ADB-Gerät(e) erkannt: Direkte Installation (-r -d)     "
    echo "=========================================================="
    for dev in $CONNECTED_DEVICES; do
        DEV_MODEL=$(adb -s "$dev" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
        echo ">>> Zielgerät: $dev (${DEV_MODEL:-Smartphone})"
        
        # Megan App hat absolute Priorität 1
        if [ -f "$LOCAL_APK_DIR/MeganApp.apk" ]; then
            echo "    [Prio 1] Installiere Megan App..."
            adb -s "$dev" install -r -d "$LOCAL_APK_DIR/MeganApp.apk" || echo "    [-] Fehler bei Megan App Installation"
        fi

        # Weitere aktualisierte Apps installieren
        for updated in "${UPDATED_APKS[@]}"; do
            if [ "$updated" != "$LOCAL_APK_DIR/MeganApp.apk" ] && [ -f "$updated" ]; then
                app_base=$(basename "$updated")
                echo "    [+] Installiere $app_base..."
                adb -s "$dev" install -r -d "$updated" || echo "    [-] Fehler bei $app_base"
            fi
        done
    done
else
    echo ""
    echo "[Info] Kein ADB-Gerät per USB/WLAN verbunden (überspringe Geräte-Push)."
fi

echo ""
echo "=========================================================="
echo "Sync abgeschlossen! Aktuelle Dateien in ~/Desktop/APKs/:"
echo "=========================================================="
ls -l "$LOCAL_APK_DIR"/*.apk
echo ""
read -t 5 -p "Fenster schließt automatisch oder drücke Enter..." || true
echo ""
