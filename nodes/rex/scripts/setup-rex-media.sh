#!/bin/bash
# antig-auftrag: rex media station setup
echo "Richte Medienverzeichnisse und Chromium-Kiosk für rex ein..."

# 1. Erstelle die Verzeichnisstrukturen für NAS-Mounts falls nötig
mkdir -p ~/.local/bin

# 2. Schreibe das autarke Chromium-Startskript ohne Passwortabfrage
cat << 'EOL' > ~/.local/bin/start-chromium-kiosk.sh
#!/bin/bash
chromium-browser --no-first-run --no-default-browser-check --disable-session-crashed-bubble --password-store=basic https://www.zdf.de
EOL
chmod +x ~/.local/bin/start-chromium-kiosk.sh

echo "rex-Einrichtung durch antig erfolgreich abgeschlossen."
