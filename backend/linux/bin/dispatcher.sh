#!/usr/bin/env bash

set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT_SCRIPT="$BASE_DIR/backend/linux/logging/audit.sh"
INVENTORY_SCRIPT="$BASE_DIR/backend/linux/connection/inventory.sh"
SSH_SCRIPT="$BASE_DIR/backend/linux/connection/ssh.sh"

command -v jq >/dev/null || {
    echo "ERROR: jq is required" >&2
    exit 1
}

# Maximum request size: 64 KiB.
MAX_REQUEST_BYTES=65536

request_file="$(mktemp)"
trap 'rm -f -- "$request_file"' EXIT

head -c "$((MAX_REQUEST_BYTES + 1))" > "$request_file"

request_size="$(wc -c < "$request_file")"

if (( request_size > MAX_REQUEST_BYTES )); then

    rejection_event="$(
        jq -n '{
            schema_version: "1.0",
            request_id: "unvalidated",
            source: "linux-backend",
            event: "request.rejected",
            target_id: "unvalidated",
            operation: "unvalidated",
            status: "rejected"
        }'
    )"

    if ! printf '%s\n' "$rejection_event" | "$AUDIT_SCRIPT"; then
        echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"AUDIT_FAILURE","message":"Audit recording failed"}]}'
        exit 3
    fi

    echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"REQUEST_TOO_LARGE","message":"Request exceeds 64 KiB"}]}'
    exit 2
fi

request="$(cat "$request_file")"

# Strictly validate the supported request contract.
if ! jq -e '
    type == "object"
    and .schema_version == "1.0"
    and (.request_id | type == "string" and length > 0)
    and (.operation == "discovery.system"
    or .operation == "discovery.bootstrap"
    or .operation == "discovery.common_health"
    or .operation == "discovery.package_manager"
    or .operation == "discovery.runtimes"
    or .operation == "discovery.web_servers"
    or .operation == "discovery.ports"
    or .operation == "discovery.panels"
    or .operation == "discovery.application_state"
    or .operation == "discovery.application_inventory"
    or .operation == "discovery.application_runtime"
    or .operation == "discovery.docker_daemon_preflight"
    or .operation == "discovery.swap_filesystem")
    and (.target_id | type == "string"
     and test("^[a-zA-Z0-9_-]{1,64}$"))
    and (
        if (
            .operation == "discovery.application_state"
            or .operation == "discovery.application_inventory"
        ) then
            (.parameters | type == "object")
            and ((.parameters | keys) == ["application_id"])
            and (
                .parameters.application_id |
                IN("git", "nginx", "docker", "docker_compose")
            )
        elif .operation == "discovery.application_runtime" then
            (.parameters | type == "object")
            and ((.parameters | keys) == ["application_id"])
            and (
                .parameters.application_id |
                IN("docker", "docker_compose")
            )
        elif .operation == "discovery.swap_filesystem" then
            (.parameters | type == "object")
            and ((.parameters | keys) == ["target_path"])
            and (.parameters.target_path |
                type == "string"
                and length > 1
                and length <= 4096
                and startswith("/")
                and (contains("\n") | not)
                and (contains("\r") | not))
        else
            .parameters == {}
        end
    )
    and ((keys | sort) ==
      (["schema_version","request_id","operation","target_id","parameters"] | sort))
' <<< "$request" >/dev/null 2>&1; then
    # Do not log the untrusted request body.
    # Use fixed metadata for invalid requests.
    rejection_event="$(
        jq -n '{
            schema_version: "1.0",
            request_id: "unvalidated",
            source: "linux-backend",
            event: "request.rejected",
            target_id: "unvalidated",
            operation: "unvalidated",
            status: "rejected"
        }'
    )"

    if ! printf '%s\n' "$rejection_event" | "$AUDIT_SCRIPT"; then
        echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"AUDIT_FAILURE","message":"Audit recording failed"}]}'
        exit 3
    fi

    echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"INVALID_REQUEST","message":"Unsupported or invalid request"}]}'
    exit 2
fi

