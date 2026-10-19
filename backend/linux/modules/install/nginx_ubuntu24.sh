#!/usr/bin/env bash
set -euo pipefail
umask 077

# Install and verify distribution-packaged Nginx on Ubuntu 24.04.
# This module is invoked only after backend authorization and sudo.

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

[[ "$EUID" -eq 0 ]] || fail "Root privileges are required."

source /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" ]] ||
    fail "This installer supports Ubuntu 24.04 only."

for tool in dpkg-query apt-get systemctl ss awk grep timeout; do
    command -v "$tool" >/dev/null 2>&1 ||
        fail "Missing prerequisite: $tool"
done

# Reject known panel-managed environments.
for panel_path in \
    /www/server/panel \
    /usr/local/CyberCP \
    /usr/local/hestia \
    /usr/local/cpanel \
    /usr/local/directadmin \
    /usr/local/psa
do
    [[ ! -e "$panel_path" ]] ||
        fail "Control-panel evidence found: $panel_path"
done

installed=false
package_status="$(dpkg-query -W -f='${Status}' nginx 2>/dev/null || true)"

if [[ "$package_status" == "install ok installed" ]]; then
    installed=true
fi

if [[ "$installed" == false ]]; then
    # Avoid overwriting an unmanaged Nginx installation.
    if command -v nginx >/dev/null 2>&1 ||
       [[ -e /etc/nginx || -e /usr/local/nginx ]]; then
        fail "Existing unmanaged Nginx evidence requires review."
    fi

    # Check before installing: package scripts may start the service.
    listeners="$(ss -H -ltn)"
    if awk '{print $4}' <<< "$listeners" |
       grep -Eq ':(80|443)$'; then
        fail "TCP port 80 or 443 is already listening."
    fi

    printf 'Installing Nginx...\n' >&2
    apt-get update >&2
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx >&2
else
    printf 'Nginx package already installed; verifying it.\n' >&2
fi

# Validate configuration before explicitly starting the service.
/usr/sbin/nginx -t >&2
systemctl start nginx
systemctl is-active --quiet nginx ||
    fail "Nginx service is not active."

# Check that a local HTTP endpoint responds within five seconds.
# This checks the default HTTP listener, not external reachability.
if ! http_status="$(
    timeout 5 bash -c '
        exec 3<>/dev/tcp/127.0.0.1/80 || exit 1
        printf "GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3
        IFS= read -r status <&3 || exit 1
        printf "%s\n" "${status%$'\''\r'\''}"
    '
)"; then
    fail "Local HTTP readiness check failed or timed out."
fi

if [[ ! "$http_status" =~ ^HTTP/1\.[01][[:space:]]+2[0-9][0-9][[:space:]] ]]; then
    fail "Local HTTP readiness returned an unexpected status: $http_status"
fi

printf 'HTTP readiness: %s\n' "$http_status" >&2

if [[ "$installed" == true ]]; then
    printf 'RESULT: package_action=already_installed service_state=active\n'
else
    printf 'RESULT: package_action=installed service_state=active\n'
fi
