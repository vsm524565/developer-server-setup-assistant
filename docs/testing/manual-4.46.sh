#!/usr/bin/env bash
# Helper for the combined lab test of 4.41-4.46 through the web UI.
#
# Usage: bash docs/testing/manual-4.46.sh <step> [vm...]
#   setup          laptop only: ~/dssa-manual-4.46/definitions with git plus
#                  the 4.37 test definitions (dssa-locktest, dssa-crashtest)
#   keys <vm>...   read-only: how many dssa-master keys each VM's dssa-test has
#   unkey <vm>...  remove the web UI's test key (dssa-master) from each VM
#                  (dssa-test's own authorized_keys; no sudo)

set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"
HOME_DIR="$HOME/dssa-manual-4.46"

host_of() {
    jq -er --arg t "$1" '.servers[] | select(.target_id == $t) | .host' "$INVENTORY" ||
        { echo "Unknown VM: $1" >&2; exit 2; }
}

case "${1:-}" in
setup)
    mkdir -p -m 700 "$HOME_DIR/definitions"
    chmod 700 "$HOME_DIR" "$HOME_DIR/definitions"
    cp "$REPO/definitions/components/git.json" "$HOME_DIR/definitions/"
    cp "$HOME/dssa-manual-4.37/definitions/dssa-locktest.json" "$HOME/dssa-manual-4.37/definitions/dssa-crashtest.json" \
        "$HOME_DIR/definitions/"
    chmod 600 "$HOME_DIR"/definitions/*.json
    ls "$HOME_DIR/definitions"
    ;;
keys|unkey)
    kind="$1"; shift
    for vm in "$@"; do
        if [[ "$kind" == keys ]]; then
            printf '%s: ' "$vm"
            ssh -o StrictHostKeyChecking=yes "dssa-test@$(host_of "$vm")" \
                'grep -c "dssa-master$" ~/.ssh/authorized_keys || true'
        else
            ssh -o StrictHostKeyChecking=yes "dssa-test@$(host_of "$vm")" \
                'sed -i "/dssa-master$/d" ~/.ssh/authorized_keys && echo "'"$vm"': test key removed"'
        fi
    done
    ;;
*)
    sed -n '2,10p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
