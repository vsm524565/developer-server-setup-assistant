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
#   opensearch_admin_password  setup (a step, before install): OpenSearch 3
#                           needs an initial admin password to install. A
#                           random one is stored root-only in
#                           /root/.dssa/opensearch-initial-admin-password and
#                           given to the install; never printed or logged.
#   postfix_local_only      setup (a step, before install, apt only): answer
#                           Postfix's installer question with "Local only",
#                           so it listens on localhost (no config file edited)

DSSA_HOOKS="${DSSA_HOOKS:- }postgresql_initdb verify_firewall_active verify_swap_active verify_php_fpm postfix_local_only opensearch_admin_password "
DSSA_HOOKS+="verify_panel_aapanel verify_panel_cyberpanel verify_panel_hestia verify_panel_cloudpanel "
DSSA_HOOKS+="verify_panel_cpanel verify_panel_plesk verify_panel_directadmin "

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

postfix_local_only() {
    local name
    name="$(hostname -f 2>/dev/null || hostname)"
    [[ "$name" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "postfix: unusable host name" >&2; return 1; }
    printf '%s\n' "postfix postfix/main_mailer_type select Local only" \
                   "postfix postfix/mailname string $name" | debconf-set-selections
}

opensearch_admin_password() {
    local dir file password
    dir="${DSSA_ROOT:-}/root/.dssa"
    file="$dir/opensearch-initial-admin-password"
    (umask 077 && mkdir -p -- "$dir") || return 1
    if [[ ! -s "$file" ]]; then
        # Letters and digits, then one of each class OpenSearch requires.
        password="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)Aa1-"
        (( ${#password} == 28 )) || { echo "opensearch: could not generate a password" >&2; return 1; }
        (umask 077 && printf '%s\n' "$password" > "$file") || return 1
    fi
    OPENSEARCH_INITIAL_ADMIN_PASSWORD="$(head -1 -- "$file")"
    export OPENSEARCH_INITIAL_ADMIN_PASSWORD
    echo "opensearch: initial admin password stored root-only in /root/.dssa/opensearch-initial-admin-password" >&2
}

# Panels (4.48): read-only, the panel's own folder is there (the same
# paths discover_panels reports).
_dssa_verify_panel() {
    local panel="$1"
    if discover_panels 2>/dev/null | jq -e --arg p "$panel" '.panels | index($p) != null' >/dev/null; then
        echo "$panel: installed" >&2
    else
        echo "$panel: not found" >&2
        return 1
    fi
}
verify_panel_aapanel() { _dssa_verify_panel aapanel; }
verify_panel_cyberpanel() { _dssa_verify_panel cyberpanel; }
verify_panel_hestia() { _dssa_verify_panel hestia; }
verify_panel_cloudpanel() { _dssa_verify_panel cloudpanel; }
verify_panel_cpanel() { _dssa_verify_panel cpanel; }
verify_panel_plesk() { _dssa_verify_panel plesk; }
verify_panel_directadmin() { _dssa_verify_panel directadmin; }
