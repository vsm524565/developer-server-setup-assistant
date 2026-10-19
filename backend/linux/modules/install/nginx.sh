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

# Shared functions must be supplied by the backend execution bundle.
for function_name in \
    package_is_installed \
    refresh_package_metadata \
    install_package \
    verify_nginx_configuration \
    verify_nginx_readiness
do
    declare -F "$function_name" >/dev/null ||
        fail "Missing shared function: $function_name"
done


# Skip existing packages without changing configuration or service state.
if package_is_installed "$manager" nginx; then
    printf 'Nginx is already installed; no changes made.\n' >&2
    printf 'RESULT: package_action=already_installed service_action=unchanged\n'
    exit 0
fi

# Verify tools needed for installation and readiness checks.
for tool in systemctl ss awk grep timeout; do
    command -v "$tool" >/dev/null 2>&1 ||
        fail "Missing prerequisite: $tool"
done

# Avoid modifying known panel-managed environments.
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

# Avoid overwriting an installation outside the package database.
if command -v nginx >/dev/null 2>&1 ||
   [[ -e /etc/nginx || -e /usr/local/nginx ]]; then
    fail "Existing unmanaged Nginx evidence requires review."
fi

# Package installation may start Nginx automatically.
listeners="$(ss -H -ltn)" ||
    fail "Unable to inspect listening TCP ports."

if awk '{print $4}' <<< "$listeners" |
   grep -Eq ':(80|443)$'; then
    fail "TCP port 80 or 443 is already listening."
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

printf 'RESULT: package_action=installed service_state=active\n'
)