request_id="$(jq -r '.request_id' <<< "$request")"
operation="$(jq -r '.operation' <<< "$request")"
target_id="$(jq -r '.target_id' <<< "$request")"
# Resolve target using the backend-owned inventory.
# Never accept host or SSH settings from request parameters.
if ! "$INVENTORY_SCRIPT" "$target_id" >/dev/null 2>&1; then

    rejection_event="$(
        jq -n '{
            schema_version: "1.0",
            request_id: "unvalidated",
            source: "linux-backend",
            event: "request.rejected",
            target_id: "unvalidated",
            operation: "unvalidated",
            status: "rejected"
        }'
    )"

    if ! printf '%s\n' "$rejection_event" | "$AUDIT_SCRIPT"; then
        echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"AUDIT_FAILURE","message":"Audit recording failed"}]}'
        exit 3
    fi

    echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"INVALID_TARGET","message":"Target is unavailable or not authorized"}]}'
    exit 2
fi

# Fail closed if audit recording fails.
audit_event="$(
    jq -n \
      --arg request_id "$request_id" \
      --arg operation "$operation" \
      --arg target_id "$target_id" \
      '{
        schema_version:"1.0",
        request_id:$request_id,
        source:"linux-backend",
        event:"discovery.requested",
        target_id:$target_id,
        operation:$operation,
        status:"accepted"
      }'
)"

if ! printf '%s\n' "$audit_event" | "$AUDIT_SCRIPT"; then
    echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"AUDIT_FAILURE","message":"Audit recording failed"}]}'
    exit 3
fi

# Execute approved discovery through the Bash SSH backend.
# Keep every argument separate; never evaluate request content.
ssh_arguments=("$target_id" "$operation")

if [[ "$operation" == "discovery.swap_filesystem" ]]; then
    target_path="$(jq -r '.parameters.target_path' <<< "$request")"
    ssh_arguments+=("$target_path")

elif [[ "$operation" == "discovery.application_state" ||
        "$operation" == "discovery.application_inventory" ||
        "$operation" == "discovery.application_runtime" ]]; then
    application_id="$(
        jq -er '.parameters.application_id' <<< "$request"
    )"
    ssh_arguments+=("$application_id")
fi

if discovery_data="$("$SSH_SCRIPT" "${ssh_arguments[@]}")"; then
    discovery_status="success"
    discovery_exit_code=0
else
    discovery_exit_code=$?
    discovery_status="error"
fi

