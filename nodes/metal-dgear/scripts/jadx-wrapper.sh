#!/bin/bash
cd /mnt/storage-fast/rea-sandbox && docker compose run --rm -v "$(pwd)":/workspace rea-sandbox jadx "$@"
