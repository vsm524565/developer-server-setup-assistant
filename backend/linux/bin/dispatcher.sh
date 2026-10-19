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
    or .operation == "discovery.package_manager"
    or .operation == "discovery.runtimes"
    or .operation == "discovery.web_servers")
    and (.target_id | type == "string"
     and test("^[a-zA-Z0-9_-]{1,64}$"))
    and .parameters == {}
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
if discovery_data="$("$SSH_SCRIPT" "$target_id" "$operation")"; then
    discovery_status="success"
    discovery_exit_code=0
else
    discovery_exit_code=$?
    discovery_status="error"
fi

# Validate the discovery response before reporting success.
if [[ "$discovery_status" == "success" ]]; then

    case "$operation" in
        discovery.system)
            validation_filter='
                type == "object"
                and ((keys | sort) == ([
                    "hostname", "os_id", "os_version",
                    "architecture", "cpu_logical_cores",
                    "cpu_model", "memory_total_bytes",
                    "memory_available_bytes", "disk_total_bytes",
                    "disk_used_bytes", "disk_available_bytes",
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


        *)
            discovery_status="error"
            discovery_exit_code=5
            ;;
    esac

    if [[ "$discovery_status" == "success" ]]; then
        if ! jq -e "$validation_filter" \
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