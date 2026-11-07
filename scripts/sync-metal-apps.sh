#!/bin/bash
echo "=== Synchronisiere aktuelle Apps von metal-dgear ==="
mkdir -p /home/bigboss/Desktop/APKs

# APKs von metal-dgear abholen
scp metal-dgear:/mnt/storage-fast/build-workspace/PaperworkScanner/app/build/outputs/apk/debug/app-debug.apk /home/bigboss/Desktop/APKs/paperwork-scanner.apk 2>/dev/null || echo "PaperworkScanner APK nicht gefunden."
scp metal-dgear:/mnt/storage-fast/build-workspace/OpenUp/app/build/outputs/apk/debug/app-debug.apk /home/bigboss/Desktop/APKs/megan-app.apk 2>/dev/null || echo "Megan APK nicht gefunden."
scp metal-dgear:/mnt/storage-fast/build-workspace/HermesAgentApp/app/build/outputs/apk/debug/app-debug.apk /home/bigboss/Desktop/APKs/hermes-app.apk 2>/dev/null || true
scp metal-dgear:/mnt/storage-fast/outerheaven-cluster-repo/apps/new-horizon-voice-app/app/build/outputs/apk/debug/app-debug.apk /home/bigboss/Desktop/APKs/VoiceRoadmap.apk 2>/dev/null || echo "VoiceRoadmap APK nicht gefunden."

# Symlinks auf Desktop & APKs-Ordner für nahtlose Kompatibilität
ln -sf /home/bigboss/Desktop/APKs/paperwork-scanner.apk /home/bigboss/Desktop/APKs/paperless-app.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/paperwork-scanner.apk /home/bigboss/Desktop/paperless-app.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/megan-app.apk /home/bigboss/Desktop/megan-app.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/hermes-app.apk /home/bigboss/Desktop/hermes-app.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/VoiceRoadmap.apk /home/bigboss/Desktop/APKs/new-horizon.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/VoiceRoadmap.apk /home/bigboss/Desktop/VoiceRoadmap.apk 2>/dev/null || true
ln -sf /home/bigboss/Desktop/APKs/VoiceRoadmap.apk /home/bigboss/Desktop/new-horizon.apk 2>/dev/null || true

echo "=== Sync abgeschlossen! ==="
echo "Die aktuellen Apps liegen nun in deinem Desktop-Ordner 'APKs'."
read -p "Drücke Enter zum Schließen..."
