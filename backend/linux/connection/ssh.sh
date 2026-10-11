#!/usr/bin/env bash
set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
INVENTORY_SCRIPT="$BASE_DIR/backend/linux/connection/inventory.sh"

SSH_KEY="$HOME/.ssh/id_ed25519"
KNOWN_HOSTS="$HOME/.ssh/known_hosts"

if [[ $# -ne 2 ]]; then
    echo "ERROR: Usage: ssh.sh TARGET_ID OPERATION" >&2
    exit 2
fi

target_id="$1"
operation="$2"

# Only explicitly approved read-only operations are accepted.
case "$operation" in
    discovery.system)
        remote_command='hostname; uname -s; uname -r; id -un'
        ;;
    *)
        echo "ERROR: Unsupported SSH operation" >&2
        exit 2
        ;;
esac

# Resolve connection details using backend-owned inventory.
if ! target_json="$("$INVENTORY_SCRIPT" "$target_id" 2>/dev/null)"; then
    echo "ERROR: Target unavailable or unauthorized" >&2
    exit 3
fi

host="$(jq -er '.host' <<< "$target_json")"
port="$(jq -er '.ssh_port' <<< "$target_json")"
user="$(jq -er '.ssh_user' <<< "$target_json")"

if [[ ! -f "$SSH_KEY" || ! -f "$KNOWN_HOSTS" ]]; then
    echo "ERROR: SSH identity or known_hosts missing" >&2
    exit 3
fi

# Validate inventory values before passing them to SSH.
if [[ ! "$host" =~ ^[a-zA-Z0-9.-]+$ ]] ||
   [[ ! "$user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]] ||
   [[ ! "$port" =~ ^[0-9]+$ ]] ||
   (( port < 1 || port > 65535 )); then
    echo "ERROR: Invalid SSH inventory fields" >&2
    exit 3
fi

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
    -- "$host" "$remote_command")"; then
    echo "ERROR: SSH discovery execution failed" >&2
    exit 4
fi

# Validate and normalize the four discovery fields.
if [[ "$(printf '%s\n' "$output" | wc -l)" -ne 4 ]]; then
    echo "ERROR: Unexpected discovery response" >&2
    exit 5
fi

hostname="$(sed -n '1p' <<< "$output")"
os="$(sed -n '2p' <<< "$output")"
kernel="$(sed -n '3p' <<< "$output")"
remote_user="$(sed -n '4p' <<< "$output")"

if [[ -z "$hostname" || -z "$os" || -z "$kernel" || -z "$remote_user" ]]; then
    echo "ERROR: Incomplete discovery response" >&2
    exit 5
fi

jq -n \
    --arg hostname "$hostname" \
    --arg os "$os" \
    --arg kernel "$kernel" \
    --arg remote_user "$remote_user" \
    '{
        hostname: $hostname,
        os: $os,
        kernel: $kernel,
        remote_user: $remote_user
    }'