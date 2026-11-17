#!/usr/bin/env bash
# Manual test helper for Milestone 4.35 (hardening).
#
# Usage: bash docs/testing/manual-4.35.sh <step> [vm]
#   defs         write three test-only definitions (never in definitions/)
#   state <vm>   read-only (no sudo): tree installed? Docker repo files and
#                apt lists present? anything staged?
#   drop         from a SECOND terminal, during an apply on ubuntu24: cut the
#                laptop's SSH connection (kills only the local ssh client,
#                like a network drop; nothing on the VM is touched)
#   crash        from a SECOND terminal, started BEFORE a dssa-crashtest apply
#                on ubuntu24: waits until the run's install step is done, then
#                kill -9 the whole detached DSSA run (sudo on the VM)
#
# vm: ubuntu24 | ubuntu22

set -euo pipefail

OUT="$HOME/dssa-manual-4.35"

host_of() {
    case "$1" in
        ubuntu24) echo 192.168.56.110 ;;
        ubuntu22) echo 192.168.56.111 ;;
        *) echo "Unknown VM: $1" >&2; exit 2 ;;
    esac
}

# id, steps, verification executable, [detection package] -> one definition
write_definition() {
    local id="$1" steps="$2" check="$3" detect="${4:-tree}"
    jq -n --arg id "$id" --argjson steps "$steps" --arg check "$check" --arg detect "$detect" '
        {schema_version: "1.0", id: $id, display_name: "DSSA 4.35 test (\($id))",
         category: "tool", depends_on: [],
         variants: [{variant_id: "apt-test", support: "supported",
             platform: {package_manager: "apt"},
             detection: {packages_all: [$detect]}, conflicts: {},
             steps: $steps,
             verification: [{type: "executable", name: $check, version_args: ["--version"]}],
             rollback: {created_paths: []}}]}' > "$OUT/definitions/$id.json"
}

case "${1:-}" in
defs)
    mkdir -p "$OUT/definitions"
    chmod 755 "$OUT" "$OUT/definitions"
    tree_steps='[{"type":"package_metadata_refresh"},
                 {"type":"packages_install","role":"main","packages":["tree"]},
                 {"type":"package_metadata_refresh"}]'
    # Drop test: finishes on the server, then rolls itself back.
    write_definition dssa-droptest "$tree_steps" dssa-missing-check
    # Crash test: would pass; the second refresh is the window to kill it.
    write_definition dssa-crashtest "$tree_steps" tree
    # Repository test (ubuntu22 jammy): add Docker's repo, then fail.
    write_definition dssa-repotest '[
        {"type":"apt_vendor_repository","repository_id":"dssa-repotest",
         "key_url":"https://download.docker.com/linux/ubuntu/gpg",
         "key_fingerprint":"9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
         "key_path":"/etc/apt/keyrings/dssa-repotest.asc",
         "source_path":"/etc/apt/sources.list.d/dssa-repotest.sources",
         "uris":"https://download.docker.com/linux/ubuntu","suites":["jammy"],
         "components":["stable"],"architectures":["amd64"]},
        {"type":"package_metadata_refresh"}]' dssa-missing-check dssa-no-such-package
    chmod 644 "$OUT"/definitions/*.json
    echo "Test definitions: $OUT/definitions"
    ls -l "$OUT/definitions"
    ;;
state)
    host="$(host_of "${2:-}")"
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$host" 'set -u
        dpkg-query -W -f="tree: \${Status}\n" tree 2>/dev/null || echo "tree: not installed"
        for f in /etc/apt/sources.list.d/dssa-repotest.sources /etc/apt/keyrings/dssa-repotest.asc; do
            [ -e "$f" ] && echo "present: $f" || echo "absent : $f"
        done
        n="$(ls /var/lib/apt/lists/ 2>/dev/null | grep -c "download.docker.com" || true)"
        echo "apt list files for download.docker.com: $n"
        ls -ld /var/lib/dssa 2>/dev/null || echo "/var/lib/dssa: absent"
        ls -d .cache/dssa/* 2>/dev/null | sed "s/^/staged (should be none): /" || true'
    ;;
drop)
    # Only the apply's terminal session matches: "ssh ... -t -- HOST sudo -- bash -c".
    # Anchored at ^ssh, so this script's own command line never matches.
    pattern="^ssh .* -t -- $(host_of ubuntu24 | sed 's/\./\\./g') sudo -- bash -c"
    pid="$(pgrep -f "$pattern" | head -1 || true)"
    [[ -n "$pid" ]] || { echo "No DSSA apply connection to ubuntu24 is open on this laptop."; exit 1; }
    echo "Cutting the laptop's SSH connection (local ssh pid $pid); the VM is not touched."
    kill -9 "$pid"
    ;;
crash)
    # Runs as root on ubuntu24. Start it BEFORE the apply: it waits until a
    # DSSA run's journal shows its package install step done, then kills
    # that run's whole session (pkill -s), like a crash between steps.
    # The anchored pattern never matches this command itself.
    script='echo "Waiting up to 3 minutes for a DSSA run to finish its install step..."
i=0
while [ "$i" -lt 360 ]; do
    pid="$(pgrep -f "^bash /var/lib/dssa/runs/[^ ]+/bundle\.sh run$" | head -1)"
    if [ -n "$pid" ]; then
        id="$(ps -o args= -p "$pid" | sed -E "s#.*/runs/([^/]+)/bundle\.sh.*#\1#")"
        j="/var/lib/dssa/transactions/$id/journal.jsonl"
        if [ -f "$j" ] && jq -s -e "any(.[]; .type == \"packages_install\" and .phase == \"done\")" "$j" >/dev/null 2>&1; then
            echo "Install step done in run $id; killing its session $pid (simulated crash)"
            pkill -9 -s "$pid"
            exit 0
        fi
    fi
    sleep 0.5
    i=$((i + 1))
done
echo "No DSSA run reached its install step in time; nothing was killed."
exit 1'
    payload="$(printf '%s\n' "$script" | base64 -w0)"
    ssh -t -o StrictHostKeyChecking=yes "dssa-test@$(host_of ubuntu24)" \
        "sudo -- bash -c \"\$(printf %s '$payload' | base64 -d)\""
    ;;
*)
    sed -n '2,13p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
