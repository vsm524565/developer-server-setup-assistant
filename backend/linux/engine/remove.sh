#!/usr/bin/env bash

# Uninstall and adopt (Milestone 4.42; application flow, decision 2).
#
# Remove: only components the install record (record.sh) lists. Packages
# DSSA added are removed; data folders are never deleted. Before anything
# changes, the package files of the installed versions are kept in the
# transaction folder, so a failed removal is undone from them
# (decision 5b). Refused while another recorded component needs it.
#
# Adopt: an installed, verified component that DSSA did not install is
# added to the record (how: "adopted"), so DSSA may later remove it.
#
# Requires journal.sh, record.sh, definitions.sh and package_manager.sh.

# --- planning (read-only, no sudo) ---------------------------------------------

# Package files for exact versions: can they be obtained? Prints a JSON
# array of the {package, version} that cannot.
_rm_unavailable() {
    local manager="$1" packages="$2" name version spec out='[]'
    while IFS=$'\t' read -r name version; do
        [[ -n "$name" ]] || continue
        case "$manager" in
            apt)
                apt-cache show -- "$name=$version" >/dev/null 2>&1 ||
                    compgen -G "/var/cache/apt/archives/${name}_${version//:/%3a}_*.deb" >/dev/null ||
                    out="$(jq -c --arg p "$name" --arg v "$version" '. + [{package: $p, version: $v}]' <<< "$out")"
                ;;
            dnf)
                spec="${name%.*}-${version}.${name##*.}"
                [[ -n "$(dnf -q repoquery -- "$spec" 2>/dev/null)" ]] ||
                    out="$(jq -c --arg p "$name" --arg v "$version" '. + [{package: $p, version: $v}]' <<< "$out")"
                ;;
        esac
    done < <(jq -r '.[] | [.package, .version] | @tsv' <<< "$packages")
    printf '%s\n' "$out"
}

# What the package manager would remove for NAMES. apt: the package names
# a simulated purge lists. dnf: rpm -e --test refuses when anything else
# still needs them (prints "!" on refusal). Prints a JSON array.
_rm_simulate() {
    local manager="$1"; shift
    case "$manager" in
        apt)
            apt-get -s purge -- "$@" 2>/dev/null |
                sed -n 's/^\(Purg\|Remv\) \([^ ]*\).*/\2/p' | sed 's/:[a-z0-9]*$//' |
                jq -Rn '[inputs] | unique'
            ;;
        dnf)
            if rpm -e --test -- "$@" >/dev/null 2>&1; then
                printf '%s\n' "$@" | jq -Rn '[inputs] | unique'
            else
                echo '["!"]'
            fi
            ;;
    esac
}

