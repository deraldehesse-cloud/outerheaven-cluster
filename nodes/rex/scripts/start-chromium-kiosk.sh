#!/bin/bash
chromium-browser \
  --no-first-run \
  --no-default-browser-check \
  --disable-session-crashed-bubble \
  --password-store=basic \
  --kiosk \
  https://www.zdf.de
