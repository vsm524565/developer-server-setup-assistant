#!/usr/bin/env bash
# Manual test helper for Milestone 4.34 (plan -> approve -> apply).
#
# Read-only: shows a lab VM's state from outside DSSA, as dssa-test and
# without sudo. All changes in this test go through `python main.py`.
#
# Usage: bash docs/testing/manual-4.34.sh status <vm>
#   vm: a target_id from config/inventory/lab-six.json (e.g. rocky10, ubuntu24)

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"

[[ "${1:-}" == status && -n "${2:-}" ]] || { sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 2; }
vm="$2"
host="$(jq -er --arg t "$vm" '.servers[] | select(.target_id == $t) | .host' "$INVENTORY")" ||
    { echo "Unknown VM: $vm" >&2; exit 2; }
lab="$(jq -r --arg t "$vm" '.servers[] | select(.target_id == $t) | .lab == true' "$INVENTORY")"

echo "VM          : $vm ($host), lab flag in inventory: $lab"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$host" 'set -u
    if command -v dpkg-query >/dev/null 2>&1; then
        for p in git docker-ce; do
            dpkg-query -W -f="$p: \${Status} \${Version}\n" "$p" 2>/dev/null || echo "$p: not installed"
        done
    else
        for p in git docker-ce; do rpm -q "$p" >/dev/null 2>&1 && echo "$p: $(rpm -q "$p")" || echo "$p: not installed"; done
    fi
    printf "docker.service: %s\n" "$(systemctl is-active docker.service 2>/dev/null || true)"
    ls -ld /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: absent"
    ls -d .cache/dssa/* 2>/dev/null | sed "s/^/staged (should be none): /" || true'
