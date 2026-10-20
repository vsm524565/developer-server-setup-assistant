#!/usr/bin/env bash

# Report known control-panel paths without changing the server.
# Loading this file defines the function; it does not run discovery.
discover_panels() {
    local name path
    local -a detected=()

    while IFS='|' read -r name path; do
        if [[ -e "$path" ]]; then
            detected+=("$name")
        fi
    done <<'PANELS'
aapanel|/www/server/panel
cyberpanel|/usr/local/CyberCP
hestia|/usr/local/hestia
cpanel|/usr/local/cpanel
directadmin|/usr/local/directadmin
plesk|/usr/local/psa
PANELS

    printf '%s\n' "${detected[@]}" |
        jq -R -s '{
            panels: (
                split("\n")
                | map(select(length > 0))
                | unique
            )
        }'
}
