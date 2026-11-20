#!/usr/bin/env bash
# Manual test helper for Milestone 4.39 (engine preflight checks).
#
# Usage: bash docs/testing/manual-4.39.sh <step> [vm] [value]
#   defs               laptop only: write the dssa-gitbad test definition
#   clock <vm>         read-only: server clock vs this computer, NTP status
#   skew <vm> <secs>   sudo on the VM: NTP off, clock set to this computer's
#                      time plus <secs> (negative = behind)
#   restore <vm>       sudo on the VM: clock set to this computer's time, NTP on
#   hidejq <vm>        sudo on the VM: rename jq to jq.dssa-hidden
#   restorejq <vm>     sudo on the VM: rename it back
#   statemode <vm> <mode>  sudo on the VM: chmod /var/lib/dssa (775 or 700)
#   state <vm>         read-only: /var/lib/dssa owner and mode, jq present
#
# vm: a target_id from config/inventory/lab-six.json

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"
DEFS="$HOME/dssa-manual-4.39/definitions"

field() {
    jq -er --arg t "${1:-}" --arg f "$2" '.servers[] | select(.target_id == $t) | .[$f] // empty' "$INVENTORY" ||
        { [[ "$2" == host ]] && { echo "Unknown VM: ${1:-}" >&2; exit 2; }; }
}

run() {
    # run VM [-t] COMMAND: plain ssh with the target's inventory key
    local vm="$1"; shift
    local host key
    host="$(field "$vm" host)"
    key="$(field "$vm" ssh_key || true)"
    key="${key/#\~/$HOME}"
    local -a opts=(-o StrictHostKeyChecking=yes)
    [[ -n "$key" ]] && opts+=(-i "$key" -o IdentitiesOnly=yes)
    [[ "${1:-}" == -t ]] && { opts+=(-t); shift; }
    ssh "${opts[@]}" "dssa-test@$host" "$1"
}

case "${1:-}" in
defs)
    mkdir -p "$DEFS"
    jq -n '
      def variant($m): {
        variant_id: "\($m)-test", support: "supported",
        platform: {package_manager: $m},
        detection: {packages_all: ["git"]}, conflicts: {},
        steps: [{type: "packages_install", role: "main", packages: ["git"]}],
        verification: [
          {type: "packages_installed", packages: ["git"]},
          {type: "executable", name: "git", version_args: ["--version"],
           version_pattern: "^git version 999\\."}],
        rollback: {created_paths: []}};
      {schema_version: "1.0", id: "dssa-gitbad", display_name: "DSSA 4.39 wrong git version pattern",
       category: "tool", depends_on: [], variants: [variant("apt"), variant("dnf")]}' \
        > "$DEFS/dssa-gitbad.json"
    ls "$DEFS"
    ;;
clock)
    now="$(date -u +%s)"
    out="$(run "${2:-}" 'date -u +%s; timedatectl show -p NTPSynchronized --value')"
    printf '%s: %+d s vs this computer, NTP synchronized: %s\n' "$2" \
        "$(( $(sed -n 1p <<< "$out") - now ))" "$(sed -n 2p <<< "$out")"
    ;;
skew)
    [[ "${3:-}" =~ ^-?[0-9]{1,5}$ ]] || { echo "skew <vm> <seconds>" >&2; exit 2; }
    run "${2:-}" -t "sudo timedatectl set-ntp false && sudo date -u -s @$(( $(date -u +%s) + $3 )) >/dev/null && echo \"clock now \$(date -u +%H:%M:%SZ), NTP off\""
    ;;
restore)
    run "${2:-}" -t "sudo date -u -s @$(date -u +%s) >/dev/null && sudo timedatectl set-ntp true && echo \"clock now \$(date -u +%H:%M:%SZ), NTP on\""
    ;;
hidejq)
    run "${2:-}" -t 'p="$(command -v jq)" && sudo mv -- "$p" "$p.dssa-hidden" && echo "hidden: $p"'
    ;;
restorejq)
    run "${2:-}" -t 'for p in /usr/bin/jq /usr/local/bin/jq; do if [ -e "$p.dssa-hidden" ]; then sudo mv -- "$p.dssa-hidden" "$p" && echo "restored: $p"; fi; done'
    ;;
statemode)
    [[ "${3:-}" == 775 || "${3:-}" == 700 ]] || { echo "statemode <vm> 775|700" >&2; exit 2; }
    run "${2:-}" -t "sudo chmod $3 /var/lib/dssa && sudo stat -c '/var/lib/dssa: uid %u mode %a' /var/lib/dssa"
    ;;
state)
    run "${2:-}" 'stat -c "/var/lib/dssa: uid %u mode %a" /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: missing"; command -v jq >/dev/null && echo "jq: present" || echo "jq: MISSING"'
    ;;
*)
    sed -n '2,14p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
