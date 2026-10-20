#!/usr/bin/env bash

# Coordinate a fixed list of supported components.
setup_selected_services() (
    set -euo pipefail
    local mode="apply"

    if [[ "${1:-}" == "--plan" ]]; then
        mode="plan"
        shift
    fi

    [[ "$EUID" -eq 0 ]] || {
        echo "ERROR: Root privileges are required." >&2
        exit 1
    }

    [[ $# -gt 0 ]] || {
        echo "ERROR: No components selected." >&2
        exit 2
    }

    # Validate all selections before making any changes.
    for component in "$@"; do
        case "$component" in
            nginx|git) ;;
            *)
                printf 'ERROR: Unsupported component: %s\n' \
                    "$component" >&2
                exit 2
                ;;
        esac
    done
# Reject incomplete bundles before processing any component.
for component in "$@"; do
    case "$component" in
        nginx)
            required_functions=(
                install_nginx
                verify_nginx_configuration
                verify_nginx_readiness
            )
            ;;
        git)
            required_functions=(install_git verify_git)
            ;;
    esac

    for function_name in "${required_functions[@]}"; do
        declare -F "$function_name" >/dev/null || {
            printf 'ERROR: Missing backend function: %s\n' \
                "$function_name" >&2
            exit 3
        }
    done
done
    [[ -r /etc/os-release ]] || {
        echo "ERROR: Operating-system information unavailable." >&2
        exit 1
    }

    source /etc/os-release

    printf 'Detected OS: %s\n' "${PRETTY_NAME:-unknown}" >&2

    local manager
    manager="$(select_package_manager)" || exit 1
    printf 'Selected package manager: %s\n' "$manager" >&2
    declare -F discover_panels >/dev/null || {
    echo "ERROR: Panel discovery function is missing." >&2
    exit 3
}
local panel_facts

panel_facts="$(discover_panels)" || {
    echo "ERROR: Panel discovery failed." >&2
    exit 1
}

printf 'Panel discovery: %s\n' "$panel_facts" >&2
    local port_facts plan

    port_facts="$(discover_ports)" || {
        echo "ERROR: Port discovery failed." >&2
        exit 1
    }

    plan="$(
        build_component_plan \
            "$manager" "$panel_facts" "$port_facts" "$@"
    )" || {
        echo "ERROR: Component planning failed." >&2
        exit 1
    }

    # Plan-only mode returns JSON without executing component installers.
    if [[ "$mode" == "plan" ]]; then
        printf '%s\n' "$plan"
        exit 0
    fi

    printf '\n=== COMPONENT PLAN ===\n' >&2
    printf '%s\n' "$plan" >&2

    if ! jq -e '.eligible == true' <<< "$plan" >/dev/null; then
        echo "ERROR: Plan requires review; no components were changed." >&2
        exit 4
    fi

    # Confirm the displayed plan before executing any component action.
    local confirmation

    printf '\nReview the component plan above.\n' >&2
    printf 'Type APPLY to execute this plan, or anything else to cancel: ' \
        > /dev/tty

    if ! IFS= read -r confirmation < /dev/tty; then
        echo "ERROR: Could not read confirmation; no changes attempted." >&2
        exit 2
    fi

    if [[ "$confirmation" != "APPLY" ]]; then
        echo "Setup cancelled; no component changes attempted." >&2
        exit 2
    fi

    # Materialize plan steps before execution so parsing failures are caught.
    local plan_steps action

    plan_steps="$(
        jq -er '
            .steps
            | .[]
            | [.component, .action]
            | @tsv
        ' <<< "$plan"
    )" || {
        echo "ERROR: Unable to read execution plan." >&2
        exit 3
    }

    # Execute the deduplicated plan in its generated order.
    while IFS=$'\t' read -r component action; do
        printf '\nComponent: %s | Action: %s\n' \
            "$component" "$action" >&2

        case "$action" in
            SKIP)
                printf 'RESULT: component=%s package_action=skipped service_action=unchanged\n' \
                    "$component"
                ;;

            INSTALL)
                case "$component" in
                    nginx)
                        install_nginx "$manager" "$panel_facts"
                        ;;
                    git)
                        install_git "$manager"
                        ;;
                    *)
                        echo "ERROR: Unsupported installation component." >&2
                        exit 3
                        ;;
                esac
                ;;

            *)
                printf 'ERROR: Plan action is not executable: %s\n' \
                    "$action" >&2
                exit 4
                ;;
        esac
    done <<< "$plan_steps"
)
