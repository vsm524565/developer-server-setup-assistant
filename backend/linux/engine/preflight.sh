#!/usr/bin/env bash

# Commands the engine needs on the target (Milestone 4.39).
#
# Plain bash on purpose: the check runs before anything uses jq, so a
# server without jq gets a clear "Missing on this server: jq" instead of
# failing partway through. The package manager's own tools are checked
# by select_package_manager.

DSSA_PLAN_COMMANDS="jq awk grep sort stat sha256sum timeout getent ss systemctl"
DSSA_APPLY_COMMANDS="$DSSA_PLAN_COMMANDS flock setsid base64 find tail install sync journalctl"
DSSA_REPORT_COMMANDS="jq base64 flock find"
# Only when a runnable component adds a vendor repository.
DSSA_VENDOR_REPOSITORY_COMMANDS="curl gpg"

# Usage: dssa_missing_commands NAME...   prints the missing ones, ", "-joined
dssa_missing_commands() {
    local name out=""
    for name in "$@"; do
        command -v -- "$name" >/dev/null 2>&1 || out="${out:+$out, }$name"
    done
    printf '%s' "$out"
}

# Usage: dssa_require_commands plan|apply|report [PLAN_JSON DEFINITIONS_JSON]
# apply with a plan also needs curl and gpg when a runnable component's
# variant adds a vendor repository. Returns 1 (with an error) if any is missing.
dssa_require_commands() {
    local kind="$1" plan="${2:-}" definitions="${3:-}" list missing
    case "$kind" in
        plan) list="$DSSA_PLAN_COMMANDS" ;;
        apply) list="$DSSA_APPLY_COMMANDS" ;;
        report) list="$DSSA_REPORT_COMMANDS" ;;
        *) printf 'ERROR: Unknown command check: %s\n' "$kind" >&2; return 1 ;;
    esac
    local -a names
    read -r -a names <<< "$list"
    missing="$(dssa_missing_commands "${names[@]}")"

    if [[ -z "$missing" && "$kind" == apply && -n "$plan" ]] &&
       jq -e --argjson plan "$plan" '
           (map({key: .id, value: (.content | fromjson)}) | from_entries) as $defs
           | any($plan.steps[] | select(.action == "INSTALL") as $s
                 | $defs[$s.component].variants[]? | select(.variant_id == $s.variant_id)
                 | .steps[].type;
                 . == "apt_vendor_repository" or . == "dnf_vendor_repository")' \
           <<< "${definitions:-[]}" >/dev/null 2>&1; then
        read -r -a names <<< "$DSSA_VENDOR_REPOSITORY_COMMANDS"
        missing="$(dssa_missing_commands "${names[@]}")"
    fi

    [[ -z "$missing" ]] && return 0
    printf 'ERROR: Missing on this server: %s. Install %s, then try again. Nothing was changed.\n' \
        "$missing" "$([[ "$missing" == *,* ]] && echo them || echo it)" >&2
    return 1
}