# Validate the discovery response before reporting success.
if [[ "$discovery_status" == "success" ]]; then

    case "$operation" in

        discovery.docker_daemon_preflight)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "schema_version", "check", "service",
                    "socket", "status", "reasons",
                    "probe_attempted", "daemon_responsive",
                    "read_only", "execution_allowed"
                ] | sort))
                and .schema_version == "1.0"
                and .check == "docker_daemon_preflight"
                and .probe_attempted == false
                and .daemon_responsive == null
                and .read_only == true
                and .execution_allowed == false

                and (.service | type == "object")
                and ((.service | keys | sort) == ([
                    "state", "main_pid", "process_verified"
                ] | sort))
                and (.service.state | IN(
                    "active", "inactive", "failed",
                    "activating", "deactivating", "unknown"
                ))
                and (.service.main_pid |
                    type == "number"
                    and floor == .
                    and . >= 0)
                and (.service.process_verified | type == "boolean")
                and (
                    if .service.process_verified then
                        .service.main_pid > 0
                    else true end
                )

                and (.socket | type == "object")
                and ((.socket | keys | sort) == ([
                    "unit_state", "path", "present",
                    "permissions", "owner", "group",
                    "listener_state"
                ] | sort))
                and (.socket.unit_state | IN(
                    "active", "inactive", "failed",
                    "activating", "deactivating", "unknown"
                ))
                and .socket.path == "/run/docker.sock"
                and (.socket.present | type == "boolean")
                and (.socket.listener_state |
                    IN("present", "absent", "unknown"))
                and (
                    if .socket.present then
                        (.socket.permissions == null or
                         (.socket.permissions |
                          type == "string"
                          and test("^[0-7]{3,4}$")))
                        and (.socket.owner == null or
                             (.socket.owner |
                              type == "string" and length > 0))
                        and (.socket.group == null or
                             (.socket.group |
                              type == "string" and length > 0))
                    else
                        .socket.permissions == null
                        and .socket.owner == null
                        and .socket.group == null
                    end
                )

                and (.status | IN(
                    "PASSIVE_CANDIDATE",
                    "SERVICE_INACTIVE",
                    "SERVICE_FAILED",
                    "UNVERIFIED"
                ))
                and (.reasons | type == "array" and length == 1)
                and (
                    .reasons[0] == (
                        if .status == "PASSIVE_CANDIDATE" then
                            "DAEMON_PROCESS_AND_SOCKET_OBSERVED"
                        elif .status == "SERVICE_INACTIVE" then
                            "DOCKER_SERVICE_INACTIVE"
                        elif .status == "SERVICE_FAILED" then
                            "DOCKER_SERVICE_FAILED"
                        else
                            "INSUFFICIENT_PASSIVE_EVIDENCE"
                        end
                    )
                )
                and (
                    if .status == "PASSIVE_CANDIDATE" then
                        .service.state == "active"
                        and .service.process_verified == true
                        and .socket.present == true
                        and .socket.listener_state == "present"
                    elif .status == "SERVICE_INACTIVE" then
                        .service.state == "inactive"
                    elif .status == "SERVICE_FAILED" then
                        .service.state == "failed"
                    else true end
                )
            '
            ;;

        discovery.application_runtime)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "schema_version", "application_id",
                    "cli", "service", "socket", "plugin",
                    "daemon_responsive", "read_only",
                    "execution_allowed"
                ] | sort))
                and .schema_version == "1.0"
                and .application_id == $requested_application
                and .read_only == true
                and .execution_allowed == false
                and .daemon_responsive == null

                and (.cli | type == "object")
                and ((.cli | keys | sort) ==
                    (["path", "status", "version_output"] | sort))
                and (.cli.status |
                    IN("available", "absent", "unknown"))
                and (
                    if .cli.path == null then
                        .cli.status == "absent"
                        and .cli.version_output == null
                    else
                        (.cli.path |
                            type == "string" and startswith("/"))
                        and (
                            if .cli.status == "available" then
                                (.cli.version_output |
                                    type == "string"
                                    and length > 0
                                    and length <= 256)
                            else
                                .cli.version_output == null
                            end
                        )
                    end
                )

                and (.service | type == "object")
                and ((.service | keys) == ["state"])
                and (.socket | type == "object")
                and ((.socket | keys) == ["state"])
                and (.plugin | type == "object")
                and ((.plugin | keys) == ["state"])
                and (.service.state | IN(
                    "active", "inactive", "failed",
                    "activating", "deactivating",
                    "unknown", "not_applicable"
                ))
                and (.socket.state | IN(
                    "active", "inactive", "failed",
                    "activating", "deactivating",
                    "unknown", "not_applicable"
                ))
                and (.plugin.state |
                    IN("available", "absent", "unknown", "not_applicable"))

                and (
                    if $requested_application == "docker" then
                        .plugin.state == "not_applicable"
                        and .service.state != "not_applicable"
                        and .socket.state != "not_applicable"
                    else
                        .service.state == "not_applicable"
                        and .socket.state == "not_applicable"
                        and (
                            if .plugin.state == "available" then
                                .cli.status == "available"
                            elif .plugin.state == "absent" then
                                .cli.status == "unknown"
                                and (.cli.path | type == "string")
                            else
                                .cli.status != "available"
                            end
                        )
                    end
                )
            '
            ;;

        discovery.application_inventory)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "schema_version", "application_id",
                    "package_manager", "packages", "binary",
                    "read_only", "execution_allowed"
                ] | sort))
                and .schema_version == "1.0"
                and .application_id == $requested_application
                and (.package_manager | IN("apt", "dnf"))
                and .read_only == true
                and .execution_allowed == false

                and (.packages | type == "array")
                and (
                    [.packages[].name] ==
                    (
                        if $requested_application == "git" then
                            ["git"]
                        elif $requested_application == "nginx" then
                            ["nginx"]
                        elif $requested_application == "docker" then
                            [
                                "docker-ce", "docker-ce-cli",
                                "docker.io", "moby-engine", "moby-cli"
                            ]
                        else
                            [
                                "docker-compose-plugin",
                                "docker-compose-v2",
                                "docker-compose"
                            ]
                        end
                    )
                )
                and (
                    .packages |
                    all(.[];
                        type == "object"
                        and ((keys | sort) ==
                             (["name", "state", "version"] | sort))
                        and (.name | type == "string" and length > 0)
                        and (.state |
                             IN("installed", "absent", "unknown"))
                        and (
                            if .state == "installed" then
                                (.version == null or
                                 (.version | type == "string"
                                  and length > 0))
                            else
                                .version == null
                            end
                        )
                    )
                )

                and (.binary | type == "object")
                and ((.binary | keys | sort) == (
                    ["name", "path", "present", "ownership"] | sort
                ))
                and .binary.name == (
                    if $requested_application == "git" then "git"
                    elif $requested_application == "nginx" then "nginx"
                    else "docker"
                    end
                )
                and (.binary.present | type == "boolean")
                and (
                    if .binary.present then
                        (.binary.path |
                         type == "string" and startswith("/"))
                    else
                        .binary.path == null
                    end
                )

                and (.binary.ownership | type == "object")
                and ((.binary.ownership | keys | sort) ==
                     (["status", "raw"] | sort))
                and (.binary.ownership.status |
                     IN("reported", "unknown"))
                and (
                    if .binary.ownership.status == "reported" then
                        .binary.present == true
                        and (.binary.ownership.raw |
                             type == "string" and length > 0)
                    else
                        .binary.ownership.raw == null
                    end
                )
            '
            ;;

        discovery.application_state)
            validation_filter='
                type == "object"
                and .schema_version == "1.0"
                and .application_id == $requested_application
                and .read_only == true
                and .execution_allowed == false
                and (.package_manager | IN("apt", "dnf"))
                and (.package | type == "object")
                and (.package.name | type == "string" and length > 0)
                and (.package.state |
                     IN("installed", "absent", "unknown"))
                and (.binary | type == "object")
                and (.binary.present | type == "boolean")
                and (
                    if .binary.present then
                        (.binary.path |
                         type == "string" and startswith("/"))
                    else
                        .binary.path == null
                    end
                )
                and (.service.state |
                     IN("active", "inactive", "failed",
                        "activating", "deactivating",
                        "unknown", "not_applicable"))
                and (.plugin.state |
                     IN("available", "unknown", "not_applicable"))
                and ((keys | sort) == ([
                    "schema_version", "application_id",
                    "package_manager", "package", "binary",
                    "service", "plugin", "read_only",
                    "execution_allowed"
                ] | sort))
                and ((.package | keys | sort) ==
                     ["name", "state"])
                and ((.binary | keys | sort) ==
                     ["path", "present"])
                and ((.service | keys) == ["state"])
                and ((.plugin | keys) == ["state"])
                and (
                    if .application_id == "git" then
                        .package.name == "git"
                    elif .application_id == "nginx" then
                        .package.name == "nginx"
                    elif .application_id == "docker" then
                        .package.name == "docker-ce"
                    else
                        .package.name == "docker-compose-plugin"
                    end
                )
            '
            ;;
        discovery.common_health)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "schema_version", "check", "identity",
                    "privileges", "time_health", "execution_allowed"
                ] | sort))
                and .schema_version == "1.0"
                and .check == "common_health"
                and .execution_allowed == false

                and (.identity | type == "object")
                and ((.identity | keys | sort) == ([
                    "schema_version", "check", "uid", "gid",
                    "username", "is_root", "execution_allowed"
                ] | sort))
                and .identity.schema_version == "1.0"
                and .identity.check == "identity"
                and (.identity.uid | type == "number" and floor == . and . >= 0)
                and (.identity.gid | type == "number" and floor == . and . >= 0)
                and (.identity.username | type == "string" and length > 0)
                and (.identity.is_root == (.identity.uid == 0))
                and .identity.execution_allowed == false

                and (.privileges | type == "object")
                and ((.privileges | keys | sort) == ([
                    "schema_version", "check", "uid",
                    "sudo_available", "status", "reason", "execution_allowed"
                ] | sort))
                and .privileges.schema_version == "1.0"
                and .privileges.check == "privileges"
                and .privileges.uid == .identity.uid
                and (.privileges.sudo_available | type == "boolean")
                and (.privileges.status | IN(
                    "ROOT", "POLICY_LIST_AVAILABLE",
                    "PASSWORD_REQUIRED", "DENIED",
                    "UNKNOWN", "UNAVAILABLE"
                ))
                and (.privileges.reason | type == "string")
                and .privileges.execution_allowed == false

                and (.time_health | type == "object")
                and ((.time_health | keys | sort) == ([
                    "schema_version", "check", "synchronization",
                    "ntp_enabled", "timedatectl_available",
                    "chrony_available", "execution_allowed"
                ] | sort))
                and .time_health.schema_version == "1.0"
                and .time_health.check == "time_health"
                and (.time_health.synchronization | IN(
                    "SYNCHRONIZED", "UNSYNCHRONIZED", "UNKNOWN"
                ))
                and (.time_health.ntp_enabled | IN(
                    "ENABLED", "DISABLED", "UNKNOWN"
                ))
                and (.time_health.timedatectl_available | type == "boolean")
                and (.time_health.chrony_available | type == "boolean")
                and .time_health.execution_allowed == false
            '
            ;;

        discovery.bootstrap)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "schema_version", "check", "capabilities",
                    "summary", "execution_allowed"
                ] | sort))
                and .schema_version == "1.0"
                and .check == "bootstrap_capabilities"
                and .execution_allowed == false
                and (.capabilities | type == "array")
                and (.capabilities | length == 17)
                and ([
                    .capabilities[] |
                    type == "object"
                    and ((keys | sort) == (["name", "status"] | sort))
                    and (.name | type == "string")
                    and (.status == "AVAILABLE" or .status == "MISSING")
                ] | all)
                and ([.capabilities[].name] | unique | length) == 17
                and ([.capabilities[].name] | sort) == ([
                    "bash", "jq", "awk", "sed", "grep", "stat",
                    "date", "df", "findmnt", "lsblk", "mktemp",
                    "base64", "systemctl", "sudo", "ip", "ss", "getent"
                ] | sort)
                and (.summary | type == "object")
                and ((.summary | keys | sort) ==
                    (["total", "available", "missing"] | sort))
                and .summary.total == 17
                and (.summary.available | type == "number"
                     and floor == . and . >= 0)
                and (.summary.missing | type == "number"
                     and floor == . and . >= 0)
                and (.summary.available + .summary.missing == 17)
                and (.summary.available ==
                    ([.capabilities[] | select(.status == "AVAILABLE")] | length))
                and (.summary.missing ==
                    ([.capabilities[] | select(.status == "MISSING")] | length))
            '
            ;;

        discovery.swap_filesystem)
            validation_filter='
                type == "object"
                and ((keys | sort) ==
                    (["schema_version", "target_path", "filesystem"] | sort))
                and .schema_version == "1.0"
                and .target_path == $requested_path
                and (.filesystem | type == "object")
                and ((.filesystem | keys | sort) ==
                    (["mountpoint", "source", "type", "available_bytes"] | sort))
                and (.filesystem.mountpoint |
                    type == "string" and startswith("/"))
                and (.filesystem.source |
                    type == "string" and length > 0)
                and (.filesystem.type |
                    type == "string" and length > 0)
                and (.filesystem.available_bytes |
                    type == "number" and floor == . and . >= 0)
            '
            ;;
        discovery.system)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "hostname", "os_id", "os_version",
                    "architecture", "cpu_logical_cores",
                    "cpu_model", "memory_total_bytes",
                    "memory_available_bytes",
                    "swap_total_bytes", "swap_used_bytes",
                    "swap_devices","swap_persistence",
                    "disk_total_bytes", "disk_used_bytes",
                    "disk_available_bytes",
                    "uptime_seconds", "virtualization"
                ] | sort))

                and (.hostname | type == "string" and length > 0)
                and (.os_id | type == "string" and length > 0)
                and (.os_version | type == "string" and length > 0)
                and (.architecture | type == "string" and length > 0)
                and (.cpu_logical_cores | type == "number" and . >= 1)
                and (.cpu_model | type == "string" and length > 0)

                and (.memory_total_bytes | type == "number" and . > 0)
                and (.memory_available_bytes | type == "number" and . >= 0)
                and (.memory_available_bytes <= .memory_total_bytes)

                and (.swap_total_bytes | type == "number" and . >= 0)
                and (.swap_used_bytes | type == "number" and . >= 0)
                and (.swap_used_bytes <= .swap_total_bytes)

                and (.swap_devices | type == "array")
                and (
                    .swap_devices |
                    all(.[];
                        type == "object"
                        and ((keys | sort) == (
                            ["name", "type", "size_bytes",
                            "used_bytes", "priority", "identity"] | sort
                        ))
                        and (.name | type == "string" and length > 0)
                        and (.type == "file" or .type == "partition")
                        and (.size_bytes | type == "number" and . > 0)
                        and (.used_bytes | type == "number" and . >= 0)
                        and (.used_bytes <= .size_bytes)
                        and (.priority | type == "number" and floor == .)
                        and (.identity | type == "object")
and ((.identity | keys | sort) ==
     (["canonical_path", "uuid"] | sort))
and (
    .identity.canonical_path == null
    or (.identity.canonical_path | type == "string" and length > 0)
)
and (
    .identity.uuid == null
    or (.identity.uuid | type == "string" and length > 0)
)
                    )
                )
                and (.swap_persistence | type == "object")
                and (
                    (.swap_persistence | keys | sort) ==
                    (["readable", "entries"] | sort)
                )
                and (.swap_persistence.readable | type == "boolean")
                and (.swap_persistence.entries | type == "array")
                and (
                    .swap_persistence.entries |
                    all(.[];
                        type == "object"
                        and ((keys | sort) == (
                        ["source", "mountpoint", "filesystem_type", "options", "resolved_path"] | sort
                        ))
                        and (.source | type == "string" and length > 0)
                        and (.mountpoint | type == "string" and length > 0)
                        and (.filesystem_type == "swap")
                        and (.options | type == "string" and length > 0)
                        and (
    .resolved_path == null
    or (.resolved_path | type == "string" and length > 0)
)
                    )
                )
                and (.disk_total_bytes | type == "number" and . > 0)
                and (.disk_used_bytes | type == "number" and . >= 0)
                and (.disk_available_bytes | type == "number" and . >= 0)
                and (.disk_used_bytes <= .disk_total_bytes)
                and (.disk_available_bytes <= .disk_total_bytes)

                and (.uptime_seconds | type == "number" and . >= 0)
                and (.virtualization | type == "string" and length > 0)
            '
            ;;

        discovery.runtimes)
            validation_filter='
                type == "object"
                and ((keys | sort) == ["runtimes"])
                and (.runtimes | type == "object")
                and (
                    (.runtimes | keys | sort) ==
                    (["python3", "php", "node", "java"] | sort)
                )
                and (
                    [.runtimes[] |
                        (
                            type == "object"
                            and ((keys | sort) == ([
                                "available",
                                "path",
                                "version"
                            ] | sort))
                            and (.available | type == "boolean")
                            and (
                                if .available then
                                    (.path | type == "string" and length > 0)
                                else
                                    .path == null and .version == null
                                end
                            )
                            and (
                                .version == null or
                                (.version | type == "string" and length > 0)
                            )
                        )
                    ] | all
                )
            '
            ;;
        discovery.package_manager)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "available_package_tools",
                    "package_manager_detected"
                ] | sort))
                and (.available_package_tools | type == "array")
                and (.available_package_tools |
                     all(.[]; type == "string" and length > 0))
                and (.package_manager_detected | type == "boolean")
                and (.package_manager_detected ==
                     (.available_package_tools | length > 0))
            '
            ;;

        discovery.web_servers)
            validation_filter='
                type == "object"
                and ((keys | sort) == ["web_servers"])
                and (.web_servers | type == "object")
                and (
                    (.web_servers | keys | sort) ==
                    (["nginx", "apache", "openlitespeed", "litespeed"] | sort)
                )
                and (
                    [.web_servers[] |
                        (
                            type == "object"
                            and ((keys | sort) == ([
                                "available",
                                "path",
                                "version",
                                "service_name",
                                "service_state",
                                "detection_status"
                            ] | sort))
                            and (.available | type == "boolean")
                            and (
                                if .available then
                                    (.path | type == "string" and length > 0)
                                    and .detection_status == "detected"
                                else
                                    .path == null
                                    and .version == null
                                    and .detection_status != "detected"
                                end
                            )
                            and (
                                .version == null or
                                (.version | type == "string" and length > 0)
                            )
                            and (
                                .service_name == null or
                                (.service_name | type == "string" and length > 0)
                            )
                            and (
                                .service_state |
                                IN("active", "inactive", "failed", "unknown")
                            )
                            and (
                                .detection_status |
                                IN("detected", "absent", "unidentified")
                            )
                        )
                    ] | all
                )
            '
            ;;

        discovery.ports)
            validation_filter='
                type == "object"
                and ((keys | sort) == ["listening_tcp_ports"])
                and (.listening_tcp_ports | type == "array")
                and (
                    .listening_tcp_ports |
                    all(.[];
                        type == "number"
                        and floor == .
                        and . >= 1
                        and . <= 65535
                    )
                )
                and (
                    .listening_tcp_ports ==
                    (.listening_tcp_ports | unique | sort)
                )
            '
            ;;

        discovery.panels)
            validation_filter='
                type == "object"
                and ((keys | sort) == ["panels"])
                and (.panels | type == "array")
                and (
                    .panels |
                    all(.[];
                        IN(
                            "aapanel",
                            "cyberpanel",
                            "hestia",
                            "cpanel",
                            "directadmin",
                            "plesk"
                        )
                    )
                )
                and (
                    .panels ==
                    (.panels | unique | sort)
                )
            '
            ;;
        *)
            discovery_status="error"
            discovery_exit_code=5
            ;;
    esac

    if [[ "$discovery_status" == "success" ]]; then
        if ! jq -e \
            --arg requested_path "${target_path:-}" \
            --arg requested_application "${application_id:-}" \
            "$validation_filter" \
            <<< "$discovery_data" >/dev/null 2>&1; then
            discovery_status="error"
            discovery_exit_code=5
        fi
    fi
