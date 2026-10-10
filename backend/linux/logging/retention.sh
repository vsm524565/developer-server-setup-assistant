#!/usr/bin/env bash

set -euo pipefail
umask 077

LOG_DIR="${DSSA_LOG_DIR:-${HOME}/.local/state/developer-server-setup-assistant/audit}"
RETENTION_DAYS="${DSSA_AUDIT_RETENTION_DAYS:-90}"

if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]] ||
   (( RETENTION_DAYS < 1 )); then
    echo "ERROR: Invalid retention period" >&2
    exit 1
fi

if [[ ! -d "$LOG_DIR" ]]; then
    echo "No audit directory exists."
    exit 0
fi

echo "Audit retention: $RETENTION_DAYS days"
echo "Mode: DRY RUN — no files will be deleted"

find "$LOG_DIR" \
    -maxdepth 1 \
    -type f \
    -name 'security-*.jsonl' \
    -mtime +"$RETENTION_DAYS" \
    -print
