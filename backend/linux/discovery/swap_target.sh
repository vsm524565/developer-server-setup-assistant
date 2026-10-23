#!/usr/bin/env bash

# Read-only swap target identity discovery.
# Never creates, removes, or modifies the target.

discover_swap_target() {
    if [[ $# -ne 1 ]]; then
        echo "ERROR: Expected one target path." >&2
        return 2
    fi

    local target="$1"
    local target_type="UNKNOWN"
    local exists=false
    local is_symlink=false

    if [[ "$target" != /* ||
          "$target" == "/" ||
          "$target" == *"//"* ||
          "$target" == */./* ||
          "$target" == */../* ||
          "$target" == */. ||
          "$target" == */.. ]]; then
        echo "ERROR: Invalid swap target path." >&2
        return 2
    fi
    # Inspect every parent component without following parent symlinks.
    # Reject uncertain or redirected paths before classifying the target.
    local parent="${target%/*}"
    local component
    local current=""
    local -a components

    [[ -n "$parent" ]] || parent="/"

    IFS='/' read -r -a components <<< "${parent#/}"

    for component in "${components[@]}"; do
        [[ -n "$component" ]] || continue
        current+="/$component"

        if [[ -L "$current" ]]; then
            echo "ERROR: Swap target parent contains a symlink." >&2
            return 3
        fi

        if [[ ! -d "$current" ]]; then
            echo "ERROR: Swap target parent is missing or inaccessible." >&2
            return 3
        fi

        if [[ ! -r "$current" || ! -x "$current" ]]; then
            echo "ERROR: Swap target parent cannot be inspected." >&2
            return 3
        fi
    done
    if [[ -L "$target" ]]; then
        target_type="SYMLINK"
        exists=true
        is_symlink=true
    elif [[ -f "$target" ]]; then
        target_type="REGULAR_FILE"
        exists=true
    elif [[ -d "$target" ]]; then
        target_type="DIRECTORY"
        exists=true
    elif [[ -e "$target" ]]; then
        target_type="OTHER"
        exists=true
    else
        # Only classify absence when the immediate parent can be
        # inspected. Otherwise, the target's state is uncertain.
        if [[ -r "$parent" && -x "$parent" ]]; then
            target_type="ABSENT"
        else
            target_type="UNKNOWN"
        fi
    fi

    jq -n \
        --arg target "$target" \
        --arg type "$target_type" \
        --argjson exists "$exists" \
        --argjson symlink "$is_symlink" '
        {
            schema_version: "1.0",
            component: "swap",
            check: "target_identity",
            target_path: $target,
            target_type: $type,
            exists: $exists,
            is_symlink: $symlink,
            execution_allowed: false
        }
    '
}
