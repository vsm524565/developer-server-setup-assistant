#!/usr/bin/env bash

# Collect listening TCP ports without changing the server.
discover_ports() {
    local listeners

    command -v ss >/dev/null 2>&1 || {
        echo "ERROR: ss is required for port discovery." >&2
        return 1
    }

    listeners="$(ss -H -ltn)" || {
        echo "ERROR: Unable to inspect TCP listeners." >&2
        return 1
    }

    awk '{print $4}' <<< "$listeners" |
        jq -R -s '{
            listening_tcp_ports: (
                split("\n")
                | map(select(length > 0))
                | map(split(":") | last | tonumber)
                | unique
            )
        }'
}
