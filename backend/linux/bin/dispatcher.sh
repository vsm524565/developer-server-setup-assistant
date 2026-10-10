#!/usr/bin/env bash

set -euo pipefail
umask 077

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT_SCRIPT="$BASE_DIR/backend/linux/logging/audit.sh"

command -v jq >/dev/null || {
    echo "ERROR: jq is required" >&2
    exit 1
}

request="$(cat)"

# Strictly validate the supported request contract.
if ! jq -e '
    type == "object"
    and .schema_version == "1.0"
    and (.request_id | type == "string" and length > 0)
    and .operation == "discovery.system"
    and .target_id == "lab-ubuntu-01"
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
