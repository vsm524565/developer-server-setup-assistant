#!/usr/bin/env bash

# Produce one component decision without changing the server.
plan_component() {
    local manager="$1"
    local panel_facts="$2"
    local port_facts="$3"
    local component="$4"
    local package action reason

    # Fixed mapping: selections never become arbitrary package names.
    case "$component" in
        nginx|git)
            package="$component"
            ;;
        *)
            echo "ERROR: Unsupported planning component." >&2
            return 2
            ;;
    esac

    # Reject incomplete discovery rather than assume no conflicts.
    jq -e '
        type == "object"
        and (.panels | type == "array")
        and all(.panels[]; type == "string")
    ' <<< "$panel_facts" >/dev/null || return 3

    jq -e '
        type == "object"
        and (.listening_tcp_ports | type == "array")
        and all(.listening_tcp_ports[];
            type == "number" and . >= 1 and . <= 65535
        )
    ' <<< "$port_facts" >/dev/null || return 3

    if package_is_installed "$manager" "$package"; then
        action="SKIP"
        reason="Package already installed; leave existing state unchanged."

    elif command -v "$component" >/dev/null 2>&1; then
        action="REVIEW"
        reason="Executable exists outside the detected package installation."

    elif [[ "$component" == "nginx" ]] &&
         [[ -e /etc/nginx || -e /usr/local/nginx ]]; then
        action="REVIEW"
        reason="Existing Nginx files require review."

    elif [[ "$component" == "nginx" ]] &&
         jq -e '.panels | length > 0' \
             <<< "$panel_facts" >/dev/null; then
        action="REVIEW"
        reason="Panel evidence found; Nginx installation requires review."

    elif [[ "$component" == "nginx" ]] &&
         jq -e '
             any(.listening_tcp_ports[]; . == 80 or . == 443)
         ' <<< "$port_facts" >/dev/null; then
        action="REVIEW"
        reason="TCP port 80 or 443 is occupied."

    else
        action="INSTALL"
        reason="Package missing; no conflicts found by current checks."
    fi

    jq -n \
        --arg component "$component" \
        --arg package "$package" \
        --arg manager "$manager" \
        --arg action "$action" \
        --arg reason "$reason" \
        '{
            component: $component,
            package: $package,
            package_manager: $manager,
            action: $action,
            reasons: [$reason]
        }'
}
# Assemble the complete plan before any installation is attempted.
build_component_plan() {
    local manager="$1"
    local panel_facts="$2"
    local port_facts="$3"
    shift 3

    [[ $# -gt 0 ]] || {
        echo "ERROR: No components selected for planning." >&2
        return 2
    }

    local component step
    local -a steps=()
    local -A seen=()

    for component in "$@"; do
        # Process repeated selections only once.
        if [[ -n "${seen[$component]:-}" ]]; then
            continue
        fi
        seen["$component"]=1

        step="$(
            plan_component \
                "$manager" "$panel_facts" "$port_facts" "$component"
        )" || return $?

        steps+=("$step")
    done

    printf '%s\n' "${steps[@]}" |
        jq -s '{
            schema_version: "1.0",
            eligible: all(.[];
                .action == "INSTALL" or .action == "SKIP"
            ),
            steps: .
        }'
}