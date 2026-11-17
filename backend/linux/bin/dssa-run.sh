#!/usr/bin/env bash
# DSSA run entry point on the control machine (Milestone 4.34).
#
# The only path that changes servers. Python (main.py) calls this script
# and never opens SSH or sees a password: `sudo` asks on the remote
# terminal. Subcommands:
#
#   targets                                   JSON list of inventory targets
#   plan [--allow-draft] TARGET COMPONENT...  read-only plan, no sudo; prints the plan JSON
#   apply TARGET PLAN_FILE PLAN_SHA256        run an approved plan (sudo on the target)
#   report TARGET PLAN_ID HOST_KEY            fetch the result of an earlier run (sudo)
#   resume-rollback TARGET PLAN_ID HOST_KEY   roll back an interrupted run (sudo)
#
# Host identity (4.34 §5): `plan` reads the key the server presents and
# binds it into the plan; every later session trusts only that key, so
# SSH refuses any other key before a command runs.
#
# apply/report/resume stream the remote terminal to stdout. Result files
# come back as DSSA_FILE / DSSA_STATUS / DSSA_EXIT marker lines.

set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
LINUX="$BASE_DIR/backend/linux"
INVENTORY="$LINUX/connection/inventory.sh"
AUDIT_SCRIPT="$LINUX/logging/audit.sh"
DEFINITIONS_DIR="${DSSA_DEFINITIONS_DIR:-$BASE_DIR/definitions/components}"
SSH_KEY="${DSSA_SSH_KEY:-$HOME/.ssh/id_ed25519}"
KNOWN_HOSTS="${DSSA_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}"
SSH_BIN="${DSSA_SSH_BIN:-ssh}"
PLAN_ID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
HOST_KEY_RE='^SHA256:[A-Za-z0-9+/]{43}$'
DIGEST_RE='^sha256:[0-9a-f]{64}$'

# shellcheck source=/dev/null
source "$LINUX/engine/definitions.sh"
# shellcheck source=/dev/null
source "$LINUX/engine/plan.sh"

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

die() { printf 'ERROR: %s\n' "$2" >&2; exit "$1"; }

usage() {
    sed -n '2,20p' "${BASH_SOURCE[0]}" >&2
    exit 2
}

# --- target and audit ---------------------------------------------------------

