#!/usr/bin/env bash

set -euo pipefail

# Linux OS identity discovery.
# Read-only operations; no system configuration changes.

if [[ ! -r /etc/os-release ]]; then
    echo "ERROR: Cannot read /etc/os-release" >&2
    exit 1
fi

# Load standard OS identification fields.
# This file is supplied by the operating system.
source /etc/os-release

hostname="$(hostname)"
architecture="$(uname -m)"

# Validate required fields before producing output.
if [[ -z "$hostname" || -z "${ID:-}" ||
      -z "${VERSION_ID:-}" || -z "$architecture" ]]; then
    echo "ERROR: Incomplete OS discovery information" >&2
    exit 1
fi

# Output structured JSON.
jq -n \
    --arg hostname "$hostname" \
    --arg os_id "$ID" \
    --arg os_version "$VERSION_ID" \
    --arg architecture "$architecture" \
    '{
        hostname: $hostname,
        os_id: $os_id,
        os_version: $os_version,
        architecture: $architecture
    }'
