#!/usr/bin/env bash

set -euo pipefail
umask 077

LOG_DIR="${DSSA_LOG_DIR:-${HOME}/.local/state/developer-server-setup-assistant/audit}"
LOG_FILE="${LOG_DIR}/security.jsonl"
LOCK_FILE="${LOG_DIR}/.audit.lock"

mkdir -p -- "$LOG_DIR"
chmod 700 -- "$LOG_DIR"

command -v flock >/dev/null 2>&1 || {
    echo "ERROR: flock is required" >&2
    exit 1
}

(
    flock -x 9

    if [[ ! -s "$LOG_FILE" ]]; then
        echo "No audit records to rotate."
        exit 0
    fi

    timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
    archive="${LOG_DIR}/security-${timestamp}-$(cat /proc/sys/kernel/random/uuid).jsonl"

    mv -- "$LOG_FILE" "$archive"
    : > "$LOG_FILE"
    chmod 600 -- "$LOG_FILE"

    echo "Rotated: $archive"

) 9>"$LOCK_FILE"
