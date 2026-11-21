#!/usr/bin/env bash

# Updates (Milestone 4.43; application flow, decision 1).
#
# - A normal plan only reports an available update for an installed
#   component (a reason on its SKIP step).
# - `plan --update` plans UPDATE steps with the exact target versions,
#   following the Patch Updater pattern: only packages already installed,
#   no major-version change, no new or removed packages (apt simulates it
#   at plan time; both managers check it at apply), keep edited config
#   files, and afterwards running services run again while stopped ones
#   stay stopped.
# - Undo (decision 5b): the installed versions' package files are kept
#   first (remove.sh helpers); a failed update downgrades from them.
#
# Candidates come from the package lists already on the server (plan is
# read-only); apply refreshes the lists and refuses if the candidate moved.
#
# Requires journal.sh, record.sh, remove.sh, definitions.sh, package_manager.sh.

# The packages an update covers: the variant's detection packages plus its
# main install packages, as installed names (dnf: name.arch).
_upd_packages() {
    local definition="$1" variant_id="$2" manager="$3" now="$4"
    jq -c --arg v "$variant_id" --arg m "$manager" --argjson now "$now" '
        .variants[] | select(.variant_id == $v)
        | ([.detection.packages_all[]] + [.steps[] | select(.type == "packages_install" and .role == "main") | .packages[]])
        | unique
        | [.[] as $n | ($now | keys[] | select(. == $n or ($m == "dnf" and startswith($n + "."))))
           | {package: ., version: $now[.]}]' <<< "$definition"
}

# Candidate (newest available) version per package. Prints
# [{package, from, to}] for packages with a newer candidate.
_upd_candidates() {
    local manager="$1" packages="$2" name version candidate out='[]'
    while IFS=$'\t' read -r name version; do
        [[ -n "$name" ]] || continue
        case "$manager" in
            apt)
                candidate="$(apt-cache policy -- "$name" 2>/dev/null | sed -n 's/^ *Candidate: *//p' | head -1)" ;;
            dnf)
                candidate="$(dnf -q repoquery --upgrades --latest-limit 1 \
                    --qf '%{epoch}:%{version}-%{release}' -- "$name" 2>/dev/null | tail -1)" ;;
        esac
        [[ -n "$candidate" && "$candidate" != "(none)" && "$candidate" != "$version" ]] || continue
        out="$(jq -c --arg p "$name" --arg f "$version" --arg t "$candidate" '. + [{package: $p, from: $f, to: $t}]' <<< "$out")"
    done < <(jq -r '.[] | [.package, .version] | @tsv' <<< "$packages")
    printf '%s\n' "$out"
}

# "1:29.1.3-1~ubuntu" -> "29": the first number after any epoch.
_upd_major() { local v="${1#*:}"; [[ "$1" == *:* ]] || v="$1"; printf '%s' "${v%%[!0-9]*}"; }

# Usage: dssa_update_info DEFINITION MANAGER ENTRY   (normal plans)
# Adds "Update available: …" to a SKIP entry's reasons. Info only.
dssa_update_info() {
    local definition="$1" manager="$2" entry="$3" now packages updates
    jq -e '.action == "SKIP" and .variant_id != null' <<< "$entry" >/dev/null 2>&1 || { printf '%s\n' "$entry"; return 0; }
    now="$(tx_package_set "$manager" 2>/dev/null)" || { printf '%s\n' "$entry"; return 0; }
    packages="$(_upd_packages "$definition" "$(jq -r .variant_id <<< "$entry")" "$manager" "$now")"
    updates="$(_upd_candidates "$manager" "$packages")"
    jq -c --argjson u "$updates" '
        if ($u | length) > 0
        then .reasons += ["Update available: \([$u[] | "\(.package) \(.from) -> \(.to)"] | join(", ")) (plan --update to apply it)."]
        else . end' <<< "$entry"
}

