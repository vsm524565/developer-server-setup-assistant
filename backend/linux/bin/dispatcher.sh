#!/usr/bin/env bash

set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT_SCRIPT="$BASE_DIR/backend/linux/logging/audit.sh"
INVENTORY_SCRIPT="$BASE_DIR/backend/linux/connection/inventory.sh"

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
    and .operation == "discovery.system"
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

# Stub response: no SSH or remote discovery yet.
jq -n \
  --arg request_id "$request_id" \
  --arg operation "$operation" \
  '{
    schema_version:"1.0",
    request_id:$request_id,
    operation:$operation,
    status:"not_implemented",
    data:null,
    errors:[{
      code:"NOT_IMPLEMENTED",
      message:"Linux system discovery backend is not connected yet"
    }]
  }'
