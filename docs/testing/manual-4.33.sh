#!/usr/bin/env bash
# Manual test helper for Milestone 4.33 (journal and rollback).
#
# THIS CHANGES LAB VMs. Take VirtualBox snapshots of ubuntu24 and rocky10
# first. It only ever installs one harmless package, `tree`, through a
# test-only definition that is never part of definitions/components.
#
# Usage: bash docs/testing/manual-4.33.sh <step> <vm>
#   status  <vm>   read-only: is tree installed, does /var/lib/dssa exist
#   fail    <vm>   install tree with a verification that must fail -> rollback
#   install <vm>   install tree with a verification that passes
#   show    <vm>   summarize the latest transaction on the VM (sudo)
# vm: ubuntu24 | rocky10
#
# fail/install/show run under sudo on the VM: you will be asked for the
# dssa-test sudo password by the VM itself (it never passes through here).

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$HOME/dssa-manual-4.33"
cd "$REPO"

step="${1:-}"
vm="${2:-}"
case "$vm" in
    ubuntu24) HOST=192.168.56.110 ;;
    rocky10)  HOST=192.168.56.115 ;;
    *) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
SSH=(ssh -o StrictHostKeyChecking=yes "dssa-test@$HOST")

# Test-only definition: install `tree`; the verification command decides
# whether the component passes (install) or must be rolled back (fail).
test_definition() {
    local check="$1"
    jq -cn --arg check "$check" '
        def variant($m): {
            variant_id: "\($m)-test", support: "supported",
            platform: {package_manager: $m},
            detection: {packages_all: ["tree"]}, conflicts: {},
            steps: [{type: "package_metadata_refresh"},
                    {type: "packages_install", role: "main", packages: ["tree"]}],
            verification: [{type: "packages_installed", packages: ["tree"]},
                           {type: "executable", name: $check, version_args: ["--version"]}],
            rollback: {created_paths: []}};
        {schema_version: "1.0", id: "dssa-testpkg", display_name: "DSSA test package (tree)",
         category: "tool", depends_on: [], variants: [variant("apt"), variant("dnf")]}'
}

run_remote_root() {
    # Send a script bundle and run it under sudo with a terminal, so the
    # VM's own sudo asks for the password. Results come back as a marker.
    local script="$1" payload
    payload="$(printf '%s\n' "$script" | base64 -w0)"
    mkdir -p "$OUT"
    "${SSH[@]}" -t "sudo -- bash -c \"\$(printf %s '$payload' | base64 -d)\"" | tee "$OUT/$vm-last.log"
}

bundle() {
    printf '%s\n' 'set -uo pipefail'
    for f in modules/software/package_manager.sh engine/definitions.sh engine/journal.sh; do
        cat "backend/linux/$f"; printf '\n'
    done
}

apply_test() {
    local check="$1" plan_id definition
    plan_id="$(cat /proc/sys/kernel/random/uuid)"
    definition="$(test_definition "$check")"
    run_remote_root "$(bundle)
plan_id=$(printf %q "$plan_id")
definition=$(printf %q "$definition")
manager=\"\$(select_package_manager)\" || exit 89
echo \"Plan \$plan_id on \$(hostname) using \$manager\"
begin_transaction \"\$plan_id\" \"\$manager\" '[]' || exit 90
result=\"\$(apply_component \"\$plan_id\" \"\$manager\" \"\$definition\" \"\$manager-test\")\"
rc=\$?
end_transaction \"\$plan_id\" \"[\${result:-null}]\" || exit 91
printf 'DSSA_RESULT: %s\n' \"\$result\"
exit \$rc"
}

summarize() {
    local line
    line="$(tr -d '\r' < "$OUT/$vm-last.log" | grep '^DSSA_RESULT: ' | tail -1 || true)"
    if [[ -z "$line" ]]; then
        echo "No DSSA_RESULT line; see $OUT/$vm-last.log"
        return 1
    fi
    echo
    echo "=== Result ==="
    jq '{component, outcome, failure, verification,
         rollback_actions: [.rollback.actions[]? | "\(.action) \(.target): \(.result)"],
         rollback_issues: .rollback.issues, upgraded_not_reverted}' <<< "${line#DSSA_RESULT: }"
}

case "$step" in
status)
    "${SSH[@]}" 'set -u
        if command -v dpkg-query >/dev/null 2>&1; then
            dpkg-query -W -f="tree: \${Status} \${Version}\n" tree 2>/dev/null || echo "tree: not installed"
        else
            rpm -q tree 2>/dev/null || echo "tree: not installed"
        fi
        ls -ld /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: absent"'
    ;;
fail)
    apply_test dssa-missing-check || true
    summarize
    ;;
install)
    apply_test tree || true
    summarize
    ;;
show)
    run_remote_root '
        set -u
        root=/var/lib/dssa/transactions
        latest="$(ls -1t "$root" 2>/dev/null | head -1)"
        [[ -n "$latest" ]] || { echo "No transactions on this VM."; exit 0; }
        tx="$root/$latest"
        echo "Transaction : $latest"
        ls -la "$tx"
        echo
        echo "Snapshot    : $(jq ".packages | length" "$tx/snapshot.json") packages, $(jq ".units | length" "$tx/snapshot.json") units, taken $(jq -r .taken_at "$tx/snapshot.json")"
        echo
        echo "Journal:"
        jq -r "\"  \(.seq)  \(.phase)\(if .step != null then \"  step \(.step) \(.type)\" else \"\" end)\(if .outcome then \"  -> \(.outcome)\" else \"\" end)\"" "$tx/journal.jsonl"
        echo
        echo "Result      : $(jq -c "[.results[] | {component, outcome}]" "$tx/result.json")"
    '
    ;;
*)
    sed -n '2,17p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
