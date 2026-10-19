#!/usr/bin/env bash

# Coordinate a fixed list of supported components.
setup_selected_services() (
    set -euo pipefail

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
            nginx) ;;
            *)
                printf 'ERROR: Unsupported component: %s\n' \
                    "$component" >&2
                exit 2
                ;;
        esac
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

    # Stop on failure until dependency-aware continuation is implemented.
    for component in "$@"; do
        printf '\nProcessing component: %s\n' "$component" >&2

        case "$component" in
            nginx)
                install_nginx "$manager"
                ;;
        esac
    done
)
