
#!/usr/bin/env bash

# Read-only Docker Engine and Compose runtime observations.
# Does not start services, install packages or contact Docker daemon.

application_runtime_version() {
    local application="$1"
    local docker_path="$2"
    local version=""

    [[ "$docker_path" == /* && -x "$docker_path" ]] || return 1
    command -v timeout >/dev/null 2>&1 || return 1

    case "$application" in
        docker)
            version="$(
                timeout 5 "$docker_path" --version 2>/dev/null
            )" || return 1
            ;;

        docker_compose)
            version="$(
                timeout 5 "$docker_path" compose version 2>/dev/null
            )" || return 1
            ;;

        *)
            return 2
            ;;
    esac

    [[ -n "$version" ]] || return 1

    # Bound version output before including it in JSON.
    printf '%s\n' "${version:0:256}"
}


discover_application_runtime() (
    set -euo pipefail

    local application="${1:-}"
    local docker_path=""
    local cli_version=""
    local cli_status="unknown"
    local service_state="not_applicable"
    local socket_state="not_applicable"
    local plugin_status="not_applicable"

    case "$application" in
        docker|docker_compose) ;;
        *)
            echo "ERROR: Unsupported runtime application" >&2
            return 2
            ;;
    esac

    command -v jq >/dev/null 2>&1 || {
        echo "ERROR: jq is required" >&2
        return 127
    }

    docker_path="$(command -v docker 2>/dev/null)" || docker_path=""

    if [[ "$docker_path" != /* ]]; then
        docker_path=""
    fi

    if [[ "$application" == "docker" ]]; then

        service_state="unknown"
        socket_state="unknown"

        if command -v systemctl >/dev/null 2>&1; then
            service_state="$(
                systemctl is-active docker.service 2>/dev/null
            )" || true

            socket_state="$(
                systemctl is-active docker.socket 2>/dev/null
            )" || true

            case "$service_state" in
                active|inactive|failed|activating|deactivating) ;;
                *) service_state="unknown" ;;
            esac

            case "$socket_state" in
                active|inactive|failed|activating|deactivating) ;;
                *) socket_state="unknown" ;;
            esac
        fi

        if [[ -n "$docker_path" ]]; then
            if cli_version="$(
                application_runtime_version docker "$docker_path"
            )"; then
                cli_status="available"
            fi
        else
            cli_status="absent"
        fi

    else
        plugin_status="unknown"

        if [[ -n "$docker_path" ]]; then
            local compose_output=""

            if compose_output="$(
                timeout 5 "$docker_path" compose version 2>&1
            )"; then
                if [[ -n "$compose_output" ]]; then
                    cli_version="${compose_output:0:256}"
                    cli_status="available"
                    plugin_status="available"
                fi

            elif [[ "$compose_output" == *"unknown command: docker compose"* ||
                    "$compose_output" == *"is not a docker command"* ]]; then

                # Docker explicitly reports that Compose is unavailable.
                plugin_status="absent"
            fi
        else
            cli_status="absent"
        fi
    fi

    jq -cn \
        --arg application "$application" \
        --arg docker_path "$docker_path" \
        --arg cli_status "$cli_status" \
        --arg cli_version "$cli_version" \
        --arg service_state "$service_state" \
        --arg socket_state "$socket_state" \
        --arg plugin_status "$plugin_status" \
        '{
            schema_version: "1.0",
            application_id: $application,
            cli: {
                path: (
                    if $docker_path == ""
                    then null else $docker_path end
                ),
                status: $cli_status,
                version_output: (
                    if $cli_version == ""
                    then null else $cli_version end
                )
            },
            service: {
                state: $service_state
            },
            socket: {
                state: $socket_state
            },
            plugin: {
                state: $plugin_status
            },
            daemon_responsive: null,
            read_only: true,
            execution_allowed: false
        }'
)
