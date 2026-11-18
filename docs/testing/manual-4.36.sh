#!/usr/bin/env bash
# Manual test helper for Milestone 4.36 (Docker everywhere + Compose).
#
# Usage: bash docs/testing/manual-4.36.sh <step> [vm]
#   flags        read-only: lab flags of all six VMs in lab-six.json
#   state <vm>   read-only (no sudo): Docker/Compose packages, docker.service,
#                Docker repo files, Docker rpm signing key (EL), /var/lib/dssa
#   defs         write the test-only dnf failure definition (alma10 step)
#
# vm: ubuntu22 | ubuntu24 | ubuntu26 | debian13 | alma10 | rocky10

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INVENTORY="$REPO/config/inventory/lab-six.json"
OUT="$HOME/dssa-manual-4.36"

case "${1:-}" in
flags)
    jq -r '.servers[] | "\(.target_id)\t\(.host)\tlab=\(.lab == true)"' "$INVENTORY"
    ;;
state)
    host="$(jq -er --arg t "${2:-}" '.servers[] | select(.target_id == $t) | .host' "$INVENTORY")" ||
        { echo "Unknown VM: ${2:-}" >&2; exit 2; }
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$host" 'set -u
        for p in docker-ce docker-compose-plugin; do
            if command -v dpkg-query >/dev/null 2>&1; then
                v="$(dpkg-query -W -f="\${Status} \${Version}" "$p" 2>/dev/null)" || v="not installed"
            else
                v="$(rpm -q "$p" 2>/dev/null)" || v="not installed"
            fi
            echo "$p: $v"
        done
        printf "docker.service: %s\n" "$(systemctl is-active docker.service 2>/dev/null || true)"
        for f in /etc/apt/sources.list.d/docker.sources /etc/yum.repos.d/docker-ce.repo \
                 /etc/yum.repos.d/dssa-dnftest.repo /etc/pki/rpm-gpg/docker-ce.asc /etc/pki/rpm-gpg/dssa-dnftest.asc; do
            [ -e "$f" ] && echo "present: $f"
        done
        if command -v rpm >/dev/null 2>&1 && ! command -v dpkg-query >/dev/null 2>&1; then
            k="$(rpm -q gpg-pubkey 2>/dev/null | grep -i 621e9f35 || true)"
            if [ -n "$k" ]; then echo "$k" | sed "s/^/rpm key (Docker): /"; else echo "rpm key (Docker): none"; fi
        fi
        ls -ld /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: absent"
        ls -d .cache/dssa/* 2>/dev/null | sed "s/^/staged (should be none): /" || true'
    ;;
defs)
    mkdir -p "$OUT/definitions"
    chmod 755 "$OUT" "$OUT/definitions"
    # Adds Docker's EL repo, installs only the Compose plugin (dnf imports
    # the signing key), then fails verification: rollback must remove the
    # package, repo file, key file and the imported rpm key.
    jq -n '{schema_version: "1.0", id: "dssa-dnftest", display_name: "DSSA 4.36 dnf repository test",
            category: "tool", depends_on: [],
            variants: [{variant_id: "dnf-test", support: "supported",
                platform: {package_manager: "dnf"},
                detection: {packages_all: ["dssa-no-such-package"]}, conflicts: {},
                steps: [
                    {type: "dnf_vendor_repository", repository_id: "dssa-dnftest",
                     baseurl: "https://download.docker.com/linux/centos/10/x86_64/stable",
                     key_url: "https://download.docker.com/linux/centos/gpg",
                     key_fingerprint: "060A61C51B558A7F742B77AAC52FEB6B621E9F35",
                     key_path: "/etc/pki/rpm-gpg/dssa-dnftest.asc",
                     repo_path: "/etc/yum.repos.d/dssa-dnftest.repo"},
                    {type: "package_metadata_refresh"},
                    {type: "packages_install", role: "main", packages: ["docker-compose-plugin"]}],
                verification: [{type: "executable", name: "dssa-missing-check", version_args: ["--version"]}],
                rollback: {created_paths: []}}]}' > "$OUT/definitions/dssa-dnftest.json"
    chmod 644 "$OUT/definitions/dssa-dnftest.json"
    echo "Test definitions: $OUT/definitions"
    ls -l "$OUT/definitions"
    ;;
*)
    sed -n '2,10p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
