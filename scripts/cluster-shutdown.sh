#!/usr/bin/env bash
# ==============================================================================
# Outer Heaven Cluster Shutdown Wrapper (delegates to cluster-power.sh)
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/cluster-power.sh" "$@"
