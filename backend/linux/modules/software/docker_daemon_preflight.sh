
#!/usr/bin/env bash

# Passive Docker daemon preflight.
# No Docker CLI API calls, socket connections or service changes.

discover_docker_daemon_preflight() (
    set -euo pipefail

    command -v jq >/dev/null 2>&1 || {
        echo "ERROR: jq is required" >&2
        return 127
    }

    local service_state="unknown"
    local socket_state="unknown"
    local main_pid=0
    local process_verified=false
    local socket_path="/run/docker.sock"
    local socket_present=false
    local socket_permissions=""
    local socket_owner=""
    local socket_group=""
    local listener_state="unknown"
    local preflight_status="UNVERIFIED"
    local reason="INSUFFICIENT_PASSIVE_EVIDENCE"

    # Read only systemd service/socket metadata.
    if command -v systemctl >/dev/null 2>&1; then
        service_state="$(
            systemctl show docker.service \
                --property=ActiveState \
                --value 2>/dev/null
        )" || service_state="unknown"

        socket_state="$(
            systemctl show docker.socket \
                --property=ActiveState \
                --value 2>/dev/null
        )" || socket_state="unknown"

        main_pid="$(
            systemctl show docker.service \
                --property=MainPID \
                --value 2>/dev/null
        )" || main_pid=0
    fi

    case "$service_state" in
        active|inactive|failed|activating|deactivating) ;;
        *) service_state="unknown" ;;
    esac

    case "$socket_state" in
        active|inactive|failed|activating|deactivating) ;;
        *) socket_state="unknown" ;;
    esac

    if [[ ! "$main_pid" =~ ^[0-9]+$ ]]; then
        main_pid=0
    fi

    # Verify that systemd's MainPID identifies dockerd.
    if (( main_pid > 0 )) &&
       [[ -r "/proc/$main_pid/comm" ]]; then

        local process_name=""
        IFS= read -r process_name \
            < "/proc/$main_pid/comm" || true

        if [[ "$process_name" == "dockerd" ]]; then
            process_verified=true
        fi
    fi

    # Inspect Unix socket metadata without connecting to it.
    if [[ -S "$socket_path" ]]; then
        socket_present=true

        socket_permissions="$(
            stat -c '%a' "$socket_path" 2>/dev/null
        )" || socket_permissions=""

        socket_owner="$(
            stat -c '%U' "$socket_path" 2>/dev/null
        )" || socket_owner=""

        socket_group="$(
            stat -c '%G' "$socket_path" 2>/dev/null
        )" || socket_group=""
    fi

    # Observe the listening Unix socket table, without connecting.
    if command -v ss >/dev/null 2>&1; then
        local listeners=""

        if listeners="$(ss -xlH 2>/dev/null)"; then
            listener_state="absent"

            if printf '%s\n' "$listeners" |
                awk '$1 == "u_str" && $2 == "LISTEN" &&
                     $5 == "/run/docker.sock" {found=1}
                     END {exit !found}'; then
                listener_state="present"
            fi
        fi
    fi

    # Passive classification: no API health claim.
    if [[ "$service_state" == "inactive" ]]; then
        preflight_status="SERVICE_INACTIVE"
        reason="DOCKER_SERVICE_INACTIVE"

    elif [[ "$service_state" == "failed" ]]; then
        preflight_status="SERVICE_FAILED"
        reason="DOCKER_SERVICE_FAILED"

    elif [[ "$service_state" == "active" ]] &&
         [[ "$process_verified" == true ]] &&
         [[ "$socket_present" == true ]] &&
         [[ "$listener_state" == "present" ]]; then

        preflight_status="PASSIVE_CANDIDATE"
        reason="DAEMON_PROCESS_AND_SOCKET_OBSERVED"
    fi

    jq -cn \
        --arg service_state "$service_state" \
        --arg socket_state "$socket_state" \
        --argjson main_pid "$main_pid" \
        --argjson process_verified "$process_verified" \
        --arg socket_path "$socket_path" \
        --argjson socket_present "$socket_present" \
        --arg socket_permissions "$socket_permissions" \
        --arg socket_owner "$socket_owner" \
        --arg socket_group "$socket_group" \
        --arg listener_state "$listener_state" \
        --arg preflight_status "$preflight_status" \
        --arg reason "$reason" \
        '{
            schema_version: "1.0",
            check: "docker_daemon_preflight",
            service: {
                state: $service_state,
                main_pid: $main_pid,
                process_verified: $process_verified
            },
            socket: {
                unit_state: $socket_state,
                path: $socket_path,
                present: $socket_present,
                permissions: (
                    if $socket_permissions == ""
                    then null else $socket_permissions end
                ),
                owner: (
                    if $socket_owner == ""
                    then null else $socket_owner end
                ),
                group: (
                    if $socket_group == ""
                    then null else $socket_group end
                ),
                listener_state: $listener_state
            },
            status: $preflight_status,
            reasons: [$reason],
            probe_attempted: false,
            daemon_responsive: null,
            read_only: true,
            execution_allowed: false
        }'
)
