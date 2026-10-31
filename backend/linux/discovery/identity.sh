
#!/usr/bin/env bash
set -euo pipefail

# DSSA common identity discovery.
# Read-only. Does not change users, groups or privileges.

discover_identity() {
    local uid gid username is_root

    uid="$(id -u)" || return 1
    gid="$(id -g)" || return 1

    if [[ ! "$uid" =~ ^[0-9]+$ ||
          ! "$gid" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Invalid UID or GID" >&2
        return 1
    fi

    # Prefer the effective account name.
    # Fallback to numeric UID if the account has no NSS entry.
    username="$(id -un 2>/dev/null)" || username="$uid"

    if [[ "$uid" == "0" ]]; then
        is_root=true
    else
        is_root=false
    fi

    # Use Bash's JSON-safe string encoding for the username.
    # Avoid treating an arbitrary account name as JSON source.
    local escaped_username
    escaped_username="${username//\\/\\\\}"
    escaped_username="${escaped_username//\"/\\\"}"

    printf '{'
    printf '"schema_version":"1.0",'
    printf '"check":"identity",'
    printf '"uid":%d,' "$uid"
    printf '"gid":%d,' "$gid"
    printf '"username":"%s",' "$escaped_username"
    printf '"is_root":%s,' "$is_root"
    printf '"execution_allowed":false'
    printf '}\n'
}
