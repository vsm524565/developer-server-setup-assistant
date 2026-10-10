#!/usr/bin/env bash

# Structured audit logger for the central Linux backend.
# Input: one JSON event object through stdin.
# Output: none on success.
# Failure: non-zero exit status.

set -euo pipefail
umask 077

LOG_DIR="${DSSA_LOG_DIR:-${HOME}/.local/state/developer-server-setup-assistant/audit}"
LOG_FILE="${LOG_DIR}/security.jsonl"

command -v jq >/dev/null 2>&1 || {
    echo "ERROR: jq is required" >&2
    exit 1
}

mkdir -p -- "$LOG_DIR"
chmod 700 -- "$LOG_DIR"

# Read one complete event from stdin.
event="$(cat)"

# Validate the minimum event contract and add trusted timestamp/event ID.
validated="$(
    jq -ce \
      --arg timestamp "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
      --arg event_id "evt-$(cat /proc/sys/kernel/random/uuid)" \
      '
      if (
    type == "object"
    and .schema_version == "1.0"
    and (.request_id | type == "string" and length > 0)
    and (.source | type == "string" and length > 0)
    and (.event | type == "string" and length > 0)
    and (.operation | type == "string" and length > 0)
    and (.target_id | type == "string" and length > 0)
    and (.status | type == "string" and length > 0)
    and (
        (keys - [
            "schema_version",
            "request_id",
            "source",
            "event",
            "actor",
            "target_id",
            "operation",
            "status",
            "duration_ms",
            "exit_code",
            "details"
            ]) | length == 0
        )
    )
    then
    . + {
        timestamp: $timestamp,
        event_id: $event_id
        }
        else
        error("Invalid audit event")
        end
      ' <<< "$event"
)"

# Prevent concurrent writers from interleaving records.
(
    flock -x 9

    touch -- "$LOG_FILE"
    chmod 600 -- "$LOG_FILE"

    printf '%s\n' "$validated" >> "$LOG_FILE"
) 9>"${LOG_DIR}/.audit.lock"
