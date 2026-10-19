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
        --argjson exit_code "$exit_code" \
        '{
            schema_version: "1.0",
            request_id: $request_id,
            source: "linux-backend",
            event: $event,
            operation: "install.nginx",
            target_id: $target,
            status: $status,
            exit_code: $exit_code,
            details: {
                installer_sha256: $digest
            }
        }' | "$AUDIT_SCRIPT"
}

[[ $# -eq 1 ]] || {
    echo "Usage: install-nginx-lab.sh TARGET_ID" >&2
    exit 2
}

target="$1"
target_json="$("$INVENTORY" "$target")"

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
    cat "$PACKAGE_MODULE"
    printf '\n'
    cat "$PACKAGE_ACTIONS"
    printf '\n'
    cat "$NGINX_VERIFICATION"
    printf '\n'
    cat "$INSTALLER"
    printf '\n'
    cat "$SETUP_MODULE"
    printf '\n'
    printf '%s\n' 'setup_selected_services nginx'
)"

bash -n <<< "$bundle"
payload="$(printf '%s\n' "$bundle" | base64 | tr -d '\n')"
digest="$(printf '%s\n' "$bundle" | sha256sum | awk '{print $1}')"

printf '\nTarget: %s (%s@%s:%s)\n' "$target" "$user" "$host" "$port"
printf 'Operation: install and start Nginx using apt or dnf\n'
printf 'Installer SHA256: %s\n' "$digest"
printf 'Type INSTALL %s to proceed: ' "$target" > /dev/tty
IFS= read -r confirmation < /dev/tty

if [[ "$confirmation" != "INSTALL $target" ]]; then
    if ! record_install_event "installation.cancelled" "cancelled" 2; then
        echo "ERROR: Could not record installation cancellation." >&2
        exit 3
    fi

    echo "Installation cancelled."
    exit 2
fi

if ! record_install_event "installation.started" "started" 0; then
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
    "installation.completed" "$install_status" "$install_exit"; then
    echo "ERROR: Outcome audit recording failed." >&2
    echo "The server may already have changed; inspect it before retrying." >&2
    exit 3
fi

exit "$install_exit"