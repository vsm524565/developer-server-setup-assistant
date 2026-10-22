
#!/usr/bin/env bash

# DSSA - Swap Identity Resolver
#
# Resolve a swap source to its canonical filesystem path.
# Read-only: does not activate, deactivate, or modify swap.

resolve_swap_path() {
    local source="${1:-}"
    local resolved=""

    [[ -n "$source" ]] || return 1

    case "$source" in
        UUID=*|PARTUUID=*|LABEL=*)
            command -v findfs >/dev/null 2>&1 || return 1
            resolved="$(findfs "$source" 2>/dev/null)" || return 1
            ;;

        /dev/*|/*)
            resolved="$source"
            ;;

        *)
            return 1
            ;;
    esac

    [[ -e "$resolved" ]] || return 1

    readlink -f -- "$resolved"
}

resolve_swap_uuid() {
    local source="${1:-}"

    [[ -n "$source" ]] || return 1
    command -v blkid >/dev/null 2>&1 || return 1

    blkid -s UUID -o value -- "$source" 2>/dev/null
}
