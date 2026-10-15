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

SSH_KEY="$HOME/.ssh/id_ed25519"
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
        remote_script="$SYSTEM_SCRIPT"
        ;;

    discovery.package_manager)
        remote_command='bash -s'
        ;;

    discovery.runtimes)
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
      "$operation" == "discovery.runtimes" ]]; then

    case "$operation" in
        discovery.package_manager)
            module="$PACKAGE_MANAGER_MODULE"
            runner="$PACKAGE_MANAGER_RUNNER"
            ;;

        discovery.runtimes)
            module="$RUNTIMES_MODULE"
            runner="$RUNTIMES_RUNNER"
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
esac

# ---------------------------------------------------------
# 8. Return validated discovery data
# ---------------------------------------------------------

printf '%s\n' "$output"
