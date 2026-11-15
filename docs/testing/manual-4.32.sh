#!/usr/bin/env bash
# Manual test helper for Milestone 4.32 (approval records).
#
# Each subcommand is self-contained, so every step works from a fresh
# shell (your terminal or the app's command box). Nothing here changes a
# server: "plan" is a read-only plan run as dssa-test without sudo.
# Approval files and audit events go to $HOME/dssa-manual-4.32 only.
#
# Usage: bash docs/testing/manual-4.32.sh <step> [args]
#   setup | plan [name] | approve [name] [CONFIRMATION] | check [mode] [name]
#   tamper | restore | consume [name] | audit | cleanup
# check modes: normal (default), expired, wrong-key

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$HOME/dssa-manual-4.32"
HOST=192.168.56.115
TARGET=rocky10
PY="$REPO/.venv/bin/python"
export DSSA_LOG_DIR="$T/audit"
cd "$REPO"

need_plan_id() {
    local name="${1:-plan}"
    [[ -f "$T/$name.json" ]] || { echo "No $name.json yet: run the 'plan' step first." >&2; exit 1; }
    jq -r .plan_id "$T/$name.json"
}

step="${1:-}"
shift || true

case "$step" in
setup)
    mkdir -p "$T"
    echo "Test folder: $T"
    echo "Audit log  : $DSSA_LOG_DIR"
    ls -la "$T"
    ;;

plan)
    name="${1:-plan}"
    mkdir -p "$T"
    key="$("$PY" -c "from bridge.components.approval import known_host_fingerprints as k; print(sorted(k('$HOST'))[0])")"
    req="$(bash -c 'set -euo pipefail; source backend/linux/engine/definitions.sh; source backend/linux/engine/plan.sh; build_plan_request "$@"' _ "$TARGET" "$key" definitions/components git)"
    {
        printf '%s\n' 'set -euo pipefail'
        for f in modules/software/package_manager.sh discovery/panel.sh discovery/ports.sh engine/definitions.sh engine/plan.sh; do
            cat "backend/linux/$f"; printf '\n'
        done
        printf 'req=%q\n' "$req"
        printf '%s\n' 'build_definition_plan "$req" "$(select_package_manager)" "$(discover_panels)" "$(discover_ports)"'
    } | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$HOST" 'bash -s' > "$T/$name.json"
    jq '{plan_id, created_at, target, runnable, held: [.held[].component],
         steps: [.steps[] | {component, decision, action, reasons}]}' "$T/$name.json"
    ;;

approve)
    name="${1:-plan}"
    confirmation="${2:-}"
    [[ -f "$T/$name.json" ]] || { echo "No $name.json: run the 'plan' step first." >&2; exit 1; }
    "$PY" - "$T" "$T/$name.json" "$HOST" "$confirmation" <<'EOF'
import sys
from pathlib import Path
from bridge.components.approval import (ApprovalError, approve_plan,
    known_host_fingerprints, plan_digest, preflight_plan)

T, plan_path, host, confirmation = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
plan_bytes = plan_path.read_bytes()
keys = known_host_fingerprints(host)

result = preflight_plan(plan_bytes, keys)
print("Preflight passed :", result["passed"], result["issues"])
if not result["passed"]:
    sys.exit(1)
plan = result["plan"]
print("Plan             :", plan["plan_id"], "(hash", plan_digest(plan_bytes)[7:15] + ")")
print("Will run         :", plan["runnable"])
print("Held             :", [(h["component"], h["action"]) for h in plan["held"]])
if not confirmation:
    # stdin carries this script, so read the operator's answer from the
    # terminal directly. Only the newline is stripped: "APPROVE " fails.
    # A terminal is not seekable, so open it separately for each direction.
    try:
        with open("/dev/tty", "w") as out, open("/dev/tty") as inp:
            out.write("Type APPROVE to approve this plan: ")
            out.flush()
            confirmation = inp.readline().rstrip("\n")
    except OSError:
        print("No terminal for typing. Pass the confirmation as an argument:")
        print("  bash docs/testing/manual-4.32.sh approve", plan_path.stem, "APPROVE")
        sys.exit(1)
else:
    print("Confirmation     :", repr(confirmation))
try:
    record = approve_plan(plan_bytes, keys, confirmation, root=T / "approvals")
except ApprovalError as error:
    print("REFUSED          :", error, error.issues)
    sys.exit(1)
print("Approved         :", record["approved_components"], "by", record["approved_by"])
print("Expires          :", record["expires_at"])
EOF
    plan_id="$(jq -r .plan_id "$T/$name.json")"
    if [[ -d "$T/approvals/$plan_id" ]]; then
        echo; ls -la "$T/approvals" "$T/approvals/$plan_id"
    fi
    ;;

check|consume)
    if [[ "$step" == consume ]]; then
        mode=consume; name="${1:-plan}"
    else
        mode="${1:-normal}"; name="${2:-plan}"
    fi
    plan_id="$(need_plan_id "$name")"
    "$PY" - "$T" "$plan_id" "$HOST" "$mode" <<'EOF'
import sys, json
from datetime import timedelta
from pathlib import Path
from bridge.components.approval import (ApprovalError, _parse_time,
    check_approval, consume_approval, known_host_fingerprints)

T, plan_id, host, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
keys = known_host_fingerprints(host)
now = None
if mode == "expired":
    created = json.loads((T / "approvals" / plan_id / "plan.json").read_text())["created_at"]
    now = _parse_time(created) + timedelta(minutes=31)
    print("Simulating time  :", now.strftime("%H:%M:%SZ"), "(plan created", created + ")")
elif mode == "wrong-key":
    keys = {"SHA256:" + "B" * 43}
    print("Simulating a changed host key")
elif mode == "consume":
    try:
        plan = consume_approval(plan_id, keys, root=T / "approvals")
        print("Consumed. Apply would now run:", plan["runnable"])
    except ApprovalError as error:
        print("REFUSED:", error, error.issues)
    sys.exit(0)
result = check_approval(plan_id, keys, now=now, root=T / "approvals")
print("Status :", result["status"])
for gate, ok in result["checks"].items():
    print(f"  {gate:<12} {ok}")
print("Issues :", result["issues"])
EOF
    ;;

tamper)
    plan_id="$(need_plan_id)"
    cp "$T/approvals/$plan_id/plan.json" "$T/plan.backup"
    printf ' ' >> "$T/approvals/$plan_id/plan.json"
    echo "Appended one space to the approved plan.json (backup saved)."
    ;;

restore)
    plan_id="$(need_plan_id)"
    cat "$T/plan.backup" > "$T/approvals/$plan_id/plan.json"
    echo "Restored the approved plan.json from backup."
    ;;

audit)
    jq -c '{event, status, actor, target_id, request_id}' "$T/audit/security.jsonl"
    ;;

cleanup)
    rm -rf -- "$T"
    echo "Removed $T"
    ;;

*)
    sed -n '2,13p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
