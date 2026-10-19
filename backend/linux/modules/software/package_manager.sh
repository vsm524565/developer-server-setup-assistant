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
# Select an installation method with its matching package database.
select_package_manager() {
    if command -v apt-get >/dev/null 2>&1 &&
       command -v dpkg-query >/dev/null 2>&1; then
        printf '%s\n' "apt"
    elif command -v dnf >/dev/null 2>&1 &&
         command -v rpm >/dev/null 2>&1; then
        printf '%s\n' "dnf"
    else
        echo "ERROR: No supported package manager found." >&2
        return 1
    fi
}

# Distinguish installed packages from merely available executables.
package_is_installed() {
    local manager="$1"
    local package="$2"
    local status

    case "$manager" in
        apt)
            status="$(
                dpkg-query -W -f='${Status}' "$package" 2>/dev/null
            )" || return 1

            [[ "$status" == "install ok installed" ]]
            ;;
        dnf)
            rpm -q -- "$package" >/dev/null 2>&1
            ;;
        *)
            return 2
            ;;
    esac
}