# Usage: plan_update_component DEFINITION MANAGER PANELS PORTS
# Prints a plan entry: UPDATE (with details), SKIP (up to date) or BLOCKED.
plan_update_component() {
    local definition="$1" manager="$2" panels="$3" ports="$4"
    local entry now packages updates names sim bad majors units unavailable
    entry="$(plan_component_from_definition "$definition" "$manager" "$panels" "$ports")" || return $?
    if ! jq -e '.action == "SKIP"' <<< "$entry" >/dev/null; then
        jq -c '.decision = "BLOCKED" | .action = "BLOCKED"
               | .reasons = ["Not installed (or not verifiable) here, so there is nothing to update."] + .reasons' <<< "$entry"
        return 0
    fi
    now="$(tx_package_set "$manager")" || return 3
    packages="$(_upd_packages "$definition" "$(jq -r .variant_id <<< "$entry")" "$manager" "$now")"
    updates="$(_upd_candidates "$manager" "$packages")"
    if [[ "$updates" == "[]" ]]; then
        jq -c '.reasons += ["Up to date with the package lists on the server; nothing to update."]' <<< "$entry"
        return 0
    fi

    # No major-version change: that is a new branch, not an update.
    majors="$(jq -r '.[] | [.package, .from, .to] | @tsv' <<< "$updates" | while IFS=$'\t' read -r name from to; do
        [[ "$(_upd_major "$from")" == "$(_upd_major "$to")" ]] || printf '%s %s -> %s\n' "$name" "$from" "$to"
    done)"
    if [[ -n "$majors" ]]; then
        jq -c --arg m "$majors" '.decision = "BLOCKED" | .action = "BLOCKED"
            | .reasons = ["Major version change, not an update: \($m | rtrimstr("\n") | split("\n") | join(", ")). Plan it as a separate change."]' <<< "$entry"
        return 0
    fi

    # apt: simulate; no package may be added or removed.
    if [[ "$manager" == apt ]]; then
        mapfile -t names < <(jq -r '.[] | "\(.package)=\(.to)"' <<< "$updates")
        sim="$(apt-get -s install --only-upgrade -- "${names[@]}" 2>/dev/null)"
        bad="$( { grep -E '^Remv ' <<< "$sim" | awk '{print $2}'; grep -E '^Inst [^ ]+ \(' <<< "$sim" | awk '{print $2 " (new)"}'; } | sort -u | paste -sd, - )"
        if [[ -n "$bad" ]]; then
            jq -c --arg b "$bad" '.decision = "BLOCKED" | .action = "BLOCKED"
                | .reasons = ["The update would add or remove packages: \($b). Not an in-place update."]' <<< "$entry"
            return 0
        fi
    fi

    unavailable="$(_rm_unavailable "$manager" "$(jq -c '[.[] | {package, version: .from}]' <<< "$updates")")"
    units="$(jq -c --arg v "$(jq -r .variant_id <<< "$entry")" \
        '[.variants[] | select(.variant_id == $v) | .steps[] | select(.type == "service_start" or .type == "service_enable") | .unit] | unique' \
        <<< "$definition")"
    local running='[]' unit
    while IFS= read -r unit; do
        [[ -n "$unit" && "$(tx_unit_active_state "$unit")" == active ]] && running="$(jq -c --arg u "$unit" '. + [$u]' <<< "$running")"
    done < <(jq -r '.[]' <<< "$units")

    jq -c --argjson u "$updates" --argjson un "$unavailable" --argjson units "$units" --argjson running "$running" '
        .decision = "UPDATE" | .action = "UPDATE"
        | .details = {updates: $u, units: $units, running_units: $running,
                      reversible: ($un | length == 0), unavailable: $un}
        | .reasons = ["Update: \([$u[] | "\(.package) \(.from) -> \(.to)"] | join(", "))."]
                     + (if ($running | length) > 0 then ["Restarts running service: \($running | join(", ")); you must type the name to approve."] else [] end)
                     + (if ($un | length) == 0 then ["Reversible: the installed package files are kept before the update."]
                        else ["NOT reversible: no package file for \([$un[] | "\(.package) \(.version)"] | join(", ")); you must type the name to approve."] end)' \
        <<< "$entry"
}

