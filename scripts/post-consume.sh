#!/usr/bin/env bash
# $DOCUMENT_ID wird nativ von Paperless-ngx exportiert
curl -s -f -X POST "http://100.93.184.87:5000/trigger-hermes?doc_id=${DOCUMENT_ID}" || \
python3 /usr/src/paperless/scripts/hermes_paperless_advisor.py "${DOCUMENT_ID}"
