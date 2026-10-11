#!/usr/bin/env bash

# Read-only package management capability discovery.
# Can run locally or on a remote Linux server.

detect_package_manager() {
    local manager path
    local -a detected=()

    for manager in apt-get dpkg dnf yum rpm apk zypper pacman; do
        if path="$(command -v "$manager" 2>/dev/null)"; then
            detected+=("$manager")
        fi
    done

    printf '%s\n' "${detected[@]}" |
        jq -R -s '
            split("\n")
            | map(select(length > 0))
            | {
                available_package_tools: .,
                package_manager_detected: (length > 0)
              }
        '
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    detect_package_manager
fi