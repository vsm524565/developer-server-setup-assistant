#!/usr/bin/env bash

# DSSA - Swap Filesystem Discovery
#
# Resolve the filesystem containing an existing swap file
# or the parent directory of a proposed new swap file.
#
# Read-only. Does not create or modify files.

discover_swap_filesystem() {
    local target_path="${1:-}"
    local probe_path
    local mountpoint source filesystem_type available_bytes

    # Require an absolute path.
    if [[ "$target_path" != /* || "$target_path" == "/" ]]; then
        echo "ERROR: Invalid swap target path." >&2
        return 2
    fi

    # Existing target: inspect its containing filesystem.
    # New target: inspect its existing parent directory.
    if [[ -e "$target_path" ]]; then
        probe_path="$target_path"
    else
        probe_path="$(dirname -- "$target_path")"

        if [[ ! -d "$probe_path" ]]; then
            echo "ERROR: Swap target parent directory does not exist." >&2
            return 3
        fi
    fi

    # Reject symlinks, including dangling final symlinks.
    if [[ -L "$target_path" ]]; then
        echo "ERROR: Symlink swap targets are unsupported." >&2
        return 3
    fi

    # Require a regular file or a proposed new file.
    if [[ -e "$target_path" ]]; then
        if [[ ! -f "$target_path" ]]; then
            echo "ERROR: Existing swap target is not a regular file." >&2
            return 3
        fi

        probe_path="$target_path"
    else
        probe_path="$(dirname -- "$target_path")"

        if [[ ! -d "$probe_path" ]]; then
            echo "ERROR: Swap target parent directory does not exist." >&2
            return 3
        fi
    fi

    # Resolve the parent directory without allowing a symlink
    # anywhere in the directory path.
    local parent_path component current_path
    parent_path="$(dirname -- "$target_path")"
    current_path=""

    # Reject dot segments and repeated separators in this V1 policy.
    if [[ "$target_path" == *"/./"* ||
          "$target_path" == *"/../"* ||
          "$target_path" == *"//"* ||
          "$target_path" == */. ||
          "$target_path" == */.. ]]; then
        echo "ERROR: Noncanonical swap target path." >&2
        return 3
    fi

    local -a path_components
    IFS='/' read -r -a path_components <<< "${parent_path#/}"

    for component in "${path_components[@]}"; do
        [[ -n "$component" ]] || continue
        current_path+="/$component"

        if [[ -L "$current_path" ]]; then
            echo "ERROR: Symlinked parent directories are unsupported." >&2
            return 3
        fi
    done

    # Obtain filesystem identity.
    mountpoint="$(findmnt -n -T "$probe_path" -o TARGET)" || return 3
    source="$(findmnt -n -T "$probe_path" -o SOURCE)" || return 3
    filesystem_type="$(findmnt -n -T "$probe_path" -o FSTYPE)" || return 3

    # Obtain available space on the actual target filesystem.
    available_bytes="$(
        df -B1 --output=avail "$probe_path" |
        awk 'NR == 2 {print $1}'
    )" || return 3

    if [[ -z "$mountpoint" ||
          -z "$source" ||
          -z "$filesystem_type" ||
          ! "$available_bytes" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Incomplete filesystem discovery." >&2
        return 3
    fi

    jq -n \
        --arg target "$target_path" \
        --arg mountpoint "$mountpoint" \
        --arg source "$source" \
        --arg filesystem_type "$filesystem_type" \
        --argjson available "$available_bytes" \
        '{
            schema_version: "1.0",
            target_path: $target,
            filesystem: {
                mountpoint: $mountpoint,
                source: $source,
                type: $filesystem_type,
                available_bytes: $available
            }
        }'
}
