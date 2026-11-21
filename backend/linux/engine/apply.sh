#!/usr/bin/env bash

# Run an approved plan on the target (Milestone 4.34).
#
# Requires package_manager.sh, discovery/panel.sh, discovery/ports.sh,
# definitions.sh, plan.sh, journal.sh and the hook files.
#
# Rules (agreed designs 4.31 §6, 4.33 §12, 4.34 §6):
#   - Plan and definitions are re-verified here before any change.
#   - One transaction for the whole run (snapshot, lock, journal).
#   - Before each component its decision is re-checked on the live
#     server. A component whose decision changed since approval (drift)
#     does not run, nor do its required dependents; the rest continue.
#     Exception: a planned INSTALL that is now SKIP (same variant) is
#     verified only, since it does less than was approved (4.36).
#   - A failed component is rolled back (4.33); its required dependents
#     are not attempted; independent components continue.
#   - rollback_incomplete or an engine error stops the whole run.
#   - All command output goes to stderr; apply_plan prints one JSON value.
#
# Exit codes: 0 every runnable component installed or verified,
#             3 some components failed, were rolled back or skipped,
#             4 rollback incomplete or engine error (server needs review),
#             2 refused before any change.

# Usage: apply_plan PLAN_JSON DEFINITIONS_JSON
#   DEFINITIONS_JSON: [{"id": ..., "content": "<exact definition text>"}]
apply_plan() {
    local plan="$1" bundle="$2" template_bundle="${3:-[]}"
    local plan_id manager allow_draft definitions='{}' id content expected
    local paths results='[]' failed='[]' stop_reason="" step component action
    local variant_id definition result rc entry panels ports now_key planned_key already_present
    local blockers count index

    _tx_require_privilege || return 2

    jq -e 'type == "object" and .schema_version == "1.0"
           and (.steps | type == "array") and (.definitions | type == "object")
           and (.allow_draft | type == "boolean")' <<< "$plan" >/dev/null 2>&1 ||
        { _tx_fail "Invalid plan document."; return 2; }
    plan_id="$(jq -r .plan_id <<< "$plan")"
    _tx_valid_plan_id "$plan_id" || return 2
    allow_draft="$(jq -r .allow_draft <<< "$plan")"

    manager="$(select_package_manager)" || return 2
    [[ "$manager" == "$(jq -r .package_manager <<< "$plan")" ]] ||
        { _tx_fail "Package manager differs from the plan."; return 2; }

    # Exactly the planned definitions, each matching its planned digest.
    jq -e --argjson plan "$plan" '
        type == "array"
        and all(.[]; type == "object" and (.id | type == "string") and (.content | type == "string"))
        and ([.[].id] | sort) == ($plan.definitions | keys | sort)' <<< "$bundle" >/dev/null 2>&1 ||
        { _tx_fail "Definitions do not match the plan."; return 2; }
    while IFS= read -r id; do
        content="$(jq -r --arg id "$id" '.[] | select(.id == $id) | .content' <<< "$bundle")"
        expected="$(jq -r --arg id "$id" '.definitions[$id]' <<< "$plan")"
        [[ "$(_dssa_sha256 "$content")" == "$expected" ]] ||
            { _tx_fail "Definition digest mismatch: $id"; return 2; }
        validate_component_definition "$content" || return 2
        jq -e --arg id "$id" '.id == $id' <<< "$content" >/dev/null ||
            { _tx_fail "Definition ID mismatch: $id"; return 2; }
        definitions="$(jq -c --argjson d "$content" '. + {($d.id): $d}' <<< "$definitions")"
    done < <(jq -r '.definitions | keys[]' <<< "$plan")

    # Snapshot every path the runnable components may create.
    paths="$(jq -c --argjson plan "$plan" '
        . as $defs
        | [ $plan.steps[] | select(.action == "INSTALL" or .action == "SKIP") as $s
            | $defs[$s.component].variants[] | select(.variant_id == $s.variant_id)
            | .rollback.created_paths[] ]
          # 4.42: files a removal deletes are snapshotted too.
          + [ $plan.steps[] | select(.action == "REMOVE") | .details.repository_files[]?, .details.remove_files[]? ]
          | unique' <<< "$definitions")" || return 2

    # 4.45: exactly the plan's templates, each matching its digest.
    jq -e --argjson plan "$plan" '
        type == "array" and all(.[]; type == "object" and (.id | type == "string") and (.content | type == "string"))
        and ([.[].id] | sort) == (($plan.templates // {}) | keys | sort)' <<< "$template_bundle" >/dev/null 2>&1 ||
        { _tx_fail "Templates do not match the plan."; return 2; }
    while IFS= read -r id; do
        content="$(jq -r --arg id "$id" '.[] | select(.id == $id) | .content' <<< "$template_bundle")"
        [[ "$(_dssa_sha256 "$content")" == "$(jq -r --arg id "$id" '.templates[$id]' <<< "$plan")" ]] ||
            { _tx_fail "Template digest mismatch: $id"; return 2; }
    done < <(jq -r '.[].id' <<< "$template_bundle")
    DSSA_TEMPLATE_BUNDLE="$template_bundle"

    begin_transaction "$plan_id" "$manager" "$paths" || return 2

    count="$(jq '.steps | length' <<< "$plan")"
    for (( index = 0; index < count; index++ )); do
        step="$(jq -c --argjson i "$index" '.steps[$i]' <<< "$plan")"
        component="$(jq -r .component <<< "$step")"
        action="$(jq -r .action <<< "$step")"
        variant_id="$(jq -r '.variant_id // ""' <<< "$step")"
        definition="$(jq -c --arg c "$component" '.[$c]' <<< "$definitions")"

        if [[ "$action" != INSTALL && "$action" != SKIP && "$action" != REMOVE && "$action" != ADOPT && "$action" != UPDATE ]]; then
            result="$(jq -c '{component, outcome: "held", action, reasons, blocked_by}' <<< "$step")"
            failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")"
            results="$(jq -c --argjson r "$result" '. + [$r]' <<< "$results")"
            continue
        fi

        if [[ -n "$stop_reason" ]]; then
            result="$(jq -cn --arg c "$component" --arg r "$stop_reason" \
                '{component: $c, outcome: "not_attempted", reason: $r, blocked_by: []}')"
            results="$(jq -c --argjson r "$result" '. + [$r]' <<< "$results")"
            continue
        fi

        if [[ "$action" == REMOVE ]]; then
            # Removal runs dependents first: if removing one that needs
            # this component failed, this one must stay (4.42).
            blockers="$(jq -c --argjson failed "$failed" --arg c "$component" \
                '[$failed[] as $f | select(.[$f] != null)
                  | select([.[$f].depends_on[] | select(.required != false) | .id] | index($c)) | $f]' \
                <<< "$definitions")"
        else
            blockers="$(jq -c --argjson failed "$failed" \
                '[.depends_on[] | select(.required != false) | .id
                  | select(. as $d | $failed | index($d))]' <<< "$definition")"
        fi
        if [[ "$blockers" != "[]" ]]; then
            result="$(jq -cn --arg c "$component" --argjson b "$blockers" \
                '{component: $c, outcome: "not_attempted",
                  reason: "Required dependency did not complete: \($b | join(", ")).", blocked_by: $b}')"
            failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")"
            results="$(jq -c --argjson r "$result" '. + [$r]' <<< "$results")"
            continue
        fi

        # Drift re-check: decide again on the live server.
        printf 'Component %s: re-checking the server before it runs\n' "$component" >&2
        entry=""
        local key_jq='{variant_id, decision, action}'
        if [[ "$action" == REMOVE ]]; then
            # What would be removed must be exactly what was approved.
            key_jq='{decision, action, remove: [.details.remove_packages[]?.package], units: ((.details.stop_units // []) + (.details.disable_units // []))}'
            local record_now
            record_now="$(dssa_record_read)" &&
                entry="$(plan_remove_component "$definition" "$manager" "$record_now" "$(jq -c .selection <<< "$plan")")" || entry=""
        elif panels="$(discover_panels)" && ports="$(discover_ports)"; then
            if [[ "$action" == ADOPT ]]; then
                entry="$(plan_adopt_component "$definition" "$manager" "$panels" "$ports" "$(dssa_record_read)")" || entry=""
            elif [[ "$action" == UPDATE ]]; then
                # The same packages from the same versions; the target
                # versions are checked again after the list refresh.
                key_jq='{variant_id, decision, action, updates: [.details.updates[]? | {package, from}]}'
                entry="$(plan_update_component "$definition" "$manager" "$panels" "$ports")" || entry=""
            else
                entry="$(plan_component_from_definition "$definition" "$manager" "$panels" "$ports")" &&
                    entry="$(jq -c --argjson allow "$allow_draft" \
                        "$DSSA_PLAN_ALLOW_DRAFT_JQ allow_draft(\$allow)" <<< "$entry")" || entry=""
            fi
        fi
        planned_key="$(jq -c "$key_jq" <<< "$step")"
        now_key="$(jq -c "$key_jq" <<< "${entry:-null}" 2>/dev/null)" || now_key=null
        # An INSTALL that is now SKIP (same variant) is already in place,
        # e.g. pulled in by an earlier component's install: verify it only.
        # That does less than was approved, so it is not drift (4.36).
        already_present=false
        if [[ -n "$entry" ]] && jq -e --argjson p "$planned_key" --argjson n "$now_key" -n \
                '$p.action == "INSTALL" and $p.decision == "INSTALL"
                 and $n.action == "SKIP" and $n.decision == "SKIP"
                 and $p.variant_id == $n.variant_id' >/dev/null; then
            already_present=true
            action=SKIP
            printf 'Component %s: already installed at re-check; verifying only\n' "$component" >&2
        elif [[ -z "$entry" || "$planned_key" != "$now_key" ]]; then
            result="$(jq -cn --arg c "$component" --argjson planned "$planned_key" \
                --argjson now "${entry:-null}" \
                '{component: $c, outcome: "drift_skipped",
                  reason: (if $now == null then "Re-check could not run; nothing was changed."
                           else "Server changed since approval; nothing was changed." end),
                  planned: $planned,
                  now: (if $now == null then null else ($now | {variant_id, decision, action, reasons}) end)}')"
            failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")"
            results="$(jq -c --argjson r "$result" '. + [$r]' <<< "$results")"
            printf 'Component %s: drift detected, skipped\n' "$component" >&2
            continue
        fi

        if [[ "$action" == REMOVE ]]; then
            result="$(remove_component "$plan_id" "$manager" "$component" "$(jq -c .details <<< "$step")")"
            rc=$?
        elif [[ "$action" == ADOPT ]]; then
            result="$(adopt_component "$plan_id" "$manager" "$definition" "$variant_id")"
            rc=$?
        elif [[ "$action" == UPDATE ]]; then
            result="$(update_component "$plan_id" "$manager" "$definition" "$variant_id" "$(jq -c .details <<< "$step")")"
            rc=$?
        elif [[ "$action" == SKIP ]]; then
            result="$(verify_component "$manager" "$definition" "$variant_id")"
            rc=$?
            if [[ "$already_present" == true ]] && jq -e 'type == "object"' <<< "${result:-null}" >/dev/null 2>&1; then
                result="$(jq -c '. + {note: "Planned as INSTALL, already installed at re-check; verified only."}' <<< "$result")"
            fi
        else
            result="$(apply_component "$plan_id" "$manager" "$definition" "$variant_id" "$allow_draft")"
            rc=$?
        fi

        case "$rc" in
            0) ;;
            3|5) failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")" ;;
            4) failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")"
               stop_reason="Stopped: an earlier rollback was incomplete; the server needs review." ;;
            *) failed="$(jq -c --arg c "$component" '. + [$c]' <<< "$failed")"
               stop_reason="Stopped: an engine error left this component's state unknown."
               jq -e 'type == "object"' <<< "${result:-null}" >/dev/null 2>&1 ||
                   result="$(jq -cn --arg c "$component" \
                       '{component: $c, outcome: "error", failure: "Engine error; see the run log."}')" ;;
        esac
        results="$(jq -c --argjson r "$result" '. + [$r]' <<< "$results")"
    done

    # 4.44: all or nothing. Any failure undoes every component this run
    # changed, the last one first.
    if [[ "$(jq -r '.all_or_nothing // false' <<< "$plan")" == true ]] &&
       jq -e 'any(.[]; .outcome | IN("installed", "verified", "removed", "updated", "adopted") | not)' <<< "$results" >/dev/null; then
        printf 'All-or-nothing plan: a component did not complete; undoing the others.\n' >&2
        local undo undone='[]' kind
        while IFS= read -r component; do
            kind="$(jq -r --arg c "$component" '.[] | select(.component == $c) | .outcome' <<< "$results")"
            case "$kind" in
                installed) undo="$(rollback_component "$plan_id" "$manager" "$component")" &&
                               dssa_record_forget "$component" || true ;;
                removed) undo="$(rollback_remove "$plan_id" "$manager" "$component")" ;;
                updated) undo="$(rollback_update "$plan_id" "$manager" "$component")" ;;
                adopted) dssa_record_forget "$component" &&
                             undo='{"complete": true, "actions": [{"action": "record_forget", "result": "ok"}], "issues": []}' ||
                             undo='{"complete": false, "actions": [], "issues": ["Could not undo the adoption."]}' ;;
            esac
            results="$(jq -c --arg c "$component" --argjson u "${undo:-null}" '
                map(if .component == $c then
                      .outcome = (if ($u.complete // false) then "undone_all_or_nothing" else "rollback_incomplete" end)
                      | .undo = $u
                      | .reason = "Undone: another component in this all-or-nothing plan did not complete."
                    else . end)' <<< "$results")"
        done < <(jq -r '[.[] | select(.outcome | IN("installed", "removed", "updated", "adopted")) | .component] | reverse[]' <<< "$results")
    fi

    end_transaction "$plan_id" "$results" || {
        _tx_fail "Could not record the transaction result."
        jq -cn --argjson plan "$plan" --argjson r "$results" \
            '{schema_version: "1.0", plan_id: $plan.plan_id, target_id: $plan.target.target_id,
              status: "needs_review", results: $r}'
        return 4
    }

    jq -cn --argjson plan "$plan" --argjson r "$results" --arg at "$(_tx_now)" '
        ([$r[].outcome]) as $o
        | (if any($o[]; . == "rollback_incomplete" or . == "error") then "needs_review"
           elif any($o[]; . == "failed_rolled_back" or . == "drift_skipped"
                          or . == "verification_failed" or . == "not_attempted"
                          or . == "undone_all_or_nothing") then "partial"
           else "completed" end) as $status
        | {schema_version: "1.0", plan_id: $plan.plan_id, target_id: $plan.target.target_id,
           package_manager: $plan.package_manager, finished_at: $at, status: $status,
           counts: (reduce $o[] as $x ({}; .[$x] += 1)), results: $r}'

    case "$(jq -r '[.[].outcome] | if any(. == "rollback_incomplete" or . == "error") then 4
                    elif any(. == "failed_rolled_back" or . == "drift_skipped"
                             or . == "verification_failed" or . == "not_attempted"
                             or . == "undone_all_or_nothing") then 3
                    else 0 end' <<< "$results")" in
        0) return 0 ;;
        3) return 3 ;;
        *) return 4 ;;
    esac
}
