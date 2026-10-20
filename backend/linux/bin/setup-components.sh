#!/usr/bin/env bash
set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
INSTALLER="$BASE_DIR/backend/linux/modules/install/nginx.sh"
PACKAGE_MODULE="$BASE_DIR/backend/linux/modules/software/package_manager.sh"
PACKAGE_ACTIONS="$BASE_DIR/backend/linux/modules/install/package_manager.sh"
INVENTORY="$BASE_DIR/backend/linux/connection/inventory.sh"
SSH_KEY="${DSSA_SSH_KEY:-$HOME/.ssh/id_ed25519}"
KNOWN_HOSTS="$HOME/.ssh/known_hosts"
NGINX_VERIFICATION="$BASE_DIR/backend/linux/verification/nginx.sh"
SETUP_MODULE="$BASE_DIR/backend/linux/operations/setup.sh"
GIT_INSTALLER="$BASE_DIR/backend/linux/modules/install/git.sh"
GIT_VERIFICATION="$BASE_DIR/backend/linux/verification/git.sh"
PANEL_DISCOVERY="$BASE_DIR/backend/linux/discovery/panel.sh"
PORT_DISCOVERY="$BASE_DIR/backend/linux/discovery/ports.sh"
PLANNING_MODULE="$BASE_DIR/backend/linux/planning/components.sh"

AUDIT_SCRIPT="$BASE_DIR/backend/linux/logging/audit.sh"
request_id="install-$(cat /proc/sys/kernel/random/uuid)"

# Record fixed operational metadata; never include credentials.
record_install_event() {
    local event="$1"
    local status="$2"
    local exit_code="$3"

    jq -n \
        --arg request_id "$request_id" \
        --arg target "$target" \
        --arg event "$event" \
        --arg status "$status" \
        --arg digest "$digest" \
        --arg operation "$operation" \
        --argjson exit_code "$exit_code" \
        --argjson components "$components_json" \
        '{
            schema_version: "1.0",
            request_id: $request_id,
            source: "linux-backend",
            event: $event,
            operation: $operation,
            target_id: $target,
            status: $status,
            exit_code: $exit_code,
            details: {
                installer_sha256: $digest,
                components: $components
            }
        }' | "$AUDIT_SCRIPT"
}
mode="apply"
operation="setup.components"

if [[ "${1:-}" == "--plan" ]]; then
    mode="plan"
    operation="setup.plan"
    shift
fi

[[ $# -ge 2 ]] || {
    echo "Usage: setup-components.sh [--plan] TARGET_ID COMPONENT..." >&2
    exit 2
}

target="$1"
shift
components=("$@")

# Validate every selection before resolving the target or connecting.
for component in "${components[@]}"; do
    case "$component" in
        nginx|git) ;;
        *)
            printf 'ERROR: Unsupported component: %s\n' "$component" >&2
            exit 2
            ;;
    esac
done

components_json="$(
    printf '%s\n' "${components[@]}" |
        jq -R -s 'split("\n") | map(select(length > 0))'
)"

if ! target_json="$("$INVENTORY" "$target" 2>/dev/null)"; then
    printf 'ERROR: Target "%s" could not be resolved from the selected inventory.\n' \
        "$target" >&2
    echo "Check the inventory path, permissions and target enabled status." >&2
    exit 3
fi

host="$(jq -er '.host' <<< "$target_json")"
port="$(jq -er '.ssh_port' <<< "$target_json")"
user="$(jq -er '.ssh_user' <<< "$target_json")"

[[ "$host" =~ ^[a-zA-Z0-9.-]+$ ]] &&
[[ "$user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] &&
[[ "$port" =~ ^[0-9]+$ ]] &&
(( port >= 1 && port <= 65535 )) || {
    echo "ERROR: Invalid inventory fields." >&2
    exit 3
}

[[ -f "$SSH_KEY" && -f "$KNOWN_HOSTS" && -r "$INSTALLER" ]] || {
    echo "ERROR: SSH identity, known_hosts or installer missing." >&2
    exit 3
}

bash -n "$INSTALLER"

# Encode the fixed installer for safe transport as a command argument.
# No credentials are included in this payload.
# Include shared package functions before the installation steps.
bundle="$(
    printf '%s\n' 'set -euo pipefail'

    cat "$PACKAGE_MODULE"
    printf '\n'
    cat "$PACKAGE_ACTIONS"
    printf '\n'

    cat "$NGINX_VERIFICATION"
    printf '\n'
    cat "$INSTALLER"
    printf '\n'

    cat "$GIT_VERIFICATION"
    printf '\n'
    cat "$GIT_INSTALLER"
    printf '\n'

    cat "$PANEL_DISCOVERY"
    printf '\n'

    cat "$PORT_DISCOVERY"
    printf '\n'

    cat "$PLANNING_MODULE"
    printf '\n'

    cat "$SETUP_MODULE"
    printf '\n'

    # Execute only after all functions have been defined.
    printf '%s' 'setup_selected_services'

    if [[ "$mode" == "plan" ]]; then
        printf ' %q' '--plan'
    fi

    printf ' %q' "${components[@]}"
    printf '\n'

    # Preserve the coordinator outcome as the remote process exit status.
    printf '%s\n' 'exit "$?"'
)"
bash -n <<< "$bundle"
payload="$(printf '%s\n' "$bundle" | base64 | tr -d '\n')"
digest="$(printf '%s\n' "$bundle" | sha256sum | awk '{print $1}')"

if [[ "$mode" == "apply" ]]; then
    printf '\nTarget: %s (%s@%s:%s)\n' \
        "$target" "$user" "$host" "$port"
    printf 'Selected components: %s\n' "${components[*]}"
    printf 'Execution bundle SHA256: %s\n' "$digest"

    if [[ "$mode" == "plan" ]]; then
        printf 'Plan-only mode: no installation will be attempted.\n' >&2
    else
        printf 'The backend will display the plan before requesting approval.\n' >&2
    fi


else
    printf 'Plan-only mode: no installation will be attempted.\n' >&2
fi

if ! record_install_event "setup.started" "started" 0; then
    echo "ERROR: Audit recording failed. Installation was not started." >&2
    exit 3
fi

# sudo reads the password from the remote terminal.
# The encoded payload contains only the fixed installer source.
remote_command="sudo -- bash -c \"\$(printf '%s' '$payload' | base64 -d)\""

if ssh \
    -F /dev/null \
    -t \
    -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" \
    -o GlobalKnownHostsFile=/dev/null \
    -o IdentitiesOnly=yes \
    -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no \
    -o ConnectTimeout=10 \
    -o ForwardAgent=no \
    -o ClearAllForwardings=yes \
    -o ControlMaster=no \
    -o ControlPath=none \
    -p "$port" \
    -l "$user" \
    -- "$host" "$remote_command"
then
    install_exit=0
    install_status="success"
else
    install_exit=$?

    # SSH exit 255 means remote completion cannot be established.
    if [[ "$install_exit" -eq 255 ]]; then
        install_status="unknown"
    else
        install_status="failed"
    fi
fi

if ! record_install_event \
    "setup.completed" "$install_status" "$install_exit"; then
    echo "ERROR: Outcome audit recording failed." >&2
    echo "The server may already have changed; inspect it before retrying." >&2
    exit 3
fi

exit "$install_exit"