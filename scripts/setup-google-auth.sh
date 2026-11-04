#!/usr/bin/env bash
# ==============================================================================
# Outer Heaven - Google Workspace OAuth 2.0 Setup (100% Safe)
# ==============================================================================
# Dieses Skript führt die lokale, sichere Google-Autorisierung für den
# Hermes-Agenten auf der Station 'fox' durch.
# Kann direkt auf 'fox' ODER remote von 'Big Boss' ausgeführt werden.
# Keine Daten an Dritte. Tokens verbleiben lokal mit chmod 600.
# ==============================================================================

set -euo pipefail

FOX_HOST="fox"
REMOTE_PYTHON="/home/fox/.hermes/.venv/bin/python"
REMOTE_SETUP="/home/fox/.hermes/skills/productivity/google-workspace/scripts/setup.py"
REMOTE_HERMES_DIR="/home/fox/.hermes"

echo "=================================================================="
echo "🦊 Outer Heaven: Google Workspace Integration (100% Safe)"
echo "=================================================================="

usage() {
    echo "Verwendung: $0 [BEFEHL]"
    echo ""
    echo "Befehle:"
    echo "  status            Prüft den aktuellen Autorisierungsstatus"
    echo "  guide             Zeigt eine 2-Minuten-Anleitung für die Google Cloud Console"
    echo "  import <DATEI>    Importiert credentials.json nach Fox"
    echo "  auth-url          Generiert die private Google-Autorisierungs-URL"
    echo "  auth-code <CODE>  Tauscht den Auth-Code gegen ein lokales Token ein"
    echo "  revoke            NOT-AUS: Widerruft und löscht alle lokalen Google-Tokens"
    echo ""
    exit 1
}

is_local_fox() {
    [[ -d "/home/fox/.hermes" && -x "$REMOTE_PYTHON" ]]
}

run_on_fox() {
    if is_local_fox; then
        bash -c "$1"
    else
        ssh "$FOX_HOST" "$1"
    fi
}

cmd="${1:-status}"

case "$cmd" in
    status|check)
        echo "🔍 Prüfe Status auf Station 'fox'..."
        if run_on_fox "$REMOTE_PYTHON $REMOTE_SETUP --check"; then
            echo ""
            echo "✅ Google Workspace ist VOLLSTÄNDIG AUTORISIERT und einsatzbereit!"
            run_on_fox "ls -la $REMOTE_HERMES_DIR/google_token.json"
        else
            echo ""
            echo "⚠️ Noch nicht autorisiert. Führe '$0 guide' aus, um zu starten."
        fi
        ;;

    guide|help)
        echo ""
        echo "📋 SCHRITT-FÜR-SCHRITT ANLEITUNG (Google Cloud Console):"
        echo "---------------------------------------------------------"
        echo "1. Öffne https://console.cloud.google.com/ in deinem Browser."
        echo "2. Erstelle ein neues Projekt (z.B. 'OuterHeaven-Butler')."
        echo "3. Aktiviere unter 'APIs & Dienste' -> 'Bibliothek' folgende APIs:"
        echo "   - Google Calendar API"
        echo "   - Gmail API"
        echo "   - Google Drive API"
        echo "   - Google Docs API & Google Sheets API (optional)"
        echo "4. Konfiguriere den 'OAuth-Zustimmungsbildschirm':"
        echo "   - Wähle 'Extern' (oder 'Intern', falls Google Workspace-Konto)."
        echo "   - App-Name: Outer Heaven Butler"
        echo "   - E-Mail-Adresse: Deine eigene E-Mail angeben."
        echo "   - Testnutzer: Füge deine eigene Google-E-Mail als Testnutzer hinzu."
        echo "5. Erstelle Anmeldedaten unter 'APIs & Dienste' -> 'Anmeldedaten':"
        echo "   - Klicke auf 'Anmeldedaten erstellen' -> 'OAuth-Client-ID'."
        echo "   - Anwendungstyp: 'Desktop-App' (Desktop Application)."
        echo "   - Name: Hermes Butler Client"
        echo "   - Klicke auf 'Erstellen' und lade die JSON-Datei herunter (z.B. 'client_secret_xxx.json')."
        echo "6. Importiere die Datei mit:"
        echo "   $0 import /pfad/zu/deiner/client_secret_xxx.json"
        echo "---------------------------------------------------------"
        ;;

    import)
        file_path="${2:-}"
        if [[ -z "$file_path" || ! -f "$file_path" ]]; then
            echo "❌ Fehler: Bitte gültigen Pfad zur client_secret_xxx.json angeben."
            echo "Beispiel: $0 import ~/Downloads/client_secret_xxx.json"
            exit 1
        fi
        echo "📤 Übertrage Client-Secret sicher nach Fox..."
        if is_local_fox; then
            cp "$file_path" "$REMOTE_HERMES_DIR/google_client_secret.json"
        else
            scp "$file_path" "$FOX_HOST:$REMOTE_HERMES_DIR/google_client_secret.json"
        fi
        run_on_fox "chmod 600 $REMOTE_HERMES_DIR/google_client_secret.json"
        echo "✅ Client-Secret erfolgreich hinterlegt (Rechte: 600)."
        echo ""
        echo "👉 Nächster Schritt: Führe '$0 auth-url' aus!"
        ;;

    auth-url)
        echo "🔗 Erzeuge Autorisierungs-URL..."
        run_on_fox "$REMOTE_PYTHON $REMOTE_SETUP --auth-url"
        echo ""
        echo "👉 Öffne den obigen Link in deinem Browser, logge dich bei Google ein und bestätige."
        echo "👉 Nach der Bestätigung leitet der Browser auf eine localhost-Adresse weiter."
        echo "👉 Kopiere die Weiterleitungs-URL (oder den 'code='-Teil) und führe aus:"
        echo "   $0 auth-code \"DEIN_CODE_ODER_URL\""
        ;;

    auth-code)
        auth_code="${2:-}"
        if [[ -z "$auth_code" ]]; then
            echo "❌ Fehler: Bitte den Code oder die Weiterleitungs-URL übergeben."
            echo "Beispiel: $0 auth-code \"4/0A...\""
            exit 1
        fi
        echo "🔑 Tausche Auth-Code gegen Token ein..."
        run_on_fox "$REMOTE_PYTHON $REMOTE_SETUP --auth-code '$auth_code'"
        run_on_fox "chmod 600 $REMOTE_HERMES_DIR/google_token.json"
        echo ""
        echo "🎉 Autorisierung erfolgreich! Token ist sicher mit chmod 600 geschützt."
        echo "Führe '$0 status' aus, um die Verbindung zu bestätigen."
        ;;

    revoke)
        echo "⚠️ NOT-AUS: Widerrufe alle Google-Zugriffsrechte auf Station 'fox'..."
        run_on_fox "$REMOTE_PYTHON $REMOTE_SETUP --revoke || true"
        run_on_fox "rm -f $REMOTE_HERMES_DIR/google_token.json $REMOTE_HERMES_DIR/google_client_secret.json $REMOTE_HERMES_DIR/google_oauth_pending.json"
        echo "✅ Alle Google-Tokens und Secrets auf Station 'fox' wurden restlos gelöscht."
        ;;

    *)
        usage
        ;;
esac
