#!/usr/bin/env bash

# Read-only application-state discovery.
# Requires package manager functions to be sourced first.
# Produces JSON and performs no package or service changes.



application_package_state() {
    local manager="$1"
    local package="$2"
    local installed_packages

    # Application package names must be simple package identifiers.
    # They must never be treated as commands or shell expressions.
    if [[ ! "$package" =~ ^[a-zA-Z0-9][a-zA-Z0-9.+_-]*$ ]]; then
        printf '%s\n' "unknown"
        return 0
    fi

    case "$manager" in
        apt)
            if ! command -v dpkg-query >/dev/null 2>&1; then
                printf '%s\n' "unknown"
                return 0
            fi

            # Query installed packages directly.
            # A successful query with no matching record means
            # the package is absent from the installed database.
            if ! installed_packages="$(
                dpkg-query -W \
                    -f='${binary:Package}\t${db:Status-Abbrev}\n' \
                    2>/dev/null
            )"; then
                printf '%s\n' "unknown"
                return 0
            fi

            if printf '%s\n' "$installed_packages" |
                awk -F '\t' -v requested="$package" '
                    $1 == requested && substr($2, 1, 2) == "ii" {
                        found = 1
                    }
                    END { exit !found }
                '; then
                printf '%s\n' "installed"
            else
                printf '%s\n' "absent"
            fi
            ;;

        dnf)
            if ! command -v rpm >/dev/null 2>&1; then
                printf '%s\n' "unknown"
                return 0
            fi

            if ! installed_packages="$(
                rpm -qa --qf '%{NAME}\n' 2>/dev/null
            )"; then
                printf '%s\n' "unknown"
                return 0
            fi

            if printf '%s\n' "$installed_packages" |
                grep -Fxq -- "$package"; then
                printf '%s\n' "installed"
            else
                printf '%s\n' "absent"
            fi
            ;;

        *)
            printf '%s\n' "unknown"
            ;;
    esac
}


application_binary_path() {
    local binary="$1"
    local path

    if path="$(command -v "$binary" 2>/dev/null)" &&
       [[ "$path" == /* ]]; then
        printf '%s\n' "$path"
    else
        printf '%s\n' ""
    fi
}


application_service_state() {
    local service="$1"
    local state

    if ! command -v systemctl >/dev/null 2>&1; then
        printf '%s\n' "unknown"
        return 0
    fi

    state="$(systemctl is-active "$service" 2>/dev/null)" || true

    case "$state" in
        active|inactive|failed|activating|deactivating)
            printf '%s\n' "$state"
            ;;
        *)
            printf '%s\n' "unknown"
            ;;
    esac
}


discover_application_state() (
    set -euo pipefail

    local application="${1:-}"
    local manager="${2:-}"
    local package binary service
    local package_state binary_path service_state
    local plugin_state="not_applicable"

    # Only predefined application IDs are accepted.
    # Package names and service names are never caller-supplied.

    case "$application" in
        git)
            package="git"
            binary="git"
            service=""
            ;;

        nginx)
            package="nginx"
            binary="nginx"
            service="nginx"
            ;;

        docker)
            package="docker-ce"
            binary="docker"
            service="docker"
            ;;

        docker_compose)
            package="docker-compose-plugin"
            binary="docker"
            service=""
            ;;
        *)
            echo "ERROR: Unsupported application." >&2
            return 2
            ;;
    esac

    case "$manager" in
        apt|dnf) ;;
        *)
            echo "ERROR: Unsupported package manager." >&2
            return 2
            ;;
    esac

    command -v jq >/dev/null 2>&1 || {
        echo "ERROR: jq is required." >&2
        return 127
    }

    package_state="$(
        application_package_state "$manager" "$package"
    )" || {
        echo "ERROR: Package-state discovery failed." >&2
        return 1
    }

    binary_path="$(application_binary_path "$binary")"

    if [[ -n "$service" ]]; then
        service_state="$(application_service_state "$service")"
    else
        service_state="not_applicable"
    fi

    # Docker Compose is a Docker CLI plugin, not necessarily
    # a standalone executable. Do not treat the docker binary
    # as proof that Compose is installed.
    if [[ "$application" == "docker_compose" ]]; then
        plugin_state="unknown"

        if [[ -n "$binary_path" ]] &&
           timeout 5 docker compose version \
               >/dev/null 2>&1; then
            plugin_state="available"
        fi
    fi

    jq -cn \
        --arg application "$application" \
        --arg manager "$manager" \
        --arg package "$package" \
        --arg package_state "$package_state" \
        --arg binary_path "$binary_path" \
        --arg service_state "$service_state" \
        --arg plugin_state "$plugin_state" \
        '{
            schema_version: "1.0",
            application_id: $application,
            package_manager: $manager,
            package: {
                name: $package,
                state: $package_state
            },
            binary: {
                path: (
                    if $binary_path == ""
                    then null
                    else $binary_path
                    end
                ),
                present: ($binary_path != "")
            },
            service: {
                state: $service_state
            },
            plugin: {
                state: $plugin_state
            },
            read_only: true,
            execution_allowed: false
        }'
)