# Usage: plan_remove_component DEFINITION MANAGER RECORD SELECTION_JSON
# Prints one plan entry with action REMOVE or BLOCKED.
plan_remove_component() {
    local definition="$1" manager="$2" record="$3" selection="$4"
    local component entry needed claimed main deps names sim extra kept_deps='[]' remove unavailable
    component="$(jq -r .id <<< "$definition")"
    entry="$(jq -c --arg c "$component" '.components[$c] // null' <<< "$record")"

    _rm_entry() {
        # action reasons [details]
        jq -cn --arg c "$component" --arg m "$manager" --arg a "$1" --argjson r "$2" \
            --argjson e "$entry" --argjson d "${3:-null}" '
            {component: $c, variant_id: ($e.variant_id // null), support: "supported",
             decision: $a, action: $a, reasons: $r, package_manager: $m,
             planning_only: true, execution_allowed: false}
            + (if $d == null then {} else {details: $d} end)'
    }

    if [[ "$entry" == null ]]; then
        _rm_entry BLOCKED '["Not installed by DSSA on this server, so DSSA will not remove it. To let DSSA manage it, adopt it first (plan --adopt)."]'
        return 0
    fi

    # Panels (4.48): no undo DSSA can do; the vendor uninstaller (where one
    # exists) is run by hand until a panel definition carries one.
    local panel_un
    panel_un="$(jq -r --arg v "$(jq -r '.variant_id // ""' <<< "$entry")" \
        '[.variants[] | select(.variant_id == $v) | .steps[] | select(.type == "vendor_installer")][0]
         | if . == null then "" elif .uninstall_url then "vendor:\(.uninstall_url)" else "none" end' <<< "$definition")"
    if [[ "$panel_un" == none ]]; then
        _rm_entry BLOCKED '["A control panel cannot be removed: the vendor has no uninstaller. Reinstall the OS or restore the VM snapshot."]'
        return 0
    elif [[ "$panel_un" == vendor:* ]]; then
        _rm_entry BLOCKED "$(jq -cn --arg u "${panel_un#vendor:}" '["Remove this panel with the vendor uninstaller (\($u)) by hand; DSSA runs it automatically only when an install fails."]')"
        return 0
    fi

    needed="$(jq -c --arg c "$component" --argjson sel "$selection" \
        '[.components | to_entries[] | select((.key as $k | $sel | index($k)) == null)
          | select((.value.depends_on // []) | index($c)) | .key]' <<< "$record")"
    if [[ "$needed" != "[]" ]]; then
        _rm_entry BLOCKED "$(jq -c '["Still needed by \(join(", ")), which DSSA also manages. Remove those in the same plan, or keep this one."]' <<< "$needed")"
        return 0
    fi

    # Only what is still installed, and never a package another recorded
    # component (not being removed) also claims.
    claimed="$(jq -c --argjson sel "$selection" \
        '[.components | to_entries[] | select((.key as $k | $sel | index($k)) == null)
          | .value | (.packages + .dependencies)[] | .package] | unique' <<< "$record")"
    local name list='[]'
    for list in packages dependencies; do
        local kept='[]'
        while IFS= read -r name; do
            [[ -n "$name" ]] || continue
            jq -e --arg n "$name" 'index($n) == null' <<< "$claimed" >/dev/null || continue
            package_is_installed "$manager" "$name" >/dev/null 2>&1 || continue
            kept="$(jq -c --arg n "$name" --argjson e "$entry" --arg l "$list" \
                '. + [$e[$l][] | select(.package == $n)]' <<< "$kept")"
        done < <(jq -r --arg l "$list" '.[$l][].package' <<< "$entry")
        if [[ "$list" == packages ]]; then main="$kept"; else deps="$kept"; fi
    done

    remove="$(jq -cn --argjson a "$main" --argjson b "$deps" '$a + $b')"
    if [[ "$remove" != "[]" ]]; then
        mapfile -t names < <(jq -r '.[].package' <<< "$remove")
        sim="$(_rm_simulate "$manager" "${names[@]}")"
        extra="$(jq -c --argjson want "$(jq -c '[.[].package | sub("\\.[a-z0-9_]+$"; "")]' <<< "$remove")" \
            --argjson raw "$(jq -c '[.[].package]' <<< "$remove")" \
            '[.[] | select(. as $p | ($want | index($p)) == null and ($raw | index($p)) == null)]' <<< "$sim")"
        if [[ "$extra" != "[]" && "$deps" != "[]" ]]; then
            # Keep the dependencies something else still needs; try again.
            kept_deps="$deps"
            remove="$main"
            mapfile -t names < <(jq -r '.[].package' <<< "$remove")
            if [[ "$remove" == "[]" ]]; then extra='[]'; else
                sim="$(_rm_simulate "$manager" "${names[@]}")"
                extra="$(jq -c --argjson want "$(jq -c '[.[].package | sub("\\.[a-z0-9_]+$"; "")]' <<< "$remove")" \
                    --argjson raw "$(jq -c '[.[].package]' <<< "$remove")" \
                    '[.[] | select(. as $p | ($want | index($p)) == null and ($raw | index($p)) == null)]' <<< "$sim")"
            fi
        fi
        if [[ "$extra" != "[]" ]]; then
            _rm_entry BLOCKED "$(jq -c 'if . == ["!"] then ["Other installed packages still need these packages; the package manager would refuse or remove more than DSSA installed."]
                                       else ["Removing would also remove packages DSSA did not install: \(join(", "))."] end' <<< "$extra")"
            return 0
        fi
    fi

    unavailable="$(_rm_unavailable "$manager" "$remove")"

    # 4.45: files DSSA wrote go only if nobody changed them since.
    local remove_files='[]' kept_files='[]' fpath fsha now_sha
    while IFS=$'\t' read -r fpath fsha; do
        [[ -n "$fpath" ]] || continue
        [[ -f "$(_tx_path "$fpath")" && ! -L "$(_tx_path "$fpath")" ]] || continue
        now_sha="sha256:$(sha256sum -- "$(_tx_path "$fpath")" | awk '{print $1}')"
        if [[ "$now_sha" == "$fsha" ]]; then
            remove_files="$(jq -c --arg p "$fpath" '. + [$p]' <<< "$remove_files")"
        else
            kept_files="$(jq -c --arg p "$fpath" '. + [$p]' <<< "$kept_files")"
        fi
    done < <(jq -r '(.files // [])[] | [.path, .sha256] | @tsv' <<< "$entry")
    local details
    details="$(jq -cn --argjson e "$entry" --argjson remove "$remove" --argjson kept "$kept_deps" \
        --argjson un "$unavailable" --argjson rf "$remove_files" --argjson kf "$kept_files" '
        {remove_packages: $remove, kept_dependencies: $kept, remove_files: $rf, kept_files: $kf,
         stop_units: ($e.started_units // []), disable_units: ($e.enabled_units // []),
         repository_files: ($e.repository_files // []), kept_paths: ($e.created_paths // []),
         reversible: ($un | length == 0), unavailable: $un, record_entry: $e}')"
    _rm_entry REMOVE "$(jq -c --argjson e "$entry" '
        ["\(if $e.how == "adopted" then "Adopted" else "Installed" end) by DSSA (plan \($e.plan_id[0:8]), \($e.recorded_at))."]
        + (if (.remove_packages | length) > 0 then ["Removes: \([.remove_packages[] | "\(.package) \(.version)"] | join(", "))."]
           else ["Its packages are already gone; only the record and DSSA files are removed."] end)
        + (if (.kept_dependencies | length) > 0 then ["Kept (still needed): \([.kept_dependencies[].package] | join(", "))."] else [] end)
        + (if (.stop_units + .disable_units | length) > 0 then ["Stops/disables: \((.stop_units + .disable_units) | unique | join(", "))."] else [] end)
        + (if (.kept_paths | length) > 0 then ["Data kept: \(.kept_paths | join(", "))."] else [] end)
        + (if (.remove_files | length) > 0 then ["Removes DSSA files: \(.remove_files | join(", "))."] else [] end)
        + (if (.kept_files | length) > 0 then ["Kept (edited since DSSA wrote them): \(.kept_files | join(", "))."] else [] end)
        + (if .reversible then ["Reversible: the installed package files are kept before removal."]
           else ["NOT reversible: no package file for \([.unavailable[] | "\(.package) \(.version)"] | join(", ")); you must type the name to approve."] end)' \
        <<< "$details")" "$details"
}

# Usage: plan_adopt_component DEFINITION MANAGER PANELS PORTS RECORD
# An installed component (planned SKIP) DSSA does not yet manage -> ADOPT.
plan_adopt_component() {
    local definition="$1" manager="$2" panels="$3" ports="$4" record="$5" entry component
    component="$(jq -r .id <<< "$definition")"
    entry="$(plan_component_from_definition "$definition" "$manager" "$panels" "$ports")" || return $?
    if jq -e --arg c "$component" '.components | has($c)' <<< "$record" >/dev/null; then
        jq -c '.decision = "BLOCKED" | .action = "BLOCKED"
               | .reasons = ["Already managed by DSSA (in the install record); nothing to adopt."]' <<< "$entry"
    elif jq -e '.action == "SKIP"' <<< "$entry" >/dev/null; then
        jq -c '.decision = "ADOPT" | .action = "ADOPT"
               | .reasons += ["Adopt: after verification it is added to the install record, so DSSA may later remove it. Nothing on the server changes."]' <<< "$entry"
    else
        jq -c '.decision = "BLOCKED" | .action = "BLOCKED"
               | .reasons = ["Not installed (or not verifiable) here, so there is nothing to adopt."] + .reasons' <<< "$entry"
    fi
}

# --- apply (root, inside a transaction) ------------------------------------------

# Usage: _rm_apt_download KEPT_DIR NAME=VERSION
# apt downloads as its unprivileged _apt user (its sandbox); it cannot
# write into the root-only kept folder and would fall back to root. So
# download into a private temporary folder owned by _apt, then move the
# file into kept/ (4.46 lab finding).
_rm_apt_download() {
    local kept="$1" spec="$2" tmp rc=0 file
    tmp="$(mktemp -d)" || return 1
    chmod 700 -- "$tmp"
    if [[ "$EUID" -eq 0 ]] && id -u _apt >/dev/null 2>&1; then
        chown _apt -- "$tmp" || { rm -rf -- "$tmp"; return 1; }
    fi
    (cd -- "$tmp" && _tx_apt download -- "$spec" >&2) || rc=$?
    if (( rc == 0 )); then
        for file in "$tmp"/*.deb; do
            [[ -f "$file" && ! -L "$file" ]] || continue
            mv -f -- "$file" "$kept/" && chown root:root -- "$kept/${file##*/}" 2>/dev/null
            chmod 600 -- "$kept/${file##*/}" || rc=1
        done
    fi
    rm -rf -- "$tmp"
    return "$rc"
}

_rm_journal() { tx_journal "$1" "$(_tx_step_entry "$2" "$3" "$4" "$5" "${6:-null}" "${7:-null}")"; }

# Usage: remove_component PLAN_ID MANAGER COMPONENT DETAILS_JSON
# Prints one result JSON. outcome: removed | failed_rolled_back | rollback_incomplete
remove_component() {
    local plan_id="$1" manager="$2" component="$3" details="$4"
    local tx kept unit failure="" index=0 before after removed extra path pre
    local -a names=() files=()
    tx="$(_tx_dir "$plan_id")"
    kept="$tx/kept/$component"
    mapfile -t names < <(jq -r '.remove_packages[].package' <<< "$details")

    tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "component_begin", operation: "remove", created_paths: []}')" || return 4

    # 1. Keep the installed versions' package files (the undo source).
    index=1
    mkdir -p -m 700 -- "$kept" || failure="Cannot create $kept."
    if [[ -z "$failure" && ${#names[@]} -gt 0 ]]; then
        local name version spec ok=true
        while IFS=$'\t' read -r name version; do
            case "$manager" in
                apt)
                    _rm_apt_download "$kept" "$name=$version" ||
                        cp -p -- /var/cache/apt/archives/"${name}_${version//:/%3a}"_*.deb "$kept/" 2>/dev/null || ok=false ;;
                dnf)
                    spec="${name%.*}-${version}.${name##*.}"
                    dnf download -q --destdir "$kept" -- "$spec" >&2 || ok=false ;;
            esac
        done < <(jq -r '.remove_packages[] | [.package, .version] | @tsv' <<< "$details")
        mapfile -t files < <(find "$kept" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.rpm' \) -printf '%f\n' | sort)
        if [[ "$ok" != true || ${#files[@]} -lt ${#names[@]} ]]; then
            if jq -e '.reversible' <<< "$details" >/dev/null; then
                failure="Could not keep the package files; nothing was removed."
            else
                echo "Package files could not all be kept; continuing (the plan said not reversible)." >&2
            fi
        fi
    fi
    _rm_journal "$plan_id" "$component" "$index" remove_keep "$([[ -z "$failure" ]] && echo done || echo failed)" null \
        "$(printf '%s\n' "${files[@]}" | jq -R 'select(length > 0)' | jq -cs '{files: .}')" || failure="${failure:-Journal write failed.}"

    # 2. Stop and disable the units DSSA started or enabled.
    if [[ -z "$failure" ]]; then
        while IFS= read -r unit; do
            [[ -n "$unit" ]] || continue
            index=$((index + 1))
            pre="$(tx_unit_active_state "$unit")"
            _rm_journal "$plan_id" "$component" "$index" remove_stop intent "$(jq -cn --arg u "$unit" --arg s "$pre" '{unit: $u, state: $s}')"
            if [[ "$pre" == active ]] && ! systemctl stop -- "$unit" >&2; then failure="Could not stop $unit."; break; fi
            _rm_journal "$plan_id" "$component" "$index" remove_stop done
        done < <(jq -r '.stop_units[]' <<< "$details")
    fi
    if [[ -z "$failure" ]]; then
        while IFS= read -r unit; do
            [[ -n "$unit" ]] || continue
            index=$((index + 1))
            pre="$(tx_unit_file_state "$unit")"
            _rm_journal "$plan_id" "$component" "$index" remove_disable intent "$(jq -cn --arg u "$unit" --arg s "$pre" '{unit: $u, state: $s}')"
            if [[ "$pre" == enabled ]] && ! systemctl disable -- "$unit" >&2; then failure="Could not disable $unit."; break; fi
            _rm_journal "$plan_id" "$component" "$index" remove_disable done
        done < <(jq -r '.disable_units[]' <<< "$details")
    fi

    # 3. Vendor repository files DSSA wrote (backed up first).
    if [[ -z "$failure" ]]; then
        while IFS= read -r path; do
            [[ -n "$path" ]] || continue
            index=$((index + 1))
            pre="$(_tx_backup_pre "$plan_id" "$path" "$component-rm-$index")" || { failure="Cannot back up $path."; break; }
            _rm_journal "$plan_id" "$component" "$index" remove_file intent "$pre"
            if [[ "$(jq -r .state <<< "$pre")" == file ]] && ! rm -f -- "$(_tx_path "$path")"; then failure="Cannot remove $path."; break; fi
            _rm_journal "$plan_id" "$component" "$index" remove_file done
        done < <(jq -r '.repository_files[], (.remove_files // [])[]' <<< "$details")
    fi

    # 4. The packages. Nothing else may go with them.
    if [[ -z "$failure" && ${#names[@]} -gt 0 ]]; then
        index=$((index + 1))
        before="$tx/pkgsets/$component-rm-$index.before.json"
        after="$tx/pkgsets/$component-rm-$index.after.json"
        (umask 077 && tx_package_set "$manager" > "$before") || failure="Cannot record package state."
        if [[ -z "$failure" ]]; then
            _rm_journal "$plan_id" "$component" "$index" remove_packages intent \
                "$(jq -cn --arg b "${before##*/}" --argjson p "$(printf '%s\n' "${names[@]}" | jq -R . | jq -cs .)" '{packages_before: $b, packages: $p}')"
            case "$manager" in
                apt) DEBIAN_FRONTEND=noninteractive _tx_apt purge -y -- "${names[@]}" >&2 ;;
                dnf) dnf remove -y --noautoremove -- "${names[@]}" >&2 ;;
            esac || failure="The package manager could not remove: ${names[*]}."
            (umask 077 && tx_package_set "$manager" > "$after") || failure="${failure:-Cannot record package state.}"
            removed="$(jq -cn --slurpfile b "$before" --slurpfile a "$after" '[$b[0] | keys[]] - [$a[0] | keys[]]' 2>/dev/null || echo '[]')"
            extra="$(jq -c --argjson want "$(printf '%s\n' "${names[@]}" | jq -R . | jq -cs .)" '. - $want' <<< "$removed")"
            [[ "$extra" == "[]" ]] || failure="${failure:-The package manager also removed: $(jq -r 'join(", ")' <<< "$extra").}"
            _rm_journal "$plan_id" "$component" "$index" remove_packages "$([[ -z "$failure" ]] && echo done || echo failed)" null \
                "$(jq -cn --argjson r "$removed" --arg a "${after##*/}" '{removed: $r, packages_after: $a}')"
        fi
    fi

    # 5. Verify, then forget the record entry.
    if [[ -z "$failure" ]]; then
        local n
        for n in "${names[@]}"; do
            package_is_installed "$manager" "$n" >/dev/null 2>&1 && { failure="Still installed after removal: $n."; break; }
        done
    fi
    if [[ -z "$failure" ]]; then
        index=$((index + 1))
        _rm_journal "$plan_id" "$component" "$index" record_forget intent "$(jq -c '.record_entry' <<< "$details")"
        if dssa_record_forget "$component"; then
            _rm_journal "$plan_id" "$component" "$index" record_forget done
        else
            failure="Could not update the install record."
        fi
    fi

    if [[ -z "$failure" ]]; then
        tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "component_end", outcome: "removed"}')" || true
        jq -cn --arg c "$component" --argjson d "$details" \
            '{component: $c, outcome: "removed", removed_packages: [$d.remove_packages[].package],
              kept_paths: $d.kept_paths, kept_dependencies: [$d.kept_dependencies[].package]}'
        return 0
    fi

    printf 'Removal of %s failed: %s Rolling back.\n' "$component" "$failure" >&2
    local rollback outcome
    rollback="$(rollback_remove "$plan_id" "$manager" "$component")"
    if jq -e '.complete == true' <<< "$rollback" >/dev/null 2>&1; then outcome=failed_rolled_back; else outcome=rollback_incomplete; fi
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --arg o "$outcome" '{component: $c, phase: "component_end", outcome: $o}')" || true
    jq -cn --arg c "$component" --arg o "$outcome" --arg f "$failure" --argjson rb "$rollback" \
        '{component: $c, outcome: $o, failure: $f, rollback: $rb}'
    [[ "$outcome" == failed_rolled_back ]] && return 3
    return 4
}

# Undo a removal from its journal: reinstall from the kept files, put the
# repository files and the record entry back, re-enable and restart units.
# Prints {complete, actions, issues}.
rollback_remove() {
    local plan_id="$1" manager="$2" component="$3"
    local tx entries ok=true e unit state path backup
    local -a actions=() issues=() files=()
    tx="$(_tx_dir "$plan_id")"
    entries="$(tx_entries "$plan_id" "$component")"
    _record() { actions+=("$(jq -cn --arg a "$1" --arg t "$2" --arg r "$3" '{action: $a, target: $t, result: $r}')"); }
    _issue() { ok=false; issues+=("$1"); _record "$2" "$3" failed; }
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "rollback_begin"}')" || true

    # Packages: reinstall what was removed, from the kept files.
    local removed
    removed="$(jq -c '[.[] | select(.type == "remove_packages" and .phase != "intent") | .detail.removed // []] | add // []' <<< "$entries")"
    if [[ "$removed" != "[]" ]]; then
        mapfile -t files < <(find "$tx/kept/$component" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.rpm' \) 2>/dev/null | sort)
        if (( ${#files[@]} == 0 )); then
            _issue "No kept package files to reinstall: $(jq -r 'join(", ")' <<< "$removed")." reinstall "$(jq -r 'join(" ")' <<< "$removed")"
        else
            local -a local_files=()
            for e in "${files[@]}"; do local_files+=("./${e#"$tx/"}"); done
            if case "$manager" in
                   apt) (cd -- "$tx" && DEBIAN_FRONTEND=noninteractive _tx_apt install -y --allow-downgrades -- "${local_files[@]}" >&2) ;;
                   dnf) (cd -- "$tx" && dnf install -y -- "${local_files[@]}" >&2) ;;
               esac; then
                _record reinstall "$(jq -r 'join(" ")' <<< "$removed")" ok
            else
                _issue "Reinstalling from the kept files failed." reinstall "$(jq -r 'join(" ")' <<< "$removed")"
            fi
        fi
    fi

    # Repository files back from their backups.
    while IFS=$'\t' read -r path state backup; do
        [[ -n "$path" && "$state" == file ]] || continue
        if cp -p -- "$tx/backups/$backup" "$(_tx_path "$path")"; then _record restore "$path" ok
        else _issue "Could not restore $path." restore "$path"; fi
    done < <(jq -r '.[] | select(.type == "remove_file" and .phase == "intent") | [.pre.path, .pre.state, (.pre.backup // "")] | @tsv' <<< "$entries")

    # Units: enable and start again what was enabled or active before.
    while IFS=$'\t' read -r unit state; do
        [[ -n "$unit" && "$state" == enabled ]] || continue
        if systemctl enable -- "$unit" >&2; then _record enable "$unit" ok; else _issue "Could not enable $unit." enable "$unit"; fi
    done < <(jq -r '.[] | select(.type == "remove_disable" and .phase == "intent") | [.pre.unit, .pre.state] | @tsv' <<< "$entries")
    while IFS=$'\t' read -r unit state; do
        [[ -n "$unit" && "$state" == active ]] || continue
        if systemctl start -- "$unit" >&2; then _record start "$unit" ok; else _issue "Could not start $unit." start "$unit"; fi
    done < <(jq -r '.[] | select(.type == "remove_stop" and .phase == "intent") | [.pre.unit, .pre.state] | @tsv' <<< "$entries")

    # The record entry, if it was already forgotten.
    if jq -e 'any(.[]; .type == "record_forget" and .phase == "done")' <<< "$entries" >/dev/null; then
        local record entry
        entry="$(jq -c '[.[] | select(.type == "record_forget" and .phase == "intent")][0].pre' <<< "$entries")"
        if record="$(dssa_record_read)" &&
           _dssa_record_write "$(jq -c --arg c "$component" --argjson e "$entry" '.components[$c] = $e' <<< "$record")"; then
            _record record_restore "$component" ok
        else
            _issue "Could not restore the install record entry." record_restore "$component"
        fi
    fi

    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --argjson ok "$ok" '{component: $c, phase: "rollback_end", complete: $ok}')" || true
    printf '%s\n' "${actions[@]}" | jq -cs --argjson ok "$ok" \
        --argjson issues "$(printf '%s\n' "${issues[@]}" | jq -R 'select(length > 0)' | jq -cs .)" \
        '{complete: $ok, actions: ., issues: $issues}'
}

# Usage: adopt_component PLAN_ID MANAGER DEFINITION VARIANT_ID
# Verify (read-only), then record as adopted. Prints one result JSON.
adopt_component() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4" component result record entry now
    component="$(jq -r .id <<< "$definition")"
    result="$(verify_component "$manager" "$definition" "$variant_id")" || {
        jq -c '. + {outcome: "verification_failed", note: "Not adopted: verification failed."}' <<< "${result:-{\}}"
        return 5
    }
    now="$(tx_package_set "$manager")" || return 4
    local variant units_enabled='[]' units_active='[]' unit paths='[]' path
    variant="$(jq -c --arg v "$variant_id" '.variants[] | select(.variant_id == $v)' <<< "$definition")"
    while IFS= read -r unit; do
        [[ -n "$unit" ]] || continue
        [[ "$(tx_unit_file_state "$unit")" == enabled ]] && units_enabled="$(jq -c --arg u "$unit" '. + [$u]' <<< "$units_enabled")"
        [[ "$(tx_unit_active_state "$unit")" == active ]] && units_active="$(jq -c --arg u "$unit" '. + [$u]' <<< "$units_active")"
    done < <(jq -r '[.steps[] | select(.type == "service_enable" or .type == "service_start") | .unit] | unique[]' <<< "$variant")
    while IFS= read -r path; do
        [[ -n "$path" && -e "$(_tx_path "$path")" ]] && paths="$(jq -c --arg p "$path" '. + [$p]' <<< "$paths")"
    done < <(jq -r '[.steps[] | select(.type == "apt_vendor_repository" or .type == "dnf_vendor_repository")
                     | .key_path, (.source_path // .repo_path)] | unique[]' <<< "$variant")
    entry="$(jq -cn --arg v "$variant_id" --arg p "$plan_id" --arg at "$(_tx_now)" --argjson now "$now" \
        --argjson variant "$variant" --argjson en "$units_enabled" --argjson ac "$units_active" --argjson files "$paths" \
        --argjson deps "$(jq -c '[.depends_on[] | select(.required != false) | .id]' <<< "$definition")" '
        def versions($names): [$names[] as $n | ($now | keys[] | select(. == $n or startswith($n + "."))) as $k
                               | {package: $k, version: $now[$k]}];
        {how: "adopted", variant_id: $v, plan_id: $p, recorded_at: $at, depends_on: $deps,
         packages: versions($variant.detection.packages_all), dependencies: [],
         enabled_units: $en, started_units: $ac, created_paths: [],
         repository_files: $files, files: []}')"
    record="$(dssa_record_read)" || return 4
    _dssa_record_write "$(jq -c --arg c "$component" --argjson e "$entry" '.components[$c] = $e' <<< "$record")" || return 4
    jq -cn --arg c "$component" --argjson e "$entry" \
        '{component: $c, outcome: "adopted", changes: "record only", packages: [$e.packages[].package]}'
}
