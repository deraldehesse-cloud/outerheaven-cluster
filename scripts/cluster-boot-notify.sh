#!/usr/bin/env bash
# ==============================================================================
# cluster-boot-notify.sh - Wrapper calling host-specific boot notification
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/outerheaven-cluster-boot-notify.sh" "$@"
