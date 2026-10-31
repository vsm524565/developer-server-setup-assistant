
#!/usr/bin/env bash
set -euo pipefail

# DSSA common privilege discovery.
# Read-only. Does not install packages, change sudoers,
# request passwords or execute privileged system changes.

discover_privileges() {
    local uid status sudo_available sudo_exit sudo_output
    local reason=""

    uid="$(id -u)" || return 1

    if [[ "$uid" == "0" ]]; then
        status="ROOT"
        sudo_available=false

        if command -v sudo >/dev/null 2>&1; then
            sudo_available=true
        fi
    elif ! command -v sudo >/dev/null 2>&1; then
        status="UNAVAILABLE"
        sudo_available=false
    else
        sudo_available=true

        # No password prompt and no privileged modification.
        # Timeout prevents stalled privilege checks.
        sudo_exit=0

        sudo_output="$(
            LC_ALL=C timeout 5 sudo -n -l 2>&1
        )" || sudo_exit=$?
        if [[ "$sudo_exit" -eq 0 ]]; then
            status="POLICY_LIST_AVAILABLE"
            reason="POLICY_INSPECTION_SUCCEEDED"
        elif [[ "$sudo_exit" -eq 124 ]]; then
            status="UNKNOWN"
            reason="SUDO_CHECK_TIMEOUT"
        elif grep -qiE \
            'a password is required|password is required|must provide a password|interactive authentication is required' \
            <<< "$sudo_output"; then
            status="PASSWORD_REQUIRED"
            reason="INTERACTIVE_AUTH_REQUIRED"
        elif grep -qiE \
            'not allowed to run sudo|not in the sudoers file|may not run sudo' \
            <<< "$sudo_output"; then
            status="DENIED"
        else
            status="UNKNOWN"
            reason="SUDO_CHECK_INDETERMINATE"
        fi
    fi

    printf '{'
    printf '"schema_version":"1.0",'
    printf '"check":"privileges",'
    printf '"uid":%d,' "$uid"
    printf '"sudo_available":%s,' "$sudo_available"
    printf '"status":"%s",' "$status"
    printf '"reason":"%s",' "$reason"
    printf '"execution_allowed":false'
    printf '}\n'
}
