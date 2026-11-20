#!/usr/bin/env bash

# Target-side run control for apply, report and resume (Milestone 4.34).
#
# The laptop stages a bundle (engine + plan + definitions), root copies it
# into /var/lib/dssa/stage and checks its digest, then calls one of:
#
#   runner_start PLAN_ID    start apply_plan detached (setsid), follow its
#                           log, then send the result files back
#   runner_report PLAN_ID   send the files of an earlier run back
#   runner_resume PLAN_ID   resume_rollback an interrupted run
#
# The detached run survives a dropped SSH session; `main.py report`
# fetches its result later. Results travel back as marker lines that
# the laptop hides from the terminal:
#   DSSA_FILE <name> <base64>   one per file
#   DSSA_STATUS <word>          finished | running | interrupted | none
#   DSSA_EXIT <code>
#
# Requires preflight.sh and journal.sh (and, for runner_start, the full engine). The
# process umask is never changed (package scripts depend on it); DSSA's
# own files are created private one by one.

_runner_dir() { printf '%s/runs/%s' "$(_tx_state_dir)" "$1"; }

_runner_emit() {
    # Usage: _runner_emit NAME FILE   (missing files are skipped)
    [[ -f "$2" && ! -L "$2" ]] || return 0
    printf 'DSSA_FILE %s %s\n' "$1" "$(base64 -w0 -- "$2")"
}

_runner_emit_all() {
    local plan_id="$1" run tx
    run="$(_runner_dir "$plan_id")"
    tx="$(_tx_dir "$plan_id")"
    _runner_emit summary.json "$run/summary.json"
    _runner_emit resume.json "$run/resume.json"
    _runner_emit run.log "$run/run.log"
    _runner_emit journal.jsonl "$tx/journal.jsonl"
    _runner_emit result.json "$tx/result.json"
    _runner_emit snapshot.json "$tx/snapshot.json"
}

_runner_retention() {
    local root dir
    # Staged bundles normally live for seconds; old ones are leftovers.
    find "$(_tx_state_dir)/stage" -mindepth 1 -maxdepth 1 -type f -name 'bundle.*' \
        -mtime +"$DSSA_TX_RETENTION_DAYS" -delete 2>/dev/null || true
    root="$(_tx_state_dir)/runs"
    [[ -d "$root" ]] || return 0
    while IFS= read -r dir; do
        [[ "$(basename -- "$dir")" =~ $DSSA_PLAN_ID_PATTERN && -f "$dir/summary.json" && ! -L "$dir" ]] || continue
        rm -rf -- "$dir"
    done < <(find "$root" -mindepth 1 -maxdepth 1 -type d -mtime +"$DSSA_TX_RETENTION_DAYS" 2>/dev/null)
}

# Usage: runner_start PLAN_ID BUNDLE_PATH [PLAN_JSON DEFINITIONS_JSON]
# BUNDLE_PATH is this bundle's root-owned copy; it is moved into the run
# folder and run again there with the "run" argument. The plan and
# definitions decide which commands the run needs (4.39).
runner_start() {
    local plan_id="$1" bundle="$2" plan="${3:-}" definitions="${4:-}" state run pid rc

    _tx_require_privilege || return 2
    _tx_valid_plan_id "$plan_id" || return 2
    if ! dssa_require_commands apply "$plan" "$definitions" || ! _tx_check_state_dir; then
        rm -f -- "$bundle"
        printf 'DSSA_EXIT 2\n'
        return 2
    fi
    state="$(_tx_state_dir)"
    mkdir -p -- "$state/runs" && chmod 700 -- "$state" "$state/runs" || return 2
    _runner_retention

    run="$(_runner_dir "$plan_id")"
    mkdir -m 700 -- "$run" 2>/dev/null || {
        rm -f -- "$bundle"
        _tx_fail "This plan has already been run on this server."
        printf 'DSSA_EXIT 2\n'
        return 2
    }
    mv -f -- "$bundle" "$run/bundle.sh" && chmod 600 -- "$run/bundle.sh" || return 2
    (umask 077 && : > "$run/run.log") || return 2

    # New session: a dropped SSH connection does not stop the run.
    setsid bash "$run/bundle.sh" run >> "$run/run.log" 2>&1 < /dev/null &
    pid=$!
    tail -n +1 -f --pid="$pid" -- "$run/run.log" 2>/dev/null
    wait "$pid"
    rc=$?
    # tail may stop before the last lines are read; nothing is lost, the
    # whole log travels back below.
    _runner_emit_all "$plan_id"
    # No summary means the run process died before finishing (for
    # example killed): its transaction is unfinished, not complete.
    if [[ -f "$run/summary.json" ]]; then
        printf 'DSSA_STATUS finished\n'
    else
        printf 'DSSA_STATUS interrupted\n'
        echo "The run stopped before finishing. Check it with report; undo it with resume-rollback." >&2
        rc=4  # needs review; never pass a signal code (>128) back to the laptop
    fi
    printf 'DSSA_EXIT %s\n' "$rc"
    return "$rc"
}