resolve_target() {
    local target_json
    target="$1"
    target_json="$("$INVENTORY" "$target" 2>/dev/null)" ||
        die 3 "Target \"$target\" could not be resolved from the inventory."
    host="$(jq -er .host <<< "$target_json")"
    port="$(jq -er .ssh_port <<< "$target_json")"
    user="$(jq -er .ssh_user <<< "$target_json")"
    lab="$(jq -r '.lab == true' <<< "$target_json")"
    [[ "$host" =~ ^[a-zA-Z0-9.-]+$ && "$user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ &&
       "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) ||
        die 3 "Invalid inventory fields."
    [[ -f "$SSH_KEY" && -f "$KNOWN_HOSTS" ]] || die 3 "SSH identity or known_hosts missing."
    if [[ "$port" == 22 ]]; then host_spec="$host"; else host_spec="[$host]:$port"; fi
}

audit() {
    # Usage: audit EVENT STATUS EXIT_CODE DETAILS_JSON. Never includes credentials.
    jq -n --arg request_id "run-$(cat /proc/sys/kernel/random/uuid)" --arg target "$target" \
        --arg event "$1" --arg status "$2" --argjson exit_code "$3" --argjson details "$4" \
        '{schema_version: "1.0", request_id: $request_id, source: "linux-backend",
          event: $event, operation: "run", target_id: $target, status: $status,
          exit_code: $exit_code, details: $details}' | "$AUDIT_SCRIPT"
}

# --- SSH ---------------------------------------------------------------------

ssh_base() {
    # Prints the shared SSH options, one per line.
    printf '%s\n' -F /dev/null -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes \
        -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
        -o ConnectTimeout=10 -o ForwardAgent=no -o ClearAllForwardings=yes \
        -o ControlMaster=no -o ControlPath=none -p "$port" -l "$user"
}

# The key the server presents in a real session (DEBUG1 shows it).
presented_host_key() {
    local -a opts
    local log
    mapfile -t opts < <(ssh_base)
    log="$WORK/hostkey.log"
    "$SSH_BIN" "${opts[@]}" -o UserKnownHostsFile="$KNOWN_HOSTS" -o LogLevel=DEBUG1 \
        -- "$host" true < /dev/null > /dev/null 2> "$log" ||
        die 3 "Could not connect to $host to read its host key (is it in known_hosts?)."
    sed -n 's/^debug1: Server host key: [^ ]* \(SHA256:[A-Za-z0-9+/]*\)\r*$/\1/p' "$log" | head -1
}

# Write a known_hosts file holding only the lines for this exact key, and
# the matching host key algorithms. Fails if known_hosts lacks the key.
pin_host_key() {
    local fingerprint="$1" line type key listed
    pin_file="$WORK/pinned_known_hosts"
    pin_algorithms=""
    : > "$pin_file"
    while IFS= read -r line; do
        [[ -n "$line" && "$line" != \#* && "$line" != @* ]] || continue
        read -r _ type key _ <<< "$line"
        listed="$(printf '%s %s\n' "$type" "$key" | ssh-keygen -l -f - 2>/dev/null | awk '{print $2}')" || continue
        [[ "$listed" == "$fingerprint" ]] || continue
        printf '%s\n' "$line" >> "$pin_file"
        case "$type" in
            ssh-rsa) pin_algorithms="rsa-sha2-512,rsa-sha2-256" ;;
            *) pin_algorithms="$type" ;;
        esac
    done < <(ssh-keygen -F "$host_spec" -f "$KNOWN_HOSTS" 2>/dev/null || true)
    [[ -s "$pin_file" && -n "$pin_algorithms" ]] ||
        die 3 "The plan's host key $fingerprint is not in known_hosts for $host_spec."
}

pinned_opts() {
    ssh_base
    printf '%s\n' -o UserKnownHostsFile="$pin_file" -o HostKeyAlgorithms="$pin_algorithms"
}

# --- bundles -----------------------------------------------------------------

emit_sources() {
    local file
    for file in "$@"; do
        cat -- "$LINUX/$file"
        printf '\n'
    done
}

engine_bundle() {
    printf '%s\n' '#!/usr/bin/env bash' 'set -uo pipefail'
    emit_sources modules/software/package_manager.sh discovery/panel.sh discovery/ports.sh \
        engine/definitions.sh engine/plan.sh engine/journal.sh engine/apply.sh engine/runner.sh
    local hook
    for hook in "$LINUX"/engine/hooks/*.sh; do
        cat -- "$hook"
        printf '\n'
    done
}

# Stage a bundle in the remote user's home (no sudo), then run it as root:
# root copies it to a root-only file, checks its digest, and runs the copy.
run_as_root() {
    local bundle_file="$1" kind="$2" plan_id="$3" digest stage remote root_cmd
    local -a opts
    mapfile -t opts < <(pinned_opts)
    digest="sha256:$(sha256sum -- "$bundle_file" | awk '{print $1}')"
    stage=".cache/dssa/$plan_id-$kind.sh"

    "$SSH_BIN" "${opts[@]}" -- "$host" \
        "umask 077 && mkdir -p .cache/dssa && cat > $stage" < "$bundle_file" ||
        die 255 "Could not stage the bundle on $host."

    # One line on purpose: printf %q then quotes it with backslashes only,
    # which any POSIX login shell on the target understands ($'...' needs bash).
    # shellcheck disable=SC2016
    root_cmd='s="${DSSA_ROOT:-}/var/lib/dssa"; mkdir -p -m 700 "$s/stage" && chmod 700 "$s" "$s/stage" || exit 2; dst="$(mktemp "$s/stage/bundle.XXXXXX")" || exit 2; cat -- "$1" > "$dst"; rm -f -- "$1"; [ "sha256:$(sha256sum -- "$dst" | cut -d" " -f1)" = "$2" ] || { echo "ERROR: Bundle digest mismatch; nothing was run." >&2; rm -f -- "$dst"; exit 2; }; exec bash "$dst" "$3"'
    remote="sudo -- bash -c $(printf %q "$root_cmd") _ $(printf %q "$stage") $(printf %q "$digest") $(printf %q "$kind")"

    set +e
    "$SSH_BIN" "${opts[@]}" -t -- "$host" "$remote"
    rc=$?
    set -e
    # The target never exits above 128 (a killed run reports 4), so this
    # means the local ssh itself was killed: like 255, the connection
    # ended and remote completion is unknown.
    if (( rc > 128 && rc != 255 )); then
        rc=255
    fi
    # A killed ssh cannot restore the terminal it put in raw mode.
    if (( rc == 255 )) && [[ -t 0 ]]; then
        stty sane 2>/dev/null || true
    fi
    return "$rc"
}

# --- subcommands ---------------------------------------------------------------

cmd_targets() {
    local file="${DSSA_INVENTORY_FILE:-$BASE_DIR/config/inventory/servers.json}"
    jq -c '[.servers[] | select(.enabled == true and .platform == "linux")
            | {target_id, host, lab: (.lab == true)}]' "$file"
}

cmd_plan() {
    local allow=() key request plan_json
    if [[ "${1:-}" == --allow-draft ]]; then allow=(--allow-draft); shift; fi
    [[ $# -ge 2 ]] || usage
    resolve_target "$1"
    shift
    [[ ${#allow[@]} -eq 0 || "$lab" == true ]] ||
        die 2 "--allow-draft is allowed only for inventory targets marked \"lab\": true."

    key="$(presented_host_key)"
    [[ "$key" =~ $HOST_KEY_RE ]] || die 3 "Could not read the host key presented by $host."
    pin_host_key "$key"
    printf 'Host key presented by %s: %s\n' "$host" "$key" >&2

    request="$(build_plan_request "${allow[@]}" "$target" "$key" "$DEFINITIONS_DIR" "$@")" || exit 2

    local -a opts
    mapfile -t opts < <(pinned_opts)
    {
        printf '%s\n' 'set -uo pipefail'
        emit_sources modules/software/package_manager.sh discovery/panel.sh discovery/ports.sh \
            engine/definitions.sh engine/plan.sh
        printf 'req=%q\n' "$request"
        printf '%s\n' 'build_definition_plan "$req" "$(select_package_manager)" "$(discover_panels)" "$(discover_ports)"'
    } > "$WORK/plan-bundle.sh"

    plan_json="$("$SSH_BIN" "${opts[@]}" -- "$host" 'bash -s' < "$WORK/plan-bundle.sh")" ||
        die 3 "Planning failed on $host."
    [[ "$(jq -s 'length' <<< "$plan_json" 2>/dev/null)" == 1 ]] ||
        die 3 "The target did not return exactly one plan."
    audit run.planned planned 0 "$(jq -c '{plan_id, runnable, held: [.held[].component], allow_draft}' <<< "$plan_json")" ||
        die 3 "Audit recording failed."
    printf '%s\n' "$plan_json"
}

cmd_apply() {
    [[ $# -eq 3 ]] || usage
    local plan_file="$2" expected="$3" plan plan_id key actual definitions='[]' id content rc
    resolve_target "$1"
    [[ -f "$plan_file" && ! -L "$plan_file" ]] || die 2 "Plan file missing."
    [[ "$expected" =~ $DIGEST_RE ]] || die 2 "Invalid plan digest."
    actual="sha256:$(sha256sum -- "$plan_file" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || die 2 "Plan file does not match the approved digest."

    plan="$(jq -c . "$plan_file")" || die 2 "Plan file is not JSON."
    plan_id="$(jq -r .plan_id <<< "$plan")"
    key="$(jq -r .target.host_key_sha256 <<< "$plan")"
    [[ "$plan_id" =~ $PLAN_ID_RE && "$key" =~ $HOST_KEY_RE ]] || die 2 "Invalid plan identity."
    [[ "$(jq -r .target.target_id <<< "$plan")" == "$target" ]] || die 2 "Plan is for a different target."
    [[ "$(jq -r .allow_draft <<< "$plan")" != true || "$lab" == true ]] ||
        die 2 "Plan allows draft variants but the target is not a lab target."
    pin_host_key "$key"

    # Exact definition texts, checked again on the target against the plan.
    dssa_check_definition_path "$DEFINITIONS_DIR" || die 2 "Unsafe definitions folder."
    while IFS= read -r id; do
        [[ "$id" =~ ^[a-z][a-z0-9_-]{0,63}$ && -f "$DEFINITIONS_DIR/$id.json" ]] ||
            die 2 "Definition missing: $id"
        dssa_check_definition_path "$DEFINITIONS_DIR/$id.json" || die 2 "Unsafe definition file: $id"
        content="$(cat -- "$DEFINITIONS_DIR/$id.json")"
        [[ "$(_dssa_sha256 "$content")" == "$(jq -r --arg id "$id" '.definitions[$id]' <<< "$plan")" ]] ||
            die 2 "Definition changed since planning: $id"
        definitions="$(jq -c --arg id "$id" --arg c "$content" '. + [{id: $id, content: $c}]' <<< "$definitions")"
    done < <(jq -r '.definitions | keys[]' <<< "$plan")

    {
        engine_bundle
        printf 'DSSA_PLAN_ID=%q\n' "$plan_id"
        printf 'DSSA_PLAN=%q\n' "$plan"
        printf 'DSSA_DEFINITIONS=%q\n' "$definitions"
        printf '%s\n' 'case "${1:-}" in' \
            '    start) runner_start "$DSSA_PLAN_ID" "$0" ;;' \
            '    run) runner_run "$DSSA_PLAN" "$DSSA_DEFINITIONS" ;;' \
            '    *) echo "ERROR: Unknown bundle mode." >&2; exit 2 ;;' \
            'esac'
    } > "$WORK/apply-bundle.sh"
    bash -n "$WORK/apply-bundle.sh" || die 2 "Generated bundle failed a syntax check."

    audit run.started started 0 "$(jq -c '{plan_id, runnable}' <<< "$plan")" ||
        die 3 "Audit recording failed. Nothing was run."
    printf 'Applying plan %s on %s (%s@%s); sudo will ask on the server.\n' \
        "$plan_id" "$target" "$user" "$host" >&2

    rc=0
    run_as_root "$WORK/apply-bundle.sh" start "$plan_id" || rc=$?
    audit run.completed "$( ((rc == 0)) && echo success || { ((rc == 255)) && echo unknown || echo failed; })" \
        "$rc" "$(jq -cn --arg p "$plan_id" '{plan_id: $p}')" ||
        echo "ERROR: Outcome audit recording failed; inspect the server before retrying." >&2
    exit "$rc"
}

cmd_remote_simple() {
    # report / resume-rollback
    local kind="$1"
    shift
    [[ $# -eq 3 ]] || usage
    local plan_id="$2" key="$3" rc=0
    resolve_target "$1"
    [[ "$plan_id" =~ $PLAN_ID_RE && "$key" =~ $HOST_KEY_RE ]] || die 2 "Invalid plan ID or host key."
    pin_host_key "$key"
    {
        engine_bundle
        printf 'DSSA_PLAN_ID=%q\n' "$plan_id"
        printf '%s\n' 'rm -f -- "$0"'
        case "$kind" in
            report) printf '%s\n' 'runner_report "$DSSA_PLAN_ID"' ;;
            resume) printf '%s\n' 'runner_resume "$DSSA_PLAN_ID" "$(select_package_manager)"' ;;
        esac
    } > "$WORK/$kind-bundle.sh"
    run_as_root "$WORK/$kind-bundle.sh" "$kind" "$plan_id" || rc=$?
    if [[ "$kind" == resume ]]; then
        audit run.resumed "$( ((rc == 0)) && echo success || echo failed)" "$rc" \
            "$(jq -cn --arg p "$plan_id" '{plan_id: $p}')" || true
    fi
    exit "$rc"
}

command="${1:-}"
shift || true
case "$command" in
    targets) cmd_targets ;;
    plan) cmd_plan "$@" ;;
    apply) cmd_apply "$@" ;;
    report) cmd_remote_simple report "$@" ;;
    resume-rollback) cmd_remote_simple resume "$@" ;;
    *) usage ;;
esac
