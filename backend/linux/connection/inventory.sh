#!/usr/bin/env bash

set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
INVENTORY="${DSSA_INVENTORY_FILE:-$BASE_DIR/config/inventory/servers.json}"

if [[ $# -ne 1 ]]; then
    echo "Usage: inventory.sh TARGET_ID" >&2
    exit 2
fi

target_id="$1"

if [[ ! "$target_id" =~ ^[a-zA-Z0-9_-]{1,64}$ ]]; then
    echo "ERROR: Invalid target identifier" >&2
    exit 2
fi

if [[ ! -f "$INVENTORY" || -L "$INVENTORY" ]]; then
    echo "ERROR: Inventory file missing or unsafe" >&2
    exit 3
fi

# Reject group/other-writable inventory files.
permissions="$(stat -c '%a' "$INVENTORY")"

if (( (8#$permissions & 8#022) != 0 )); then
    echo "ERROR: Inventory file is writable by group or others" >&2
    exit 3
fi

jq -ce --arg target "$target_id" '
    if (
        type == "object"
        and .schema_version == "1.0"
        and (.servers | type == "array")
        and ([.servers[] | select(.target_id == $target)] | length) == 1
    )
    then
        .servers[]
        | select(.target_id == $target)
        | select(
            .enabled == true
            and .platform == "linux"
            and (.host | type == "string" and length > 0)
            and (.ssh_user | type == "string" and length > 0)
            and (.ssh_port | type == "number" and . >= 1 and . <= 65535)
        )
    else
        error("Invalid inventory or target")
    end
' "$INVENTORY"
