
#!/usr/bin/env bash

# DSSA - Swap Planning Module
#
# Purpose:
#   Build a read-only swap management plan.
#
# Responsibilities:
#   - Validate requested swap capacity.
#   - Validate discovered swap information.
#   - Classify existing swap configuration.
#   - Check active swap against persistence configuration.
#   - Return a structured planning decision.
#
# This module must NEVER modify swap or /etc/fstab.

plan_swap() {
    local discovery_json="${1:-}"
    local desired_total_bytes="${2:-}"

    local current_total_bytes
    local swap_device_count swap_entry_count
    local swap_type="unknown"
    local persistence_matches=false

    local decision action reason

    # --------------------------------------------------
    # 1. Validate requested capacity
    # --------------------------------------------------

    if [[ ! "$desired_total_bytes" =~ ^(0|[1-9][0-9]*)$ ]]; then
        echo "ERROR: Invalid desired swap size." >&2
        return 2
    fi

    # Restrict size to jq's exact integer range.
    if (( ${#desired_total_bytes} > 15 )); then
        echo "ERROR: Requested swap size exceeds supported range." >&2
        return 2
    fi

    # --------------------------------------------------
    # 2. Validate discovery information
    # --------------------------------------------------

    if ! jq -e '
        type == "object"
        and (.swap_total_bytes | type == "number" and . >= 0)
        and (.swap_used_bytes | type == "number" and . >= 0)
        and (.swap_used_bytes <= .swap_total_bytes)
        and (.swap_devices | type == "array")
        and (.swap_persistence | type == "object")
        and (.swap_persistence.readable | type == "boolean")
        and (.swap_persistence.entries | type == "array")
        and (
            .swap_devices |
            all(.[];
                type == "object"
                and (.name | type == "string" and length > 0)
                and (.type == "file" or .type == "partition")
                and (.size_bytes | type == "number" and . > 0)
                and (.used_bytes | type == "number" and . >= 0)
                and (.used_bytes <= .size_bytes)
                and (.priority | type == "number" and floor == .)
            )
        )
        and (
            .swap_persistence.entries |
            all(.[];
                type == "object"
                and (.source | type == "string" and length > 0)
                and (.mountpoint | type == "string")
                and (.filesystem_type == "swap")
                and (.options | type == "string")
            )
        )
    ' <<< "$discovery_json" >/dev/null 2>&1; then
        echo "ERROR: Invalid swap discovery facts." >&2
        return 3
    fi

    # --------------------------------------------------
    # 3. Extract discovered state
    # --------------------------------------------------

    current_total_bytes="$(
        jq -r '.swap_total_bytes' <<< "$discovery_json"
    )"

    swap_device_count="$(
        jq -r '.swap_devices | length' <<< "$discovery_json"
    )"

    swap_entry_count="$(
        jq -r '.swap_persistence.entries | length' <<< "$discovery_json"
    )"

    # --------------------------------------------------
    # 4. Classify existing swap
    # --------------------------------------------------

    if (( swap_device_count == 0 && swap_entry_count == 0 )); then
        swap_type="absent"

    elif (( swap_device_count == 1 && swap_entry_count == 1 )); then
        swap_type="$(
            jq -r '.swap_devices[0].type' <<< "$discovery_json"
        )"

    elif (( swap_device_count > 1 )); then
        swap_type="multiple"

    else
        swap_type="unknown"
    fi

    # --------------------------------------------------
    # 5. Verify persistence identity
    # --------------------------------------------------

    if jq -e '.swap_persistence.readable == true' \
        <<< "$discovery_json" >/dev/null; then

        if [[ "$swap_type" == "absent" ]]; then
            persistence_matches=true

        elif [[ "$swap_type" == "file" ]]; then
            # V1 supports direct file path matching only.
            if jq -e '
                .swap_devices[0].name as $active
                | .swap_persistence.entries[0].source as $persistent
                | $active == $persistent
            ' <<< "$discovery_json" >/dev/null; then
                persistence_matches=true
            fi
        fi
    fi

    # --------------------------------------------------
    # 6. Determine planning decision
    # --------------------------------------------------

    if ! jq -e '.swap_persistence.readable == true' \
        <<< "$discovery_json" >/dev/null; then

        decision="BLOCKED"
        action="NONE"
        reason="Persistent swap configuration cannot be inspected."

    elif [[ "$persistence_matches" != true ]]; then

        decision="BLOCKED"
        action="NONE"
        reason="Active swap and persistent configuration cannot be safely matched."

    elif [[ "$desired_total_bytes" == "$current_total_bytes" ]]; then

        decision="NO_CHANGE"
        action="NONE"
        reason="Requested swap capacity already matches current capacity."

    else

        decision="BLOCKED"
        action="NONE"
        reason="Swap modification safety checks are not yet implemented."
    fi

    # --------------------------------------------------
    # 7. Produce structured plan
    # --------------------------------------------------

    jq -n \
        --arg component "swap" \
        --arg decision "$decision" \
        --arg action "$action" \
        --arg swap_type "$swap_type" \
        --arg reason "$reason" \
        --argjson current_total_bytes "$current_total_bytes" \
        --argjson desired_total_bytes "$desired_total_bytes" \
        --argjson persistence_matches "$persistence_matches" \
        '{
            schema_version: "1.0",
            component: $component,
            decision: $decision,
            action: $action,
            swap_type: $swap_type,
            current_total_bytes: $current_total_bytes,
            desired_total_bytes: $desired_total_bytes,
            persistence_matches: $persistence_matches,
            reasons: [$reason],
            approval_required: false
        }'
}
