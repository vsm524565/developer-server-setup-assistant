#!/usr/bin/env bash
# Manual test helper for Milestone 4.37 (engine hardening).
#
# Usage: bash docs/testing/manual-4.37.sh <step> [vm] [seconds]
#   defs              write the three test-only definitions
#   aptcheck <vm>     sudo on the VM: the exact refresh DSSA now runs
#   units <vm>        sudo on the VM: create the stable and crash-loop test units
#   lock <vm> [secs]  sudo on the VM: hold dpkg's frontend lock (default 120 s)
#   state <vm>        read-only: test units, hello package, /var/lib/dssa
#   cleanup <vm>      sudo on the VM: stop and remove the test units
#
# vm: ubuntu22 | ubuntu24 | ubuntu26 | debian13 | alma10 | rocky10

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"
OUT="$HOME/dssa-manual-4.37"

host_of() {
    jq -er --arg t "${1:-}" '.servers[] | select(.target_id == $t) | .host' "$INVENTORY" ||
        { echo "Unknown VM: ${1:-}" >&2; exit 2; }
}

remote() {  # remote <vm> <script>; -t so sudo can ask for the password
    ssh -t -o StrictHostKeyChecking=yes "dssa-test@$(host_of "$1")" "$2"
}

case "${1:-}" in
defs)
    mkdir -p "$OUT/definitions"
    chmod 755 "$OUT" "$OUT/definitions"
    # Starts a unit that stays up: the 15 s watch passes.
    jq -n '{schema_version: "1.0", id: "dssa-stabletest", display_name: "DSSA 4.37 stable service test",
            category: "tool", depends_on: [],
            variants: [{variant_id: "apt-test", support: "supported", platform: {package_manager: "apt"},
                detection: {packages_all: ["dssa-no-such-package"]}, conflicts: {},
                steps: [{type: "service_start", unit: "dssa-stable.service"}],
                verification: [{type: "service_active", unit: "dssa-stable.service"}],
                rollback: {created_paths: []}}]}' > "$OUT/definitions/dssa-stabletest.json"
    # Starts a unit that exits after 4 s and is restarted (Restart=always):
    # the watch must fail the step and rollback must stop the unit again.
    jq -n '{schema_version: "1.0", id: "dssa-crashtest", display_name: "DSSA 4.37 crash-loop test",
            category: "tool", depends_on: [],
            variants: [{variant_id: "apt-test", support: "supported", platform: {package_manager: "apt"},
                detection: {packages_all: ["dssa-no-such-package"]}, conflicts: {},
                steps: [{type: "service_start", unit: "dssa-crash.service"}],
                verification: [{type: "service_active", unit: "dssa-crash.service"}],
                rollback: {created_paths: []}}]}' > "$OUT/definitions/dssa-crashtest.json"
    # Installs the small "hello" package while dpkg is locked: apt must
    # give up after 60 s instead of waiting forever.
    jq -n '{schema_version: "1.0", id: "dssa-locktest", display_name: "DSSA 4.37 apt lock test",
            category: "tool", depends_on: [],
            variants: [{variant_id: "apt-test", support: "supported", platform: {package_manager: "apt"},
                detection: {packages_all: ["hello"]}, conflicts: {},
                steps: [{type: "packages_install", role: "main", packages: ["hello"]}],
                verification: [{type: "packages_installed", packages: ["hello"]}],
                rollback: {created_paths: []}}]}' > "$OUT/definitions/dssa-locktest.json"
    chmod 644 "$OUT"/definitions/*.json
    echo "Test definitions: $OUT/definitions"
    ls -l "$OUT/definitions"
    ;;
aptcheck)
    remote "${2:-}" 'apt-get --version | head -1
        sudo apt-get -o DPkg::Lock::Timeout=60 update --error-on=any >/dev/null; echo "refresh exit: $?"'
    ;;
units)
    remote "${2:-}" 'sudo tee /etc/systemd/system/dssa-stable.service >/dev/null <<EOF
[Unit]
Description=DSSA 4.37 test unit that stays up
[Service]
ExecStart=/bin/sleep infinity
EOF
sudo tee /etc/systemd/system/dssa-crash.service >/dev/null <<EOF
[Unit]
Description=DSSA 4.37 test unit that crashes every 4 s
StartLimitIntervalSec=0
[Service]
ExecStart=/bin/sh -c "sleep 4; exit 1"
Restart=always
RestartSec=1
EOF
sudo systemctl daemon-reload
for u in dssa-stable.service dssa-crash.service; do printf "%s: %s\n" "$u" "$(systemctl is-active "$u")"; done'
    ;;
lock)
    secs="${3:-120}"
    [[ "$secs" =~ ^[0-9]{1,3}$ ]] || { echo "seconds: 1-999" >&2; exit 2; }
    # apt uses fcntl locks (not flock); lockf takes the same kind.
    remote "${2:-}" "sudo python3 -c 'import fcntl, time
f = open(\"/var/lib/dpkg/lock-frontend\", \"w\")
fcntl.lockf(f, fcntl.LOCK_EX)
print(\"holding /var/lib/dpkg/lock-frontend for $secs s\", flush=True)
time.sleep($secs)
print(\"released\")'"
    ;;
state)
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$(host_of "${2:-}")" 'set -u
        for u in dssa-stable.service dssa-crash.service; do
            printf "%s: %s (restarts %s)\n" "$u" "$(systemctl is-active "$u" 2>/dev/null || true)" \
                "$(systemctl show --property=NRestarts --value "$u" 2>/dev/null)"
        done
        v="$(dpkg-query -W -f="\${Status} \${Version}" hello 2>/dev/null)" || v="not installed"
        echo "hello: $v"
        ls -ld /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: absent"'
    ;;
cleanup)
    remote "${2:-}" 'sudo systemctl stop dssa-stable.service dssa-crash.service 2>/dev/null
        sudo rm -f /etc/systemd/system/dssa-stable.service /etc/systemd/system/dssa-crash.service
        sudo systemctl daemon-reload; sudo systemctl reset-failed 2>/dev/null
        ls /etc/systemd/system/dssa-* 2>/dev/null || echo "test units removed"'
    ;;
*)
    sed -n '2,12p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
