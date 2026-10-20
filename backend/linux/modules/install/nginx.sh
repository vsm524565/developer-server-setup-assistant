#!/usr/bin/env bash


# Install missing Nginx using shared package-manager functions.
# Existing packages are skipped without changing service state.
# The backend runner bundles package_manager.sh before this script.
# A subshell isolates variables and exit statements to this component.
install_nginx() (
    set -euo pipefail
    umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

# Verify execution privileges and identify the operating system.
[[ "$EUID" -eq 0 ]] || fail "Root privileges are required."

local manager="${1:?Package manager is required}"
local panel_facts="${2:?Panel discovery result is required}"

# Shared functions must be supplied by the backend execution bundle.
for function_name in \
    package_is_installed \
    refresh_package_metadata \
    install_package \
    verify_nginx_configuration \
    discover_ports \
    verify_nginx_readiness
do
    declare -F "$function_name" >/dev/null ||
        fail "Missing shared function: $function_name"
done

# Skip existing packages without changing configuration or service state.
if package_is_installed "$manager" nginx; then
    printf 'Nginx is already installed; no changes made.\n' >&2
    jq -cn '{
        component: "nginx",
        package_action: "skipped",
        verification: "not_run",
        service_action: "unchanged"
    }'
    exit 0
fi

# Conservative policy for installing missing Nginx on panel-managed hosts.
if ! jq -e '
    type == "object"
    and (.panels | type == "array")
    and (.panels | length == 0)
' <<< "$panel_facts" >/dev/null; then
    fail "Panel evidence found or invalid discovery data; Nginx installation requires review."
fi

# Verify tools needed for installation and readiness checks.
for tool in systemctl ss awk grep timeout; do
    command -v "$tool" >/dev/null 2>&1 ||
        fail "Missing prerequisite: $tool"
done

# Avoid overwriting an installation outside the package database.
if command -v nginx >/dev/null 2>&1 ||
   [[ -e /etc/nginx || -e /usr/local/nginx ]]; then
    fail "Existing unmanaged Nginx evidence requires review."
fi

# Package installation may start Nginx automatically.
local port_facts

port_facts="$(discover_ports)" ||
    fail "Port discovery failed."

if ! jq -e '
    type == "object"
    and (.listening_tcp_ports | type == "array")
    and all(.listening_tcp_ports[]; type == "number")
    and (
        [.listening_tcp_ports[] | select(. == 80 or . == 443)]
        | length == 0
    )
' <<< "$port_facts" >/dev/null; then
    fail "TCP port 80 or 443 is occupied, or port discovery data is invalid."
fi

# Install using the selected method; do not try another on failure.
printf 'Installing Nginx using %s...\n' "$manager" >&2

if [[ "$manager" == "apt" ]]; then
    refresh_package_metadata "$manager" ||
        fail "Package metadata refresh failed."
fi

install_package "$manager" nginx ||
    fail "Nginx installation failed; partial changes may remain."

package_is_installed "$manager" nginx ||
    fail "Nginx package installation could not be verified."

# Check configuration before making the explicit service change.
verify_nginx_configuration ||
    fail "Nginx configuration validation failed."

systemctl start nginx ||
    fail "Unable to start Nginx."

verify_nginx_readiness ||
    fail "Nginx readiness verification failed."

jq -cn '{
    component: "nginx",
    package_action: "installed",
    verification: "passed",
    service_action: "start_requested"
}'
)