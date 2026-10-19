#!/usr/bin/env bash
set -euo pipefail

# DSSA approved web server discovery entry point.
# The reusable collector is loaded before this runner.

# Verify the remote JSON dependency before collecting data.
if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: Missing remote prerequisite: jq" >&2
    exit 127
fi

discover_web_servers
