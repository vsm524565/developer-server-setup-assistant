#!/usr/bin/env bash
set -euo pipefail
umask 077

# DSSA SSH transport.
# Executes approved read-only discovery operations against inventory targets.

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"

INVENTORY_SCRIPT="$BASE_DIR/backend/linux/connection/inventory.sh"

# System discovery.
SYSTEM_SCRIPT="$BASE_DIR/backend/linux/discovery/system.sh"

# Package manager discovery.
PACKAGE_MANAGER_MODULE="$BASE_DIR/backend/linux/modules/software/package_manager.sh"
PACKAGE_MANAGER_RUNNER="$BASE_DIR/backend/linux/discovery/package_manager.sh"

# Software runtime discovery.
RUNTIMES_MODULE="$BASE_DIR/backend/linux/modules/software/runtimes.sh"
RUNTIMES_RUNNER="$BASE_DIR/backend/linux/discovery/runtimes.sh"

# Web server discovery.
WEB_SERVERS_MODULE="$BASE_DIR/backend/linux/modules/software/web_servers.sh"
WEB_SERVERS_RUNNER="$BASE_DIR/backend/linux/discovery/web_servers.sh"

SWAP_IDENTITY_MODULE="$BASE_DIR/backend/linux/discovery/swap_identity.sh"

SSH_KEY="${DSSA_SSH_KEY:-$HOME/.ssh/id_ed25519}"
KNOWN_HOSTS="$HOME/.ssh/known_hosts"

# ---------------------------------------------------------
# 1. Validate arguments
# ---------------------------------------------------------

if [[ $# -ne 2 ]]; then
    echo "ERROR: Usage: ssh.sh TARGET_ID OPERATION" >&2
    exit 2
fi

target_id="$1"
operation="$2"

# ---------------------------------------------------------
# 2. Allow only approved discovery operations
# ---------------------------------------------------------

case "$operation" in
    discovery.system)
        remote_command='bash -s'

        for file in "$SWAP_IDENTITY_MODULE" "$SYSTEM_SCRIPT"; do
            if [[ ! -f "$file" || ! -r "$file" ]]; then
                echo "ERROR: System discovery component unavailable" >&2
                exit 3
            fi
        done

        remote_script="$(mktemp)"
        trap 'rm -f -- "$remote_script"' EXIT

        {
            cat "$SWAP_IDENTITY_MODULE"
            printf '\n'
            cat "$SYSTEM_SCRIPT"
            printf '\n'
        } > "$remote_script"

        if ! bash -n "$remote_script"; then
            echo "ERROR: Invalid system discovery bundle" >&2
            exit 3
        fi
        ;;

    discovery.package_manager)
        remote_command='bash -s'
        ;;

    discovery.runtimes)
        remote_command='bash -s'
        ;;

    discovery.web_servers)
        remote_command='bash -s'
        ;;

    *)
        echo "ERROR: Unsupported SSH operation" >&2
        exit 2
        ;;
esac

# ---------------------------------------------------------
# 3. Resolve target from backend-owned inventory
# ---------------------------------------------------------

if ! target_json="$("$INVENTORY_SCRIPT" "$target_id" 2>/dev/null)"; then
    echo "ERROR: Target unavailable or unauthorized" >&2
    exit 3
fi

host="$(jq -er '.host' <<< "$target_json")"
port="$(jq -er '.ssh_port' <<< "$target_json")"
user="$(jq -er '.ssh_user' <<< "$target_json")"

# ---------------------------------------------------------
# 4. Validate SSH identity and inventory fields
# ---------------------------------------------------------

if [[ ! -f "$SSH_KEY" || ! -f "$KNOWN_HOSTS" ]]; then
    echo "ERROR: SSH identity or known_hosts missing" >&2
    exit 3
fi

if [[ ! "$host" =~ ^[a-zA-Z0-9.-]+$ ]] ||
   [[ ! "$user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] ||
   [[ ! "$port" =~ ^[0-9]+$ ]] ||
   (( port < 1 || port > 65535 )); then

    echo "ERROR: Invalid SSH inventory fields" >&2
    exit 3
fi

# ---------------------------------------------------------
# 5. Build reusable discovery bundles
# ---------------------------------------------------------

if [[ "$operation" == "discovery.package_manager" ||
      "$operation" == "discovery.runtimes" ||
      "$operation" == "discovery.web_servers" ]]; then

    case "$operation" in
        discovery.package_manager)
            module="$PACKAGE_MANAGER_MODULE"
            runner="$PACKAGE_MANAGER_RUNNER"
            ;;

        discovery.runtimes)
            module="$RUNTIMES_MODULE"
            runner="$RUNTIMES_RUNNER"
            ;;

        discovery.web_servers)
            module="$WEB_SERVERS_MODULE"
            runner="$WEB_SERVERS_RUNNER"
            ;;
    esac

    # Both collector and runner must exist and be readable.
    for file in "$module" "$runner"; do
        if [[ ! -f "$file" || ! -r "$file" ]]; then
            echo "ERROR: Discovery component unavailable" >&2
            exit 3
        fi
    done

    # Create a temporary bundle with restrictive permissions.
    remote_script="$(mktemp)"
    trap 'rm -f -- "$remote_script"' EXIT

    {
        cat "$module"
        printf '\n'
        cat "$runner"
        printf '\n'
    } > "$remote_script"

    # Validate the complete script before remote execution.
    if ! bash -n "$remote_script"; then
        echo "ERROR: Invalid discovery bundle" >&2
        exit 3
    fi