# Usage: update_component PLAN_ID MANAGER DEFINITION VARIANT_ID DETAILS
# Prints one result JSON. outcome: updated | failed_rolled_back | rollback_incomplete
update_component() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4" details="$5"
    local component tx kept failure="" index=0 before after changed extra unit check
    local -a specs=() names=() files=()
    component="$(jq -r .id <<< "$definition")"
    tx="$(_tx_dir "$plan_id")"
    kept="$tx/kept/$component"
    mapfile -t names < <(jq -r '.updates[].package' <<< "$details")
    case "$manager" in
        apt) mapfile -t specs < <(jq -r '.updates[] | "\(.package)=\(.to)"' <<< "$details") ;;
        dnf) mapfile -t specs < <(jq -r '.updates[] | "\(.package | sub("\\.[a-z0-9_]+$"; ""))-\(.to).\(.package | capture("\\.(?<a>[a-z0-9_]+)$").a)"' <<< "$details") ;;
    esac

    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --arg v "$variant_id" \
        '{component: $c, phase: "component_begin", operation: "update", variant_id: $v, created_paths: []}')" || return 4

    # 1. Keep the installed versions' package files.
    index=1
    mkdir -p -m 700 -- "$kept" || failure="Cannot create $kept."
    if [[ -z "$failure" ]]; then
        local name version spec ok=true
        while IFS=$'\t' read -r name version; do
            case "$manager" in
                apt) _rm_apt_download "$kept" "$name=$version" ||
                         cp -p -- /var/cache/apt/archives/"${name}_${version//:/%3a}"_*.deb "$kept/" 2>/dev/null || ok=false ;;
                dnf) spec="${name%.*}-${version}.${name##*.}"
                     dnf download -q --destdir "$kept" -- "$spec" >&2 || ok=false ;;
            esac
        done < <(jq -r '.updates[] | [.package, .from] | @tsv' <<< "$details")
        mapfile -t files < <(find "$kept" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.rpm' \) -printf '%f\n' | sort)
        if [[ "$ok" != true || ${#files[@]} -lt ${#names[@]} ]] && jq -e '.reversible' <<< "$details" >/dev/null; then
            failure="Could not keep the package files; nothing was updated."
        fi
    fi
    _rm_journal "$plan_id" "$component" "$index" update_keep "$([[ -z "$failure" ]] && echo done || echo failed)" null \
        "$(printf '%s\n' "${files[@]}" | jq -R 'select(length > 0)' | jq -cs '{files: .}')" || true

    # 2. Unit states before (running ones must run again, stopped stay stopped).
    local states='{}'
    while IFS= read -r unit; do
        [[ -n "$unit" ]] && states="$(jq -c --arg u "$unit" --arg s "$(tx_unit_active_state "$unit")" '. + {($u): $s}' <<< "$states")"
    done < <(jq -r '.units[]' <<< "$details")

    # 3. Refresh the lists; the candidates must still be the planned versions.
    if [[ -z "$failure" ]]; then
        index=$((index + 1))
        case "$manager" in
            apt) _tx_apt update --error-on=any >&2 ;;
            dnf) dnf makecache -q >&2 ;;
        esac || failure="Refreshing package lists failed; nothing was updated."
        if [[ -z "$failure" ]]; then
            local now moved
            now="$(_upd_candidates "$manager" "$(jq -c '[.updates[] | {package, version: .from}]' <<< "$details")")"
            moved="$(jq -cn --argjson p "$(jq -c '.updates' <<< "$details")" --argjson n "$now" '[$p[] | select(. as $x | $n | index($x) == null) | .package]')"
            [[ "$moved" == "[]" ]] || failure="The available version changed since planning ($(jq -r 'join(", ")' <<< "$moved")); nothing was updated. Plan again."
        fi
    fi

    # 4. Upgrade to exactly the planned versions, keeping edited config files.
    if [[ -z "$failure" ]]; then
        index=$((index + 1))
        before="$tx/pkgsets/$component-up-$index.before.json"
        after="$tx/pkgsets/$component-up-$index.after.json"
        (umask 077 && tx_package_set "$manager" > "$before") || failure="Cannot record package state."
        _rm_journal "$plan_id" "$component" "$index" update_packages intent \
            "$(jq -cn --arg b "${before##*/}" --argjson s "$states" --argjson u "$(jq -c .updates <<< "$details")" \
                '{packages_before: $b, unit_states: $s, updates: $u}')" || true
        if [[ -z "$failure" ]]; then
            case "$manager" in
                apt) DEBIAN_FRONTEND=noninteractive _tx_apt install -y --only-upgrade \
                         -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef -- "${specs[@]}" >&2 ;;
                dnf) dnf upgrade -y --setopt=obsoletes=0 -- "${specs[@]}" >&2 ;;
            esac || failure="The package manager could not update: ${names[*]}."
            (umask 077 && tx_package_set "$manager" > "$after") || failure="${failure:-Cannot record package state.}"
            # Nothing may be added or removed.
            extra="$(jq -cn --slurpfile b "$before" --slurpfile a "$after" \
                '([$a[0] | keys[]] - [$b[0] | keys[]]) + ([$b[0] | keys[]] - [$a[0] | keys[]])' 2>/dev/null || echo '[]')"
            [[ "$extra" == "[]" ]] || failure="${failure:-The update added or removed packages: $(jq -r 'join(", ")' <<< "$extra").}"
            changed="$(jq -cn --slurpfile b "$before" --slurpfile a "$after" \
                '[$b[0] | to_entries[] | select($a[0][.key] != null and $a[0][.key] != .value)
                  | {package: .key, before: .value, after: $a[0][.key]}]' 2>/dev/null || echo '[]')"
            _rm_journal "$plan_id" "$component" "$index" update_packages "$([[ -z "$failure" ]] && echo done || echo failed)" null \
                "$(jq -cn --argjson c "$changed" --argjson e "$extra" '{changed: $c, added_or_removed: $e}')" || true
        fi
    fi

    # 5. Services back to their state before, then verification.
    if [[ -z "$failure" ]]; then
        while IFS=$'\t' read -r unit state; do
            if [[ "$state" == active ]]; then
                [[ "$(tx_unit_active_state "$unit")" == active ]] || systemctl start -- "$unit" >&2 || { failure="Could not start $unit again."; break; }
                _tx_service_settle "$unit" >/dev/null || { failure="Service $unit is not stable after the update."; break; }
            elif [[ "$(tx_unit_active_state "$unit")" == active ]]; then
                systemctl stop -- "$unit" >&2 || { failure="Could not stop $unit again."; break; }
            fi
        done < <(jq -r 'to_entries[] | [.key, .value] | @tsv' <<< "$states")
    fi
    if [[ -z "$failure" ]]; then
        local variant
        variant="$(jq -c --arg v "$variant_id" '.variants[] | select(.variant_id == $v)' <<< "$definition")"
        while IFS= read -r check; do
            tx_run_check "$manager" "$check" || { failure="Verification failed: $(jq -r .type <<< "$check")"; break; }
        done < <(jq -c '.verification[]' <<< "$variant")
    fi

    if [[ -z "$failure" ]]; then
        # Keep the record's versions current when DSSA manages it.
        local record
        if record="$(dssa_record_read 2>/dev/null)" && jq -e --arg c "$component" '.components | has($c)' <<< "$record" >/dev/null; then
            _dssa_record_write "$(jq -c --arg c "$component" --argjson now "$(tx_package_set "$manager")" '
                .components[$c] |= (.packages |= map(.version = ($now[.package] // .version))
                                    | .dependencies |= map(.version = ($now[.package] // .version)))' <<< "$record")" || true
        fi
        tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "component_end", outcome: "updated"}')" || true
        jq -cn --arg c "$component" --argjson ch "${changed:-[]}" '{component: $c, outcome: "updated", verification: "passed", changed: $ch}'
        return 0
    fi

    printf 'Update of %s failed: %s Rolling back.\n' "$component" "$failure" >&2
    local rollback outcome
    rollback="$(rollback_update "$plan_id" "$manager" "$component")"
    if jq -e '.complete == true' <<< "$rollback" >/dev/null 2>&1; then outcome=failed_rolled_back; else outcome=rollback_incomplete; fi
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --arg o "$outcome" '{component: $c, phase: "component_end", outcome: $o}')" || true
    jq -cn --arg c "$component" --arg o "$outcome" --arg f "$failure" --argjson rb "$rollback" \
        '{component: $c, outcome: $o, failure: $f, rollback: $rb}'
    [[ "$outcome" == failed_rolled_back ]] && return 3
    return 4
}

# Downgrade from the kept files and put units back. Prints {complete, actions, issues}.
rollback_update() {
    local plan_id="$1" manager="$2" component="$3"
    local tx entries ok=true unit state changed
    local -a actions=() issues=() files=() local_files=()
    tx="$(_tx_dir "$plan_id")"
    entries="$(tx_entries "$plan_id" "$component")"
    _record() { actions+=("$(jq -cn --arg a "$1" --arg t "$2" --arg r "$3" '{action: $a, target: $t, result: $r}')"); }
    _issue() { ok=false; issues+=("$1"); _record "$2" "$3" failed; }
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "rollback_begin"}')" || true

    changed="$(jq -c '[.[] | select(.type == "update_packages" and .phase != "intent") | .detail.changed // [] | .[]]' <<< "$entries")"
    if [[ "$changed" != "[]" ]]; then
        mapfile -t files < <(find "$tx/kept/$component" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.rpm' \) 2>/dev/null | sort)
        if (( ${#files[@]} == 0 )); then
            _issue "No kept package files to go back to." downgrade "$(jq -r '[.[].package] | join(" ")' <<< "$changed")"
        else
            local f
            for f in "${files[@]}"; do local_files+=("./${f#"$tx/"}"); done
            if case "$manager" in
                   apt) (cd -- "$tx" && DEBIAN_FRONTEND=noninteractive _tx_apt install -y --allow-downgrades \
                            -o Dpkg::Options::=--force-confold -- "${local_files[@]}" >&2) ;;
                   dnf) (cd -- "$tx" && dnf downgrade -y -- "${local_files[@]}" >&2) ;;
               esac; then
                _record downgrade "$(jq -r '[.[].package] | join(" ")' <<< "$changed")" ok
            else
                _issue "Downgrading from the kept files failed." downgrade "$(jq -r '[.[].package] | join(" ")' <<< "$changed")"
            fi
        fi
    fi

    while IFS=$'\t' read -r unit state; do
        [[ -n "$unit" ]] || continue
        if [[ "$state" == active ]]; then
            if [[ "$(tx_unit_active_state "$unit")" == active ]] || systemctl start -- "$unit" >&2; then _record start "$unit" ok
            else _issue "Could not start $unit." start "$unit"; fi
        elif [[ "$(tx_unit_active_state "$unit")" == active ]]; then
            if systemctl stop -- "$unit" >&2; then _record stop "$unit" ok; else _issue "Could not stop $unit." stop "$unit"; fi
        fi
    done < <(jq -r '[.[] | select(.type == "update_packages" and .phase == "intent")][0].pre.unit_states // {} | to_entries[] | [.key, .value] | @tsv' <<< "$entries")

    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --argjson ok "$ok" '{component: $c, phase: "rollback_end", complete: $ok}')" || true
    printf '%s\n' "${actions[@]}" | jq -cs --argjson ok "$ok" \
        --argjson issues "$(printf '%s\n' "${issues[@]}" | jq -R 'select(length > 0)' | jq -cs .)" \
        '{complete: $ok, actions: ., issues: $issues}'
}