# Usage: runner_run PLAN_JSON DEFINITIONS_JSON   (inside the detached run)
runner_run() {
    local plan="$1" definitions="$2" plan_id run summary rc
    plan_id="$(jq -r .plan_id <<< "$plan")"
    run="$(_runner_dir "$plan_id")"

    summary="$(apply_plan "$plan" "$definitions")"
    rc=$?
    if ! jq -e 'type == "object"' <<< "${summary:-null}" >/dev/null 2>&1; then
        summary="$(jq -cn --arg p "$plan_id" --argjson rc "$rc" \
            '{schema_version: "1.0", plan_id: $p, status: "refused", exit_code: $rc, results: [],
              reason: "Refused before any change; see the run log."}')"
    fi
    (umask 077 && jq . <<< "$summary" > "$run/summary.json.tmp") &&
        mv -f -- "$run/summary.json.tmp" "$run/summary.json"
    sync -- "$run/summary.json" 2>/dev/null || true
    return "$rc"
}

# Usage: runner_report PLAN_ID
runner_report() {
    local plan_id="$1" run tx status
    _tx_require_privilege || return 2
    _tx_valid_plan_id "$plan_id" || return 2
    dssa_require_commands report && _tx_check_state_dir || { printf 'DSSA_EXIT 2\n'; return 2; }
    run="$(_runner_dir "$plan_id")"
    tx="$(_tx_dir "$plan_id")"

    if [[ -f "$run/summary.json" ]]; then
        status=finished
    elif [[ -d "$run" || -d "$tx" ]]; then
        # Running if the run lock is held; otherwise it was interrupted.
        if [[ -f "$(_tx_state_dir)/run.lock" ]] &&
           ! flock -n "$(_tx_state_dir)/run.lock" true 2>/dev/null; then
            status=running
        elif [[ -d "$tx" && ! -f "$tx/result.json" ]]; then
            status=interrupted
        else
            status=finished
        fi
    else
        status=none
    fi

    _runner_emit_all "$plan_id"
    printf 'DSSA_STATUS %s\n' "$status"
    printf 'DSSA_EXIT 0\n'
}

# Usage: runner_resume PLAN_ID MANAGER
runner_resume() {
    local plan_id="$1" manager="$2" run result rc
    _tx_require_privilege || return 2
    _tx_valid_plan_id "$plan_id" || return 2
    dssa_require_commands apply && _tx_check_state_dir || { printf 'DSSA_EXIT 2\n'; return 2; }
    result="$(resume_rollback "$plan_id" "$manager")"
    rc=$?
    run="$(_runner_dir "$plan_id")"
    if [[ $rc -eq 0 ]] && jq -e 'type == "object"' <<< "${result:-null}" >/dev/null 2>&1; then
        mkdir -p -m 700 -- "$run" &&
            (umask 077 && jq . <<< "$result" > "$run/resume.json")
        if jq -e 'all(.results[]; .outcome == "failed_rolled_back")' <<< "$result" >/dev/null; then
            rc=0
        else
            rc=4
        fi
    else
        rc=2
    fi
    _runner_emit_all "$plan_id"
    printf 'DSSA_STATUS %s\n' "$([[ $rc -eq 2 ]] && echo none || echo finished)"
    printf 'DSSA_EXIT %s\n' "$rc"
    return "$rc"
}