fi

if [[ ! -f "$remote_script" || ! -r "$remote_script" ]]; then
    echo "ERROR: Discovery module unavailable" >&2
    exit 3
fi

# ---------------------------------------------------------
# 6. Execute discovery through secure agentless SSH
# ---------------------------------------------------------

if ! output="$(ssh \
    -F /dev/null \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" \
    -o GlobalKnownHostsFile=/dev/null \
    -o IdentitiesOnly=yes \
    -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no \
    -o PreferredAuthentications=publickey \
    -o ConnectTimeout=5 \
    -o ConnectionAttempts=1 \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=1 \
    -o LogLevel=ERROR \
    -o ProxyCommand=none \
    -o ControlMaster=no \
    -o ControlPath=none \
    -o PermitLocalCommand=no \
    -o ClearAllForwardings=yes \
    -T \
    -i "$SSH_KEY" \
    -p "$port" \
    -l "$user" \
    -- "$host" "$remote_command" < "$remote_script")"; then

    echo "ERROR: SSH discovery execution failed" >&2
    exit 4
fi

# ---------------------------------------------------------
# 7. Validate the remote JSON response
# ---------------------------------------------------------

case "$operation" in

    # -----------------------------------------------------
    # System discovery
    # -----------------------------------------------------

    discovery.system)
        if ! jq -e '
            type == "object"
            and ((keys | sort) == ([
                "hostname",
                "os_id",
                "os_version",
                "architecture",
                "cpu_logical_cores",
                "cpu_model",
                "memory_total_bytes",
                "memory_available_bytes",
                "swap_total_bytes",
                "swap_used_bytes",
                "swap_devices",
                "swap_persistence",
                "disk_total_bytes",
                "disk_used_bytes",
                "disk_available_bytes",
                "uptime_seconds",
                "virtualization"
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
                        ["name", "type", "size_bytes", "used_bytes", "priority", "identity"] | sort
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
            and (.disk_available_bytes <= .disk_total_bytes)
            and (.disk_used_bytes <= .disk_total_bytes)
            and (.uptime_seconds | type == "number" and . >= 0)
            and (.virtualization | type == "string" and length > 0)
        ' <<< "$output" >/dev/null 2>&1; then

            echo "ERROR: Invalid remote system discovery response" >&2
            exit 5
        fi
        ;;

    # -----------------------------------------------------
    # Package manager discovery
    # -----------------------------------------------------

    discovery.package_manager)
        if ! jq -e '
            type == "object"
            and ((keys | sort) == ([
                "available_package_tools",
                "package_manager_detected"
            ] | sort))
            and (.available_package_tools | type == "array")
            and (.available_package_tools |
                all(.[]; type == "string" and length > 0))
            and (.package_manager_detected | type == "boolean")
            and (
                .package_manager_detected ==
                (.available_package_tools | length > 0)
            )
        ' <<< "$output" >/dev/null 2>&1; then

            echo "ERROR: Invalid remote package manager discovery response" >&2
            exit 5
        fi
        ;;

    # -----------------------------------------------------
    # Runtime discovery
    # -----------------------------------------------------

    discovery.runtimes)
        if ! jq -e '
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
        ' <<< "$output" >/dev/null 2>&1; then

            echo "ERROR: Invalid remote runtime discovery response" >&2
            exit 5
        fi
        ;;
    # -----------------------------------------------------
    # Web server discovery
    # -----------------------------------------------------
    discovery.web_servers)
        if ! jq -e '
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
        ' <<< "$output" >/dev/null 2>&1; then

            echo "ERROR: Invalid remote web server discovery response" >&2
            exit 5
        fi
        ;;

esac

# ---------------------------------------------------------
# 8. Return validated discovery data
# ---------------------------------------------------------

printf '%s\n' "$output"
