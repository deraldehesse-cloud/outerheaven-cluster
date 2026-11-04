#!/bin/bash
REMOTE_HOST="100.93.184.87"
PORT="8081"
URL="http://$REMOTE_HOST:$PORT"

command -v notify-send >/dev/null && notify-send "Hermes Agent" "Öffne Hermes Web-UI ($URL)..."

if command -v xdg-open >/dev/null; then
    xdg-open "$URL"
elif command -v chromium >/dev/null; then
    chromium "$URL"
elif command -v chromium-browser >/dev/null; then
    chromium-browser "$URL"
else
    sensible-browser "$URL"
fi
