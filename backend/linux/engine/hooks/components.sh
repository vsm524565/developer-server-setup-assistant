#!/usr/bin/env bash

# Hooks for the components phase. Only names registered in DSSA_HOOKS can
# be called, and only when a definition declares them.
#
#   postgresql_initdb       setup (a step): create the EL database cluster
#                           once; /var/lib/pgsql/data is in created_paths
#   verify_firewall_active  verify (read-only): firewall on, SSH allowed
#   verify_swap_active      verify (read-only): swap is in use
#   verify_php_fpm          verify (read-only): a php*-fpm unit is active
#                           (Debian/Ubuntu name it by version, e.g. php8.3-fpm)

DSSA_HOOKS="${DSSA_HOOKS:- }postgresql_initdb verify_firewall_active verify_swap_active verify_php_fpm "

# EL's postgresql-server ships without a cluster; the service cannot start
# until one exists. Debian/Ubuntu create theirs in the package.
postgresql_initdb() {
    local data="${DSSA_ROOT:-}/var/lib/pgsql/data"
    if [[ -f "$data/PG_VERSION" ]]; then
        echo "postgresql: cluster already exists; nothing to do" >&2
        return 0
    fi
    command -v postgresql-setup >/dev/null 2>&1 || { echo "postgresql: postgresql-setup not found" >&2; return 1; }
    postgresql-setup --initdb >&2
}

verify_firewall_active() {
    local port
    if [[ "${DSSA_MANAGER:-$(select_package_manager 2>/dev/null)}" == apt ]]; then
        ufw status 2>/dev/null | grep -q '^Status: active' || { echo "ufw: not active" >&2; return 1; }
        for port in $(_tx_ssh_ports 2>/dev/null); do
            ufw status 2>/dev/null | grep -Eq "^${port}/tcp +ALLOW" ||
                { echo "ufw: SSH port $port is not allowed" >&2; return 1; }
        done
        echo "ufw: active, SSH allowed" >&2
    else
        [[ "$(firewall-cmd --state 2>/dev/null)" == running ]] || { echo "firewalld: not running" >&2; return 1; }
        echo "firewalld: running" >&2
    fi
}

verify_swap_active() {
    [[ "$(dssa_swap_active)" == true ]] || { echo "swap: none active" >&2; return 1; }
    echo "swap: active" >&2
}

verify_php_fpm() {
    local unit
    unit="$(systemctl list-units --type=service --state=active --no-legend --plain 'php*-fpm.service' 2>/dev/null |
            awk '{print $1}' | head -1)"
    [[ -n "$unit" ]] || { echo "php-fpm: no active php*-fpm unit" >&2; return 1; }
    echo "php-fpm: $unit active" >&2
}
