#!/usr/bin/env bash

# Read-only checks. Loading this file does not execute verification.
verify_nginx_configuration() {
    [[ -x /usr/sbin/nginx ]] || {
        echo "ERROR: Nginx executable not found." >&2
        return 1
    }

    /usr/sbin/nginx -t >&2
}

verify_nginx_readiness() {
    local http_status

    systemctl is-active --quiet nginx || {
        echo "ERROR: Nginx service is not active." >&2
        return 1
    }

    if ! http_status="$(
        timeout 5 bash -c '
            exec 3<>/dev/tcp/127.0.0.1/80 || exit 1
            printf "GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3
            IFS= read -r status <&3 || exit 1
            printf "%s\n" "$status"
        '
    )"; then
        echo "ERROR: Local HTTP check failed or timed out." >&2
        return 1
    fi

    http_status="${http_status%$'\r'}"

    if [[ ! "$http_status" =~ ^HTTP/1\.[01][[:space:]]+2[0-9][0-9][[:space:]] ]]; then
        printf 'ERROR: Unexpected HTTP status: %s\n' "$http_status" >&2
        return 1
    fi

    printf 'HTTP readiness: %s\n' "$http_status" >&2
}
