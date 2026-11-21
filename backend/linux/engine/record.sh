#!/usr/bin/env bash

# Permanent install record (Milestone 4.41).
#
# /var/lib/dssa/installed.json says which components DSSA installed (or
# adopted) on this server, and what it put there. Uninstall, adopt and
# config ownership depend on it (application flow, decisions 2 and 3).
# Unlike transactions it is never removed by retention.
#
#   {"schema_version": "1.0", "components": {
#      "<id>": {"how": "installed" | "adopted", "variant_id": ..., "plan_id": ...,
#               "recorded_at": ..., "depends_on": [required component ids],
#               "packages": [{package, version}],
#               "dependencies": [{package, version}], "enabled_units": [...],
#               "started_units": [...], "created_paths": [...],
#               "repository_files": [...], "files": [{path, sha256}]}}}
#
# The record is not secret: it is mode 0644 and the state folder is 0711,
# so the read-only plan (no sudo) can read it without listing anything
# else. It is trusted only when root owns it and only root can write it.
#
# Requires journal.sh (state folder, journal, package sets) and jq.

DSSA_RECORD_SCHEMA="1.0"

_dssa_record_file() { printf '%s/installed.json' "$(_tx_state_dir)"; }

_dssa_record_empty() { jq -cn --arg v "$DSSA_RECORD_SCHEMA" '{schema_version: $v, components: {}}'; }

# Usage: dssa_record_read   prints the record (an empty one if none yet).
# Fails, with an error, when the file is unsafe or not a valid record.
dssa_record_read() {
    local file owner=0 meta
    file="$(_dssa_record_file)"
    [[ -n "${DSSA_ROOT:-}" ]] && owner="$EUID"
    if [[ -L "$file" ]]; then
        _tx_fail "Install record $file is a symlink; not trusted."
        return 1
    fi
    if [[ ! -e "$file" ]]; then
        _dssa_record_empty
        return 0
    fi
    [[ -f "$file" ]] || { _tx_fail "Install record $file is not a file; not trusted."; return 1; }
    meta="$(stat -c '%u %a' -- "$file")" || { _tx_fail "Cannot read $file."; return 1; }
    if [[ "${meta% *}" != "$owner" ]] || (( 8#${meta#* } & 8#022 )); then
        _tx_fail "Install record $file is not root-only writable (uid mode: $meta); not trusted."
        return 1
    fi
    jq -ce --arg v "$DSSA_RECORD_SCHEMA" \
        'select(type == "object" and .schema_version == $v and (.components | type == "object"))' \
        "$file" 2>/dev/null || { _tx_fail "Install record $file is not a valid record."; return 1; }
}

# Usage: _dssa_record_write RECORD_JSON   atomic replace, mode 0644
_dssa_record_write() {
    local record="$1" file tmp
    file="$(_dssa_record_file)"
    [[ ! -L "$file" ]] || { _tx_fail "Install record $file is a symlink."; return 1; }
    tmp="$(mktemp "$(_tx_state_dir)/.installed.XXXXXX")" || return 1
    if ! jq . <<< "$record" > "$tmp" || ! chmod 644 -- "$tmp"; then
        rm -f -- "$tmp"
        return 1
    fi
    sync -- "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$file" || { rm -f -- "$tmp"; return 1; }
}

# Usage: dssa_record_component_entry PLAN_ID MANAGER DEFINITION_JSON VARIANT_ID
# What this run's journal says the component changed (intent entries keep
# their pre-state under .pre). Prints the entry.
dssa_record_component_entry() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4"
    local tx component entries now added_names='[]' file before after paths_pre created='[]' path
    tx="$(_tx_dir "$plan_id")"
    component="$(jq -r .id <<< "$definition")"
    entries="$(tx_entries "$plan_id" "$component")"
    now="$(tx_package_set "$manager")" || return 1

    # Packages new since each install step began (requested ones and the
    # dependencies the package manager pulled in).
    while IFS= read -r file; do
        before="$tx/pkgsets/$file"
        after="${before%.before.json}.after.json"
        [[ -f "$before" && -f "$after" ]] || continue
        added_names="$(jq -cn --argjson acc "$added_names" --slurpfile b "$before" --slurpfile a "$after" \
            '$acc + ([$a[0] | keys[]] - [$b[0] | keys[]]) | unique')"
    done < <(jq -r '.[] | select(.type == "packages_install" and .phase == "intent") | .pre.packages_before' <<< "$entries")

    # Paths the definition may create that did not exist before the run.
    paths_pre="$(jq -c '.paths // {}' "$tx/snapshot.json" 2>/dev/null || echo '{}')"
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        if [[ "$(jq -r --arg p "$path" '.[$p] // "absent"' <<< "$paths_pre")" == absent && -e "$(_tx_path "$path")" ]]; then
            created="$(jq -c --arg p "$path" '. + [$p]' <<< "$created")"
        fi
    done < <(jq -r --arg v "$variant_id" '.variants[] | select(.variant_id == $v) | .rollback.created_paths[]' <<< "$definition")

    jq -cn --arg v "$variant_id" --arg p "$plan_id" --arg at "$(_tx_now)" \
        --argjson entries "$entries" --argjson now "$now" --argjson added "$added_names" \
        --argjson created "$created" --argjson deps "$(jq -c '[.depends_on[] | select(.required != false) | .id]' <<< "$definition")" '
        ([$entries[] | select(.type == "packages_install" and .phase == "intent")
          | .pre.packages - (.pre.already_installed // [])] | add // [] | unique) as $requested
        | def versions($names): [$names[] | select($now[.] != null) | {package: ., version: $now[.]}];
        {how: "installed", variant_id: $v, plan_id: $p, recorded_at: $at, depends_on: $deps,
         packages: versions([$requested[] | select(. as $r | $added | index($r))]),
         dependencies: versions($added - $requested),
         enabled_units: [$entries[] | select(.type == "service_enable" and .phase == "intent"
                                             and .pre.state != "enabled") | .pre.unit] | unique,
         started_units: [$entries[] | select(.type == "service_start" and .phase == "intent"
                                             and .pre.state != "active") | .pre.unit] | unique,
         created_paths: $created,
         repository_files: [$entries[] | select((.type == "apt_vendor_repository" or .type == "dnf_vendor_repository")
                                                and .phase == "intent")
                             | .pre.key, .pre.source | select(.state == "absent") | .path] | unique,
         files: [$entries[] | select(.type == "managed_file" and .phase == "done")
                 | {path: .detail.path, sha256: .detail.sha256}]}'
}

# Usage: dssa_record_installed PLAN_ID MANAGER DEFINITION_JSON VARIANT_ID
# Runs inside the transaction (under the run lock) after a component was
# installed and verified. Adds or replaces its entry.
dssa_record_installed() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4" record entry component
    component="$(jq -r .id <<< "$definition")"
    record="$(dssa_record_read)" || return 1
    entry="$(dssa_record_component_entry "$plan_id" "$manager" "$definition" "$variant_id")" || return 1
    record="$(jq -c --arg c "$component" --argjson e "$entry" '.components[$c] = $e' <<< "$record")" || return 1
    _dssa_record_write "$record"
}

# Usage: dssa_record_forget COMPONENT   (used when a recorded install is undone)
dssa_record_forget() {
    local component="$1" record
    record="$(dssa_record_read)" || return 1
    jq -e --arg c "$component" '.components | has($c)' <<< "$record" >/dev/null || return 0
    _dssa_record_write "$(jq -c --arg c "$component" 'del(.components[$c])' <<< "$record")"
}
