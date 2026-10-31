
#!/usr/bin/env bash
set -euo pipefail

# DSSA common bootstrap capability discovery.
#
# Read-only.
# No sudo, package installation, service changes,
# filesystem modification or external dependencies.
#
# Produces structured JSON without relying on jq.

discover_bootstrap() {
    local command_name
    local status
    local resolved_path
    local first=1
    local available_count=0
    local missing_count=0

    local commands=(
        bash
        jq
        awk
        sed
        grep
        stat
        date
        df
        findmnt
        lsblk
        mktemp
        base64
        systemctl
        sudo
        ip
        ss
        getent
    )

    printf '{'
    printf '"schema_version":"1.0",'
    printf '"check":"bootstrap_capabilities",'
    printf '"capabilities":['

    for command_name in "${commands[@]}"; do
        if (( first == 0 )); then
            printf ','
        fi

        first=0

        if resolved_path="$(type -P "$command_name")" &&
           [[ -n "$resolved_path" ]]; then
            status="AVAILABLE"
            available_count=$((available_count + 1))
        else
            status="MISSING"
            resolved_path=""
            missing_count=$((missing_count + 1))
        fi

        # Command names are fixed literals from the internal allowlist.
        # Resolved paths are deliberately not serialized here, avoiding
        # unsafe JSON string interpolation and unnecessary information.
        printf '{"name":"%s","status":"%s"}' \
            "$command_name" "$status"
    done

    printf '],'
    printf '"summary":{'
    printf '"total":%d,' "${#commands[@]}"
    printf '"available":%d,' "$available_count"
    printf '"missing":%d' "$missing_count"
    printf '},'
    printf '"execution_allowed":false'
    printf '}\n'
}

# Permit direct execution and SSH stdin execution.
discover_bootstrap