fi

# Record the execution outcome before returning data.
outcome_event="$(
    jq -n \
      --arg request_id "$request_id" \
      --arg operation "$operation" \
      --arg target_id "$target_id" \
      --arg status "$discovery_status" \
      --argjson exit_code "$discovery_exit_code" \
      '{
        schema_version:"1.0",
        request_id:$request_id,
        source:"linux-backend",
        event:"discovery.completed",
        target_id:$target_id,
        operation:$operation,
        status:$status,
        exit_code:$exit_code
      }'
)"

if ! printf '%s\n' "$outcome_event" | "$AUDIT_SCRIPT"; then
    echo '{"schema_version":"1.0","status":"error","data":null,"errors":[{"code":"AUDIT_FAILURE","message":"Audit recording failed"}]}'
    exit 3
fi

if [[ "$discovery_status" == "success" ]]; then
    jq -n \
      --arg request_id "$request_id" \
      --arg operation "$operation" \
      --argjson data "$discovery_data" \
      '{
        schema_version:"1.0",
        request_id:$request_id,
        operation:$operation,
        status:"success",
        data:$data,
        errors:[]
      }'
else
    jq -n \
      --arg request_id "$request_id" \
      --arg operation "$operation" \
      '{
        schema_version:"1.0",
        request_id:$request_id,
        operation:$operation,
        status:"error",
        data:null,
        errors:[{
          code:"DISCOVERY_FAILED",
          message:"Remote system discovery failed"
        }]
      }'

    exit 4
fi