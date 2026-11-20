#!/usr/bin/env bash
# Manual test helper for Milestone 4.38 (SSH key and approval fixes).
#
# Usage: bash docs/testing/manual-4.38.sh <step> [vm]
#   env           read-only: the DSSA variables set in this terminal
#   keys          read-only: ssh_key of every target in lab-six.json, and
#                 whether that file exists
#   hello <vm>    read-only: is the hello package installed
#   purge <vm>    sudo on the VM: remove the hello package again
#
# vm: ubuntu22 | ubuntu24 | ubuntu26 | debian13 (hello is an apt package)

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"

host_of() {
    jq -er --arg t "${1:-}" '.servers[] | select(.target_id == $t) | .host' "$INVENTORY" ||
        { echo "Unknown VM: ${1:-}" >&2; exit 2; }
}

case "${1:-}" in
env)
    for v in DSSA_SSH_KEY XDG_STATE_HOME DSSA_LOG_DIR DSSA_INVENTORY_FILE DSSA_DEFINITIONS_DIR; do
        printf '%s=%s\n' "$v" "${!v:-unset}"
    done
    ;;
keys)
    stat -c 'inventory mode: %a' "$INVENTORY"
    jq -r '.servers[] | "\(.target_id)\t\(.ssh_key // "(none)")"' "$INVENTORY" |
        while IFS=$'\t' read -r t k; do
            f="${k/#\~/$HOME}"
            if [[ "$k" == "(none)" ]]; then s="-"; elif [[ -f "$f" ]]; then s="exists"; else s="MISSING"; fi
            printf '%-9s %-34s %s\n' "$t" "$k" "$s"
        done
    ;;
hello)
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$(host_of "${2:-}")" \
        'v="$(dpkg-query -W -f="\${Status} \${Version}" hello 2>/dev/null)" || v="not installed"; echo "hello: $v"'
    ;;
purge)
    ssh -t -o StrictHostKeyChecking=yes "dssa-test@$(host_of "${2:-}")" \
        'sudo apt-get purge -y hello >/dev/null && echo "hello removed"'
    ;;
*)
    sed -n '2,11p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
