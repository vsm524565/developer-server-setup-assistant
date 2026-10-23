#!/usr/bin/env bash

# DSSA - Swap Execution Readiness
#
# Classify proposed swap operations.
# Read-only. No swap or persistence modifications.

classify_swap_operation() {
    local current_bytes="${1:-}"
    local desired_bytes="${2:-}"

    # Accept only non-negative decimal integers.
    if [[ ! "$current_bytes" =~ ^(0|[1-9][0-9]*)$ ]] ||
       [[ ! "$desired_bytes" =~ ^(0|[1-9][0-9]*)$ ]]; then
        echo "ERROR: Invalid swap capacity." >&2
        return 2
    fi

    # Keep comparisons within the planner's supported range.
    if (( ${#current_bytes} > 15 ||
          ${#desired_bytes} > 15 )); then
        echo "ERROR: Swap capacity exceeds supported range." >&2
        return 2
    fi

    local operation

    if (( current_bytes == desired_bytes )); then
        operation="NO_CHANGE"
    elif (( current_bytes == 0 )); then
        operation="CREATE"
    elif (( desired_bytes == 0 )); then
        operation="REMOVE"
    elif (( desired_bytes > current_bytes )); then
        operation="INCREASE"
    else
        operation="REDUCE"
    fi

    jq -n \
        --arg operation "$operation" \
        --argjson current "$current_bytes" \
        --argjson desired "$desired_bytes" \
        '{
            schema_version: "1.0",
            component: "swap",
            operation: $operation,
            current_total_bytes: $current,
            desired_total_bytes: $desired,
            execution_allowed: false
        }'
}
# Evaluate a swap plan without authorizing execution.
evaluate_swap_readiness() {
    local plan_json="${1:-}"
    local operation_json="${2:-}"

    # Reject malformed or inconsistent evidence.
    if ! jq -e -n \
        --argjson plan "$plan_json" \
        --argjson operation "$operation_json" '
        ($plan | type == "object")
        and ($operation | type == "object")
        and ($plan.component == "swap")
        and ($operation.component == "swap")
        and ($plan.schema_version == "1.0")
        and ($operation.schema_version == "1.0")
        and ($plan.decision | IN("NO_CHANGE", "BLOCKED"))
        and ($plan.action == "NONE")
        and ($plan.approval_required == false)
        and ($plan.accounting_consistent | type == "boolean")
        and ($plan.persistence_matches | type == "boolean")
        and ($operation.execution_allowed == false)
        and ($operation.operation |
            IN("NO_CHANGE", "CREATE", "INCREASE", "REDUCE", "REMOVE"))
        and ($plan.current_total_bytes == $operation.current_total_bytes)
and ($plan.desired_total_bytes == $operation.desired_total_bytes)
and ($operation.current_total_bytes | type == "number")
and ($operation.desired_total_bytes | type == "number")
and ($operation.current_total_bytes >= 0)
and ($operation.desired_total_bytes >= 0)
and (
    ($operation.operation == "NO_CHANGE"
        and $operation.current_total_bytes == $operation.desired_total_bytes)
    or
    ($operation.operation == "CREATE"
        and $operation.current_total_bytes == 0
        and $operation.desired_total_bytes > 0)
    or
    ($operation.operation == "REMOVE"
        and $operation.current_total_bytes > 0
        and $operation.desired_total_bytes == 0)
    or
    ($operation.operation == "INCREASE"
        and $operation.current_total_bytes > 0
        and $operation.desired_total_bytes > $operation.current_total_bytes)
    or
    ($operation.operation == "REDUCE"
        and $operation.desired_total_bytes > 0
        and $operation.desired_total_bytes < $operation.current_total_bytes)
)
and (
    ($plan.decision == "NO_CHANGE"
        and $operation.operation == "NO_CHANGE")
    or
    ($plan.decision == "BLOCKED")
)
    ' >/dev/null 2>&1; then
        echo "ERROR: Invalid or inconsistent swap planning evidence." >&2
        return 3
    fi

    local operation decision status issue

    operation="$(jq -r '.operation' <<< "$operation_json")"
    decision="$(jq -r '.decision' <<< "$plan_json")"

    if [[ "$decision" == "NO_CHANGE" &&
          "$operation" == "NO_CHANGE" ]] &&
       jq -e '
           .accounting_consistent == true
           and .persistence_matches == true
       ' <<< "$plan_json" >/dev/null; then
        status="NOT_REQUIRED"
        issue="Existing swap configuration already matches the request."

    elif [[ "$operation" == "REDUCE" ||
            "$operation" == "REMOVE" ]]; then
        status="BLOCKED"
        issue="Reducing or removing swap is unsupported in V1."

    elif [[ "$operation" == "CREATE" ||
            "$operation" == "INCREASE" ]]; then
        if jq -e '
            .accounting_consistent == true
            and .persistence_matches == true
        ' <<< "$plan_json" >/dev/null; then
            status="UNKNOWN"
            issue="Disk, filesystem, persistence, and rollback checks are pending."
        else
            status="BLOCKED"
            issue="Swap accounting or persistence identity is not verified."
        fi

    else
        status="BLOCKED"
        issue="Swap planning evidence does not permit this operation."
    fi

    jq -n \
        --arg operation "$operation" \
        --arg status "$status" \
        --arg issue "$issue" \
        '{
            schema_version: "1.0",
            component: "swap",
            operation: $operation,
            status: $status,
            execution_allowed: false,
            issues: [$issue]
        }'
}
