#!/usr/bin/env bash

# DSSA - Swap Safety Preflight
#
# Purpose:
#   Evaluate read-only safety evidence before a proposed swap change.
#
# Security contract:
#   - Never modify swap.
#   - Never modify /etc/fstab.
#   - Never authorize execution.
#   - Reject missing or inconsistent evidence.
#
# Inputs:
#   1. Swap plan JSON
#   2. Swap operation JSON
#   3. Swap readiness JSON
#   4. Filesystem discovery JSON
#   5. Explicit canonical target path

evaluate_swap_preflight() {
    if [[ $# -ne 5 ]]; then
        echo "ERROR: Expected five preflight arguments." >&2
        return 2
    fi

    local plan_json="$1"
    local operation_json="$2"
    local readiness_json="$3"
    local filesystem_json="$4"
    local target_path="$5"

    # Require an absolute, canonical path without traversal segments.
    if [[ "$target_path" != /* ||
          "$target_path" == "/" ||
          "$target_path" == *"//"* ||
          "$target_path" == */./* ||
          "$target_path" == */../* ||
          "$target_path" == */. ||
          "$target_path" == */.. ]]; then
        echo "ERROR: Invalid canonical swap target path." >&2
        return 2
    fi

    # Validate all supplied evidence before policy evaluation.
    if ! jq -e -n \
        --argjson plan "$plan_json" \
        --argjson operation "$operation_json" \
        --argjson readiness "$readiness_json" \
        --argjson filesystem "$filesystem_json" \
        --arg target "$target_path" '
        ($plan | type == "object")
        and ($operation | type == "object")
        and ($readiness | type == "object")
        and ($filesystem | type == "object")
        and ($plan.schema_version == "1.0")
        and ($operation.schema_version == "1.0")
        and ($readiness.schema_version == "1.0")
        and ($filesystem.schema_version == "1.0")
        and ($plan.component == "swap")
        and ($operation.component == "swap")
        and ($readiness.component == "swap")
        and ($plan.action == "NONE")
        and ($plan.approval_required == false)
        and ($operation.execution_allowed == false)
        and ($readiness.execution_allowed == false)
        and ($operation.operation == $readiness.operation)
        and ($plan.decision | IN("NO_CHANGE", "BLOCKED"))
        and ($plan.accounting_consistent == true)
        and ($plan.persistence_matches == true)
        and ($operation.operation |
            IN("NO_CHANGE", "CREATE", "INCREASE", "REDUCE", "REMOVE"))
        and ($readiness.status |
            IN("NOT_REQUIRED", "UNKNOWN", "BLOCKED"))
        and ($operation.current_total_bytes |
            type == "number" and floor == . and . >= 0)
        and ($operation.desired_total_bytes |
            type == "number" and floor == . and . >= 0)
        and (
            ($operation.operation == "NO_CHANGE"
                and $operation.current_total_bytes == $operation.desired_total_bytes
                and $plan.decision == "NO_CHANGE"
                and $readiness.status == "NOT_REQUIRED")
            or
            ($operation.operation == "CREATE"
                and $operation.current_total_bytes == 0
                and $operation.desired_total_bytes > 0
                and $plan.decision == "BLOCKED"
                and $readiness.status == "UNKNOWN")
            or
            ($operation.operation == "INCREASE"
                and $operation.current_total_bytes > 0
                and $operation.desired_total_bytes > $operation.current_total_bytes
                and $plan.decision == "BLOCKED")
            or
            ($operation.operation == "REDUCE"
                and $operation.desired_total_bytes > 0
                and $operation.desired_total_bytes < $operation.current_total_bytes
                and $readiness.status == "BLOCKED")
            or
            ($operation.operation == "REMOVE"
                and $operation.current_total_bytes > 0
                and $operation.desired_total_bytes == 0
                and $readiness.status == "BLOCKED")
        )
        and ($plan.current_total_bytes == $operation.current_total_bytes)
        and ($plan.desired_total_bytes == $operation.desired_total_bytes)
        and ($filesystem.target_path == $target)
        and ($filesystem.filesystem | type == "object")
        and ($filesystem.filesystem.mountpoint |
            type == "string" and length > 0)
        and ($filesystem.filesystem.source |
            type == "string" and length > 0)
        and ($filesystem.filesystem.type |
            type == "string" and length > 0)
        and ($filesystem.filesystem.available_bytes |
            type == "number"
            and floor == .
            and . >= 0
            and . <= 999999999999999)
        ' >/dev/null 2>&1; then
        echo "ERROR: Invalid or inconsistent swap preflight evidence." >&2
        return 3
    fi
    # Fail closed until the safety evaluation is implemented.
    local capacity_json
    local filesystem_policy_json
    local filesystem_type
    local operation_type
    local requested_bytes
    local available_bytes

    operation_type=$(jq -r '.operation' <<< "$operation_json")
    requested_bytes=$(jq -r '.desired_total_bytes' <<< "$operation_json")
    available_bytes=$(jq -r '.filesystem.available_bytes' <<< "$filesystem_json")
    filesystem_type=$(jq -r '.filesystem.type' <<< "$filesystem_json")

    if [[ "$operation_type" == "CREATE" ]]; then
        if ! capacity_json=$(
            evaluate_swap_capacity "$available_bytes" "$requested_bytes"
        ); then
            echo "ERROR: Swap capacity evaluation failed." >&2
            return 3
        fi
    else
        capacity_json='null'
    fi

    if [[ "$operation_type" == "CREATE" ]]; then
        if ! filesystem_policy_json=$(
            evaluate_swap_filesystem_policy "$filesystem_type"
        ); then
            echo "ERROR: Swap filesystem policy evaluation failed." >&2
            return 3
        fi
    else
        filesystem_policy_json='null'
    fi

    jq -n \
        --arg operation "$operation_type" \
        --arg target "$target_path" \
        --argjson filesystem_policy "$filesystem_policy_json" \
        --argjson capacity "$capacity_json" '
        {
            schema_version: "1.0",
            component: "swap",
            check: "execution_preflight",
            operation: $operation,
            target_path: $target,
            status: (
                if $operation == "NO_CHANGE" then "NOT_REQUIRED"
                elif $operation == "CREATE"
                    and $capacity.status == "PASS"
                    and $filesystem_policy.status == "CANDIDATE"
                then "UNKNOWN"
                else "BLOCKED"
                end
            ),
            capacity: $capacity,
            outstanding_checks: (
                if $operation == "CREATE" then
                    [
                        "FILESYSTEM_COMPATIBILITY",
                        "TARGET_IDENTITY",
                        "EVIDENCE_FRESHNESS",
                        "PERSISTENCE_SAFETY",
                        "ROLLBACK_READINESS"
                    ]
                else []
                end
            ),
            filesystem_policy: $filesystem_policy,
            execution_allowed: false
        }
    '
}
# Evaluate proposed swap allocation against free-space reserves.
# Read-only. A PASS result never authorizes execution.
evaluate_swap_capacity() {
    if [[ $# -ne 2 ]]; then
        echo "ERROR: Expected available and requested bytes." >&2
        return 2
    fi

    local available_bytes="$1"
    local requested_bytes="$2"

    if [[ ! "$available_bytes" =~ ^(0|[1-9][0-9]*)$ ||
          ! "$requested_bytes" =~ ^(0|[1-9][0-9]*)$ ||
          ${#available_bytes} -gt 15 ||
          ${#requested_bytes} -gt 15 ]]; then
        echo "ERROR: Invalid capacity inputs." >&2
        return 2
    fi

    jq -n \
        --argjson available "$available_bytes" \
        --argjson requested "$requested_bytes" '
        1073741824 as $minimum_reserve
        | ($available - $requested) as $remaining
        | (($available / 10) | ceil) as $percentage_reserve
        | ($remaining >= $minimum_reserve
           and $remaining >= $percentage_reserve
           and $requested > 0) as $sufficient
        | {
            schema_version: "1.0",
            component: "swap",
            check: "filesystem_capacity",
            status: (if $sufficient then "PASS" else "BLOCKED" end),
            available_bytes: $available,
            requested_bytes: $requested,
            remaining_bytes: $remaining,
            minimum_reserve_bytes: $minimum_reserve,
            percentage_reserve_bytes: $percentage_reserve,
            execution_allowed: false
          }
    '
}
# Read-only filesystem compatibility assessment.
# CANDIDATE does not mean execution is authorized.
evaluate_swap_filesystem_policy() {
    if [[ $# -ne 1 ]]; then
        echo "ERROR: Expected filesystem type." >&2
        return 2
    fi

    local filesystem_type="$1"

    if [[ -z "$filesystem_type" ]]; then
        echo "ERROR: Empty filesystem type." >&2
        return 2
    fi

    jq -n --arg fs "$filesystem_type" '
        {
            schema_version: "1.0",
            component: "swap",
            check: "filesystem_compatibility",
            filesystem_type: $fs,
            status: (
                if $fs == "ext4" then "CANDIDATE"
                else "BLOCKED"
                end
            ),
            execution_allowed: false
        }
    '
}
# Evaluate target identity evidence without modifying the filesystem.
# CANDIDATE means only that the target is reported absent.
# It never authorizes execution.
evaluate_swap_target_policy() {
    if [[ $# -ne 2 ]]; then
        echo "ERROR: Expected target identity JSON and target path." >&2
        return 2
    fi

    local identity_json="$1"
    local expected_path="$2"

    if ! jq -e \
        --arg expected_path "$expected_path" '
        type == "object"
        and .schema_version == "1.0"
        and .component == "swap"
        and .check == "target_identity"
        and .target_path == $expected_path
        and (.target_type | type == "string")
        and (.target_type | IN(
            "ABSENT",
            "REGULAR_FILE",
            "DIRECTORY",
            "SYMLINK",
            "OTHER",
            "UNKNOWN"
        ))
        and (.exists | type == "boolean")
        and (.is_symlink | type == "boolean")
        and .execution_allowed == false
        and (
            if .target_type == "ABSENT" then
                .exists == false and .is_symlink == false
            elif .target_type == "SYMLINK" then
                .exists == true and .is_symlink == true
            elif .target_type == "UNKNOWN" then
                .is_symlink == false
            else
                .exists == true and .is_symlink == false
            end
        )
    ' <<< "$identity_json" >/dev/null 2>&1; then
        echo "ERROR: Invalid swap target identity evidence." >&2
        return 3
    fi

    jq -n \
        --argjson identity "$identity_json" '
        {
            schema_version: "1.0",
            component: "swap",
            check: "target_identity_policy",
            target_path: $identity.target_path,
            target_type: $identity.target_type,
            status: (
                if $identity.target_type == "ABSENT"
                then "CANDIDATE"
                else "BLOCKED"
                end
            ),
            execution_allowed: false
        }
    '
}