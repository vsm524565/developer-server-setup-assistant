#!/usr/bin/env bash

# Change journal, journaled step execution and rollback (Milestone 4.33).
#
# This is the first DSSA engine that CHANGES servers. Rules:
#   - One run per server at a time (flock on run.lock).
#   - A whole-server snapshot is taken before any change.
#   - Write-ahead journal: every change is recorded (intent + pre-state)
#     and fsynced BEFORE it happens, then recorded again (done/failed).
#   - Unknown pre-state means refuse (InfraGuard rule).
#   - A failed component is rolled back to the snapshot from its journal
#     alone. Undo actions are idempotent, so an interrupted step is
#     treated as possibly applied.
#   - Rollback order: stop/disable new units, remove new packages,
#     remove repository files, remove created paths, daemon-reload, and
#     only if all of that succeeded restore pre-existing unit states.
#   - Upgraded pre-existing packages are reported, never downgraded.
#   - All command output goes to stderr; public functions print at most
#     one JSON value on stdout.
#
# Production runs as root with DSSA_ROOT empty. Tests set DSSA_ROOT to a
# temporary directory and put mock package/systemd tools on PATH.
#
# Requires definitions.sh (4.30) for package_is_installed-independent
# validation helpers, plus jq, flock, sync, curl, gpg, sha256sum.

DSSA_TX_RETENTION_DAYS=30
DSSA_PLAN_ID_PATTERN='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
DSSA_SUPPORTED_STEPS=" package_metadata_refresh packages_install apt_vendor_repository service_enable service_start "
DSSA_PROTECTED_PATHS=" / /bin /boot /dev /etc /home /lib /lib64 /opt /proc /root /run /sbin /srv /sys /tmp /usr /var /var/lib /var/log /etc/apt /etc/apt/sources.list.d /etc/systemd /etc/systemd/system "

_tx_fail() { printf 'ERROR: %s\n' "$1" >&2; return 1; }
_tx_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
_tx_path() { printf '%s%s' "${DSSA_ROOT:-}" "$1"; }
_tx_state_dir() { _tx_path /var/lib/dssa; }
_tx_dir() { printf '%s/transactions/%s' "$(_tx_state_dir)" "$1"; }

_tx_require_privilege() {
    # Without DSSA_ROOT the engine acts on the real system: root only.
    if [[ -z "${DSSA_ROOT:-}" && "$EUID" -ne 0 ]]; then
        _tx_fail "Root privileges are required."
        return 1
    fi
}

_tx_valid_plan_id() {
    [[ "$1" =~ $DSSA_PLAN_ID_PATTERN ]] || _tx_fail "Invalid plan ID."
}

# --- package and unit state ----------------------------------------------

# Installed packages as {"name": "version"}.
tx_package_set() {
    local manager="$1"

    case "$manager" in
        apt)
            dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' 2>/dev/null |
                jq -Rn '[inputs | split("\t") | select(length == 3 and (.[2] | startswith("ii")))
                         | {(.[0]): .[1]}] | add // {}'
            ;;
        dnf)
            rpm -qa --qf '%{NAME}.%{ARCH}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n' 2>/dev/null |
                jq -Rn '[inputs | split("\t") | select(length == 2 and (.[0] | startswith("gpg-pubkey.") | not))
                         | {(.[0]): .[1]}] | add // {}'
            ;;
        *)
            _tx_fail "Unsupported package manager."
            ;;
    esac
}

# Unit-file state of one unit: enabled, disabled, static, masked,
# not-found, ... (never empty).
tx_unit_file_state() {
    local state
    state="$(systemctl is-enabled -- "$1" 2>/dev/null)" || true
    [[ -n "$state" ]] || state="not-found"
    printf '%s\n' "$state"
}

tx_unit_active_state() {
    local state
    state="$(systemctl is-active -- "$1" 2>/dev/null)" || true
    [[ -n "$state" ]] || state="unknown"
    printf '%s\n' "$state"
}

# Every unit: {"unit": {"file_state": ..., "active": ...}}.
tx_unit_states() {
    local files units
    files="$(systemctl list-unit-files --no-legend --no-pager 2>/dev/null |
        jq -Rn '[inputs | split(" ") | map(select(length > 0)) | select(length >= 2)
                 | {(.[0]): {file_state: .[1]}}] | add // {}')" || return 1
    units="$(systemctl list-units --all --no-legend --no-pager --plain 2>/dev/null |
        jq -Rn '[inputs | split(" ") | map(select(length > 0)) | select(length >= 4)
                 | {(.[0]): {active: .[2]}}] | add // {}')" || return 1
    jq -cn --argjson f "$files" --argjson u "$units" '$f * $u'
}

# State of each named path: absent, file, directory, symlink, other.
tx_path_states() {
    local paths_json="$1" path real kind
    local -a pairs=()

    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        real="$(_tx_path "$path")"
        if [[ -L "$real" ]]; then kind=symlink
        elif [[ -d "$real" ]]; then kind=directory
        elif [[ -f "$real" ]]; then kind=file
        elif [[ -e "$real" ]]; then kind=other
        else kind=absent
        fi
        pairs+=("$(jq -cn --arg p "$path" --arg k "$kind" '{($p): $k}')")
    done < <(jq -r '.[]' <<< "$paths_json")

    printf '%s\n' "${pairs[@]}" | jq -cs 'add // {}'
}

# --- journal ---------------------------------------------------------------

# Append one entry and force it to disk before returning.
tx_journal() {
    local plan_id="$1" entry="$2"
    local file seq

    file="$(_tx_dir "$plan_id")/journal.jsonl"
    # DSSA's own files are private regardless of the process umask. The
    # umask itself is never changed: package scripts must keep theirs.
    [[ -e "$file" ]] || (umask 077 && : > "$file") || return 1
    seq=$(( $( (wc -l < "$file") 2>/dev/null || echo 0) + 1 ))

    jq -c --argjson seq "$seq" --arg at "$(_tx_now)" \
        '{seq: $seq, at: $at} + .' <<< "$entry" >> "$file" || return 1
    sync -- "$file" || return 1
}

tx_entries() {
    local plan_id="$1" component="${2:-}"
    local file
    file="$(_tx_dir "$plan_id")/journal.jsonl"
    [[ -f "$file" ]] || { echo '[]'; return 0; }

    if [[ -n "$component" ]]; then
        jq -cs --arg c "$component" '[.[] | select(.component == $c)]' "$file"
    else
        jq -cs '.' "$file"
    fi
}

# --- transaction lifecycle -------------------------------------------------

# Components that began but never ended in an unfinished transaction.
tx_unfinished() {
    local root dir
    root="$(_tx_state_dir)/transactions"
    [[ -d "$root" ]] || return 0
    for dir in "$root"/*/; do
        [[ -d "$dir" ]] || continue
        [[ -f "$dir/journal.jsonl" && ! -f "$dir/result.json" ]] &&
            basename -- "$dir"
    done
    return 0
}

# Remove finished transactions older than the retention period.
_tx_retention() {
    local root dir name
    root="$(_tx_state_dir)/transactions"
    [[ -d "$root" ]] || return 0
    while IFS= read -r dir; do
        name="$(basename -- "$dir")"
        [[ "$name" =~ $DSSA_PLAN_ID_PATTERN && -f "$dir/result.json" && ! -L "$dir" ]] || continue
        rm -rf -- "$dir" && printf 'Removed transaction older than %s days: %s\n' \
            "$DSSA_TX_RETENTION_DAYS" "$name" >&2
    done < <(find "$root" -mindepth 1 -maxdepth 1 -type d -mtime +"$DSSA_TX_RETENTION_DAYS" 2>/dev/null)
}

# Usage: begin_transaction PLAN_ID MANAGER PATHS_JSON
# Acquires the run lock on fd 8 in the CURRENT shell (do not call in a
# command substitution), refuses while an unfinished run exists, then
# writes the whole-server snapshot. Prints nothing on success.
begin_transaction() {
    local plan_id="$1" manager="$2" paths_json="$3"
    local state tx unfinished packages units paths os_id os_version

    _tx_require_privilege || return 1
    _tx_valid_plan_id "$plan_id" || return 1
    [[ "$manager" == apt || "$manager" == dnf ]] || { _tx_fail "Unsupported package manager."; return 1; }
    jq -e 'type == "array" and all(.[]; type == "string" and startswith("/"))' \
        <<< "$paths_json" >/dev/null 2>&1 || { _tx_fail "Invalid snapshot path list."; return 1; }

    state="$(_tx_state_dir)"
    mkdir -p -- "$state/transactions" || return 1
    chmod 700 -- "$state" "$state/transactions" || return 1

    exec 8>"$state/run.lock" || return 1
    if ! flock -n 8; then
        exec 8>&-
        _tx_fail "Another DSSA run holds the lock on this server."
        return 1
    fi

    unfinished="$(tx_unfinished)"
    if [[ -n "$unfinished" ]]; then
        exec 8>&-
        _tx_fail "Unfinished transaction requires resume rollback: $unfinished"
        return 1
    fi

    tx="$(_tx_dir "$plan_id")"
    if [[ -e "$tx" ]]; then
        exec 8>&-
        _tx_fail "A transaction already exists for this plan."
        return 1
    fi

    _tx_retention
    mkdir -m 700 -- "$tx" "$tx/backups" "$tx/pkgsets" || { exec 8>&-; return 1; }

    packages="$(tx_package_set "$manager")" || { exec 8>&-; _tx_fail "Package snapshot failed."; return 1; }
    units="$(tx_unit_states)" || { exec 8>&-; _tx_fail "Unit snapshot failed."; return 1; }
    paths="$(tx_path_states "$paths_json")" || { exec 8>&-; _tx_fail "Path snapshot failed."; return 1; }
    os_id="$(read_os_release_field ID 2>/dev/null)" || os_id=""
    os_version="$(read_os_release_field VERSION_ID 2>/dev/null)" || os_version=""

    jq -n --arg plan_id "$plan_id" --arg at "$(_tx_now)" --arg manager "$manager" \
        --arg os_id "$os_id" --arg os_version "$os_version" \
        --argjson packages "$packages" --argjson units "$units" --argjson paths "$paths" \
        '{schema_version: "1.0", plan_id: $plan_id, taken_at: $at,
          package_manager: $manager, os: {id: $os_id, version: $os_version},
          packages: $packages, units: $units, paths: $paths}' \
        > "$tx/snapshot.json.tmp" || { exec 8>&-; return 1; }
    chmod 600 -- "$tx/snapshot.json.tmp" && mv -f -- "$tx/snapshot.json.tmp" "$tx/snapshot.json" ||
        { exec 8>&-; return 1; }
    chmod 600 -- "$tx/snapshot.json"
    sync -- "$tx/snapshot.json"

    tx_journal "$plan_id" "$(jq -cn '{phase: "transaction_begin"}')" || { exec 8>&-; return 1; }
}

# Usage: end_transaction PLAN_ID RESULTS_JSON
end_transaction() {
    local plan_id="$1" results="$2" tx
    tx="$(_tx_dir "$plan_id")"

    tx_journal "$plan_id" "$(jq -cn '{phase: "transaction_end"}')" || return 1
    jq -n --arg plan_id "$plan_id" --arg at "$(_tx_now)" --argjson results "$results" \
        '{schema_version: "1.0", plan_id: $plan_id, finished_at: $at, results: $results}' \
        > "$tx/result.json" || return 1
    chmod 600 -- "$tx/result.json"
    sync -- "$tx/result.json"
    exec 8>&- 2>/dev/null || true
}

# --- step executors ----------------------------------------------------------
# Each executor journals intent (with pre-state) before acting, then
# done/failed. Return 0 on success, non-zero on failure.

_tx_step_entry() {
    # component step type phase [pre-json] [detail-json]
    jq -cn --arg c "$1" --argjson s "$2" --arg t "$3" --arg ph "$4" \
        --argjson pre "${5:-null}" --argjson detail "${6:-null}" \
        '{component: $c, step: $s, type: $t, phase: $ph}
         + (if $pre == null then {} else {pre: $pre} end)
         + (if $detail == null then {} else {detail: $detail} end)'
}

_tx_exec_metadata_refresh() {
    local plan_id="$1" component="$2" index="$3" manager="$4"
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh intent)" || return 1
    case "$manager" in
        apt) apt-get update >&2 ;;
        dnf) dnf makecache -y >&2 ;;
    esac || { tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh failed)"; return 1; }
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh done)"
}

_tx_exec_packages_install() {
    local plan_id="$1" component="$2" index="$3" manager="$4" step="$5"
    local tx before after status=0 upgraded
    local -a packages=()
    tx="$(_tx_dir "$plan_id")"

    mapfile -t packages < <(jq -r '.packages[]' <<< "$step")
    before="$tx/pkgsets/$component-$index.before.json"
    after="$tx/pkgsets/$component-$index.after.json"

    (umask 077 && tx_package_set "$manager" > "$before") || { _tx_fail "Cannot record package state."; return 1; }
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install intent \
        "$(jq -cn --arg b "${before##*/}" --argjson p "$(printf '%s\n' "${packages[@]}" | jq -R . | jq -cs .)" \
            '{packages_before: $b, packages: $p}')")" || return 1

    case "$manager" in
        apt) DEBIAN_FRONTEND=noninteractive apt-get install -y -- "${packages[@]}" >&2 ;;
        dnf) dnf install -y -- "${packages[@]}" >&2 ;;
    esac || status=$?

    # Record what actually changed, even after a partial failure.
    (umask 077 && tx_package_set "$manager" > "$after") || status=1
    upgraded="$(jq -cn --slurpfile b "$before" --slurpfile a "$after" \
        '[$b[0] | to_entries[] | select($a[0][.key] != null and $a[0][.key] != .value)
          | {package: .key, before: .value, after: $a[0][.key]}]')" || upgraded='[]'

    if (( status != 0 )); then
        tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install failed null \
            "$(jq -cn --arg a "${after##*/}" --argjson u "$upgraded" '{packages_after: $a, upgraded: $u}')")"
        return 1
    fi
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install done null \
        "$(jq -cn --arg a "${after##*/}" --argjson u "$upgraded" '{packages_after: $a, upgraded: $u}')")"
}

_tx_backup_pre() {
    # Record a path's pre-state; back it up if it exists. Prints JSON.
    local plan_id="$1" path="$2" tag="$3"
    local real backup
    real="$(_tx_path "$path")"
    if [[ -L "$real" ]]; then
        _tx_fail "Refusing to manage a symlinked path: $path"
        return 1
    elif [[ -f "$real" ]]; then
        backup="$(_tx_dir "$plan_id")/backups/$tag"
        cp -p -- "$real" "$backup" && chmod go-rwx -- "$backup" || return 1
        jq -cn --arg p "$path" --arg b "$tag" '{path: $p, state: "file", backup: $b}'
    elif [[ -e "$real" ]]; then
        _tx_fail "Refusing to replace a non-file path: $path"
        return 1
    else
        jq -cn --arg p "$path" '{path: $p, state: "absent"}'
    fi
}

_tx_exec_apt_vendor_repository() {
    local plan_id="$1" component="$2" index="$3" manager="$4" step="$5"
    local key_url fingerprint key_path source_path keyring_dir pre_key pre_source pre_dir
    local tmp actual key_real source_real

    [[ "$manager" == apt ]] || { _tx_fail "APT repository on non-APT system."; return 1; }
    key_url="$(jq -r .key_url <<< "$step")"
    fingerprint="$(jq -r .key_fingerprint <<< "$step")"
    key_path="$(jq -r .key_path <<< "$step")"
    source_path="$(jq -r .source_path <<< "$step")"
    keyring_dir="$(dirname -- "$key_path")"

    pre_key="$(_tx_backup_pre "$plan_id" "$key_path" "$component-$index-key")" || return 1
    pre_source="$(_tx_backup_pre "$plan_id" "$source_path" "$component-$index-source")" || return 1
    if [[ -d "$(_tx_path "$keyring_dir")" ]]; then pre_dir=directory; else pre_dir=absent; fi

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" apt_vendor_repository intent \
        "$(jq -cn --argjson k "$pre_key" --argjson s "$pre_source" --arg d "$keyring_dir" --arg ds "$pre_dir" \
            '{key: $k, source: $s, keyring_dir: {path: $d, state: $ds}}')")" || return 1

    _repo_failed() {
        tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" apt_vendor_repository failed null \
            "$(jq -cn --arg r "$1" '{reason: $r}')")"
        _tx_fail "$1"
    }

    tmp="$(_tx_dir "$plan_id")/backups/$component-$index-download"
    (umask 077 && curl -fsSL --proto '=https' --connect-timeout 10 --max-time 60 -o "$tmp" -- "$key_url" >&2) ||
        { _repo_failed "Signing key download failed."; return 1; }
    actual="$(gpg --batch --show-keys --with-colons -- "$tmp" 2>/dev/null |
        awk -F: '$1 == "fpr" { print $10; exit }')"
    [[ "$actual" == "$fingerprint" ]] ||
        { _repo_failed "Signing key fingerprint mismatch."; return 1; }

    key_real="$(_tx_path "$key_path")"
    source_real="$(_tx_path "$source_path")"
    install -d -m 0755 -- "$(_tx_path "$keyring_dir")" ||
        { _repo_failed "Cannot create keyring directory."; return 1; }
    install -m 0644 -- "$tmp" "$key_real" ||
        { _repo_failed "Cannot install signing key."; return 1; }

    {
        printf 'Types: deb\n'
        printf 'URIs: %s\n' "$(jq -r .uris <<< "$step")"
        printf 'Suites: %s\n' "$(jq -r '.suites | join(" ")' <<< "$step")"
        printf 'Components: %s\n' "$(jq -r '.components | join(" ")' <<< "$step")"
        printf 'Architectures: %s\n' "$(jq -r '.architectures | join(" ")' <<< "$step")"
        printf 'Signed-By: %s\n' "$key_path"
    } > "$source_real.dssa-tmp" && chmod 0644 -- "$source_real.dssa-tmp" &&
        mv -f -- "$source_real.dssa-tmp" "$source_real" ||
        { rm -f -- "$source_real.dssa-tmp"; _repo_failed "Cannot write repository source."; return 1; }

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" apt_vendor_repository done)"
}

_tx_exec_service() {
    local plan_id="$1" component="$2" index="$3" kind="$4" step="$5"
    local unit pre
    unit="$(jq -r .unit <<< "$step")"

    if [[ "$kind" == service_enable ]]; then
        pre="$(tx_unit_file_state "$unit")"
    else
        pre="$(tx_unit_active_state "$unit")"
    fi
    [[ -n "$pre" ]] || { _tx_fail "Unknown pre-state for $unit."; return 1; }

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" "$kind" intent \
        "$(jq -cn --arg u "$unit" --arg s "$pre" '{unit: $u, state: $s}')")" || return 1

    if [[ "$kind" == service_enable ]]; then
        systemctl enable -- "$unit" >&2
    else
        systemctl start -- "$unit" >&2
    fi || { tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" "$kind" failed)"; return 1; }

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" "$kind" done)"
}

# --- verification -------------------------------------------------------------

tx_run_check() {
    local manager="$1" check="$2" type
    type="$(jq -r .type <<< "$check")"

    case "$type" in
        packages_installed)
            local pkg
            while IFS= read -r pkg; do
                package_is_installed "$manager" "$pkg" || { echo "Package not installed: $pkg" >&2; return 1; }
            done < <(jq -r '.packages[]' <<< "$check")
            ;;
        executable)
            local name
            name="$(jq -r .name <<< "$check")"
            command -v -- "$name" >/dev/null 2>&1 || { echo "Executable not found: $name" >&2; return 1; }
            if jq -e '.version_args' <<< "$check" >/dev/null; then
                local -a args=()
                mapfile -t args < <(jq -r '.version_args[]' <<< "$check")
                timeout 15 "$name" "${args[@]}" >&2 || return 1
            fi
            ;;
        service_active)
            systemctl is-active --quiet -- "$(jq -r .unit <<< "$check")" ;;
        service_enabled)
            systemctl is-enabled --quiet -- "$(jq -r .unit <<< "$check")" ;;
        tcp_listening)
            ss -H -ltn "sport = :$(jq -r .port <<< "$check")" 2>/dev/null | grep -q . ;;
        http_local)
            local port expect status
            port="$(jq -r .port <<< "$check")"
            expect="$(jq -r .expect_status <<< "$check")"
            status="$(timeout 5 bash -c '
                exec 3<>/dev/tcp/127.0.0.1/"$1" || exit 1
                printf "GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3
                IFS= read -r line <&3 || exit 1
                printf "%s\n" "$line"' _ "$port")" || return 1
            status="$(awk '{print $2}' <<< "${status%$'\r'}")"
            [[ "$expect" == *xx ]] && [[ "$status" == "${expect:0:1}"?? ]] && return 0
            [[ "$status" == "$expect" ]]
            ;;
        config_test)
            [[ "$(jq -r .command <<< "$check")" == "nginx -t" ]] || return 1
            nginx -t >&2 ;;
        hook)
            local hook
            hook="$(jq -r .name <<< "$check")"
            declare -F "$hook" >/dev/null || { echo "Verification hook not available: $hook" >&2; return 1; }
            "$hook" >&2 ;;
        *)
            return 1 ;;
    esac
}

# --- apply one component ------------------------------------------------------

# Usage: apply_component PLAN_ID MANAGER DEFINITION_JSON VARIANT_ID
# Runs inside an open transaction. Prints one result JSON.
# outcome: installed | verified | failed_rolled_back | rollback_incomplete
apply_component() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4"
    local component variant step type count index failed_step="" failure="" outcome
    local verify_failed=false rollback

    _tx_require_privilege || return 1
    [[ -f "$(_tx_dir "$plan_id")/snapshot.json" ]] || { _tx_fail "No open transaction."; return 1; }
    validate_component_definition "$definition" || return 1

    component="$(jq -r .id <<< "$definition")"
    variant="$(jq -c --arg v "$variant_id" '.variants[] | select(.variant_id == $v)' <<< "$definition")"
    [[ -n "$variant" ]] || { _tx_fail "Unknown variant: $variant_id"; return 1; }
    [[ "$(jq -r .platform.package_manager <<< "$variant")" == "$manager" ]] ||
        { _tx_fail "Variant does not match package manager."; return 1; }
    [[ "$(jq -r .support <<< "$variant")" == supported ]] ||
        { _tx_fail "Variant $variant_id is a draft; installation is not allowed."; return 1; }

    # Refuse before any change if a step type has no executor yet.
    while IFS= read -r type; do
        [[ "$DSSA_SUPPORTED_STEPS" == *" $type "* ]] ||
            { _tx_fail "Step type not supported by this engine: $type"; return 1; }
    done < <(jq -r '.steps[].type' <<< "$variant")

    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --arg v "$variant_id" \
        --argjson cp "$(jq -c .rollback.created_paths <<< "$variant")" \
        '{component: $c, phase: "component_begin", variant_id: $v, created_paths: $cp}')" || return 1

    count="$(jq '.steps | length' <<< "$variant")"
    for (( index = 0; index < count; index++ )); do
        step="$(jq -c --argjson i "$index" '.steps[$i]' <<< "$variant")"
        type="$(jq -r .type <<< "$step")"
        printf 'Component %s: step %d/%d %s\n' "$component" "$((index + 1))" "$count" "$type" >&2

        case "$type" in
            package_metadata_refresh) _tx_exec_metadata_refresh "$plan_id" "$component" "$index" "$manager" ;;
            packages_install) _tx_exec_packages_install "$plan_id" "$component" "$index" "$manager" "$step" ;;
            apt_vendor_repository) _tx_exec_apt_vendor_repository "$plan_id" "$component" "$index" "$manager" "$step" ;;
            service_enable|service_start) _tx_exec_service "$plan_id" "$component" "$index" "$type" "$step" ;;
        esac || { failed_step="$index"; failure="Step $((index + 1)) ($type) failed."; break; }
    done

    if [[ -z "$failed_step" ]]; then
        local check
        while IFS= read -r check; do
            if ! tx_run_check "$manager" "$check"; then
                verify_failed=true
                failure="Verification failed: $(jq -r .type <<< "$check")"
                break
            fi
        done < <(jq -c '.verification[]' <<< "$variant")
    fi

    if [[ -z "$failed_step" && "$verify_failed" == false ]]; then
        outcome=installed
        tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "component_end", outcome: "installed"}')" || return 1
        jq -cn --arg c "$component" --argjson up "$(_tx_upgraded "$plan_id" "$component")" \
            '{component: $c, outcome: "installed", verification: "passed", upgraded_not_reverted: $up}'
        return 0
    fi

    echo "Rolling back $component: $failure" >&2
    rollback="$(rollback_component "$plan_id" "$manager" "$component")" ||
        rollback='{"complete": false, "actions": [], "issues": ["Rollback could not run."]}'
    if jq -e '.complete == true' <<< "$rollback" >/dev/null 2>&1; then
        outcome=failed_rolled_back
    else
        outcome=rollback_incomplete
    fi
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --arg o "$outcome" \
        '{component: $c, phase: "component_end", outcome: $o}')" || true

    jq -cn --arg c "$component" --arg o "$outcome" --arg f "$failure" \
        --argjson rb "$rollback" --argjson up "$(_tx_upgraded "$plan_id" "$component")" \
        '{component: $c, outcome: $o, failure: $f, rollback: $rb, upgraded_not_reverted: $up}'
    [[ "$outcome" == failed_rolled_back ]] && return 3
    return 4
}

# Usage: verify_component MANAGER DEFINITION_JSON VARIANT_ID
# Read-only checks for a SKIP entry. Nothing was changed, so a failure is
# reported, never rolled back. Prints one result JSON.
verify_component() {
    local manager="$1" definition="$2" variant_id="$3" variant check component
    validate_component_definition "$definition" || return 1
    component="$(jq -r .id <<< "$definition")"
    variant="$(jq -c --arg v "$variant_id" '.variants[] | select(.variant_id == $v)' <<< "$definition")"
    [[ -n "$variant" ]] || { _tx_fail "Unknown variant: $variant_id"; return 1; }

    while IFS= read -r check; do
        if ! tx_run_check "$manager" "$check"; then
            jq -cn --arg c "$component" --arg t "$(jq -r .type <<< "$check")" \
                '{component: $c, outcome: "verification_failed", failure: "Verification failed: \($t)",
                  changes: "none"}'
            return 5
        fi
    done < <(jq -c '.verification[]' <<< "$variant")

    jq -cn --arg c "$component" '{component: $c, outcome: "verified", changes: "none"}'
}

_tx_upgraded() {
    tx_entries "$1" "$2" | jq -c '[.[] | select(.type == "packages_install" and .detail.upgraded != null)
                                   | .detail.upgraded[]] | unique_by(.package)'
}

# --- rollback -----------------------------------------------------------------

_tx_protected() {
    local path="${1%/}"
    [[ -z "$path" ]] && return 0
    [[ "$DSSA_PROTECTED_PATHS" == *" $path "* ]] && return 0
    # Require at least two path components below root.
    [[ "$(tr -cd '/' <<< "$path" | wc -c)" -lt 2 ]]
}

# Usage: rollback_component PLAN_ID MANAGER COMPONENT
# Works from the journal and snapshot alone. Prints one result JSON:
# {complete, actions: [...], issues: [...]}.
rollback_component() {
    local plan_id="$1" manager="$2" component="$3"
    local tx entries snapshot action ok=true
    local -a actions=() issues=()
    tx="$(_tx_dir "$plan_id")"
    snapshot="$tx/snapshot.json"

    _record() { actions+=("$(jq -cn --arg a "$1" --arg t "$2" --arg r "$3" '{action: $a, target: $t, result: $r}')"); }
    _issue() { ok=false; issues+=("$1"); _record "$2" "$3" failed; }

    [[ -f "$snapshot" ]] || { jq -cn '{complete: false, actions: [], issues: ["Snapshot missing; nothing undone."]}'; return 0; }
    entries="$(tx_entries "$plan_id" "$component")"
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" '{component: $c, phase: "rollback_begin"}')" || true

    # 1. Stop and disable units this component started or enabled, when
    #    they were not active/enabled before the run.
    local unit pre
    while IFS=$'\t' read -r unit pre; do
        [[ -n "$unit" ]] || continue
        if [[ "$pre" != active ]]; then
            if systemctl stop -- "$unit" >&2 2>/dev/null || [[ "$(tx_unit_file_state "$unit")" == not-found ]]; then
                _record stop "$unit" ok
            else
                _issue "Could not stop $unit." stop "$unit"
            fi
        fi
    done < <(jq -r '[.[] | select(.type == "service_start" and .phase == "intent")] | reverse[] | [.pre.unit, .pre.state] | @tsv' <<< "$entries")

    while IFS=$'\t' read -r unit pre; do
        [[ -n "$unit" ]] || continue
        if [[ "$pre" == not-found || "$pre" == disabled ]]; then
            if systemctl disable -- "$unit" >&2 2>/dev/null || [[ "$(tx_unit_file_state "$unit")" == not-found ]]; then
                _record disable "$unit" ok
            else
                _issue "Could not disable $unit." disable "$unit"
            fi
        fi
    done < <(jq -r '[.[] | select(.type == "service_enable" and .phase == "intent")] | reverse[] | [.pre.unit, .pre.state] | @tsv' <<< "$entries")

    # 2. Remove packages that are new since each install step began and
    #    were not installed when the run started.
    local current new_packages
    current="$(tx_package_set "$manager")" || current=""
    if [[ -z "$current" ]]; then
        _issue "Cannot read package state; packages not removed." remove_packages "(unknown)"
    else
        new_packages="$(
            jq -r --slurpfile snap "$snapshot" --argjson current "$current" --arg dir "$tx/pkgsets" '
                [.[] | select(.type == "packages_install" and .phase == "intent") | .pre.packages_before]
                | unique | .[]' <<< "$entries" |
            while IFS= read -r before_file; do
                [[ -n "$before_file" ]] || continue
                after_file="${before_file%.before.json}.after.json"
                if [[ -f "$tx/pkgsets/$after_file" ]]; then
                    jq -rn --slurpfile b "$tx/pkgsets/$before_file" --slurpfile a "$tx/pkgsets/$after_file" \
                        '$a[0] | keys[] | select($b[0][.] == null)'
                else
                    # Interrupted step: anything new since it began.
                    jq -rn --slurpfile b "$tx/pkgsets/$before_file" --argjson c "$current" \
                        '$c | keys[] | select($b[0][.] == null)'
                fi
            done | sort -u |
            jq -Rr --slurpfile snap "$snapshot" --argjson current "$current" \
                'select(length > 0 and $snap[0].packages[.] == null and $current[.] != null)'
        )"
        if [[ -n "$new_packages" ]]; then
            local -a remove=()
            mapfile -t remove <<< "$new_packages"
            local removed=true
            case "$manager" in
                apt) DEBIAN_FRONTEND=noninteractive apt-get purge -y -- "${remove[@]}" >&2 || removed=false ;;
                # Never let dnf remove "unneeded" dependencies: they may
                # have been installed before the run.
                dnf) dnf remove -y --setopt=clean_requirements_on_remove=False -- "${remove[@]}" >&2 ||
                         removed=false ;;
            esac
            current="$(tx_package_set "$manager")" || current='{}'
            local pkg
            for pkg in "${remove[@]}"; do
                if [[ "$(jq -r --arg p "$pkg" '.[$p] // empty' <<< "$current")" == "" ]]; then
                    _record remove_package "$pkg" ok
                else
                    _issue "Package still installed: $pkg" remove_package "$pkg"
                fi
            done
            [[ "$removed" == true ]] || { ok=false; issues+=("Package removal command failed."); }
        fi

        # Safety net: nothing that existed before the run may be missing.
        local missing
        current="$(tx_package_set "$manager")" || current='{}'
        missing="$(jq -rn --slurpfile snap "$snapshot" --argjson current "$current" \
            '$snap[0].packages | keys[] | select($current[.] == null)')"
        while IFS= read -r pkg; do
            [[ -n "$pkg" ]] || continue
            _issue "Pre-existing package is missing after rollback: $pkg" check_preexisting "$pkg"
        done <<< "$missing"
    fi

    # 3. Repository files: remove if absent before, restore a backup if not.
    local file_entry path state backup
    while IFS= read -r file_entry; do
        [[ -n "$file_entry" ]] || continue
        path="$(jq -r .path <<< "$file_entry")"
        state="$(jq -r .state <<< "$file_entry")"
        backup="$(jq -r '.backup // empty' <<< "$file_entry")"
        if [[ "$state" == absent ]]; then
            if rm -f -- "$(_tx_path "$path")"; then _record remove_file "$path" ok
            else _issue "Could not remove $path." remove_file "$path"; fi
        elif [[ -n "$backup" ]]; then
            if cp -p -- "$tx/backups/$backup" "$(_tx_path "$path")"; then _record restore_file "$path" ok
            else _issue "Could not restore $path." restore_file "$path"; fi
        fi
    done < <(jq -c '[.[] | select(.type == "apt_vendor_repository" and .phase == "intent")] | reverse[]
                    | .pre.source, .pre.key' <<< "$entries")

    # Keyring directories that did not exist before: remove only if empty.
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        if [[ -d "$(_tx_path "$path")" ]] && ! _tx_protected "$path"; then
            if rmdir -- "$(_tx_path "$path")" 2>/dev/null; then _record remove_directory "$path" ok
            else _issue "Directory $path is not empty; left in place." remove_directory "$path"; fi
        fi
    done < <(jq -r '[.[] | select(.type == "apt_vendor_repository" and .phase == "intent"
                     and .pre.keyring_dir.state == "absent") | .pre.keyring_dir.path] | unique[]' <<< "$entries")

    # 4. Created paths that did not exist when the run started.
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        local snap_state real
        snap_state="$(jq -r --arg p "$path" '.paths[$p] // "unknown"' "$snapshot")"
        real="$(_tx_path "$path")"
        [[ -e "$real" || -L "$real" ]] || continue
        if [[ "$snap_state" != absent ]]; then
            [[ "$snap_state" == unknown ]] &&
                _issue "Pre-run state of $path unknown; left in place." remove_path "$path"
            continue
        fi
        if _tx_protected "$path"; then
            _issue "Refusing to remove protected path $path." remove_path "$path"
            continue
        fi
        if rm -rf --one-file-system -- "$real"; then _record remove_path "$path" ok
        else _issue "Could not remove $path." remove_path "$path"; fi
    done < <(jq -r '[.[] | select(.phase == "component_begin") | .created_paths[]] | unique[]' <<< "$entries")

    # 5. Managed files: no executor yet, so nothing to restore.

    # 6. Reload unit definitions after package and file changes.
    if systemctl daemon-reload >&2 2>/dev/null; then _record daemon_reload systemd ok
    else _issue "systemctl daemon-reload failed." daemon_reload systemd; fi

    # 7. Only after a clean undo: restore units that existed before.
    if [[ "$ok" == true ]]; then
        while IFS=$'\t' read -r unit pre; do
            [[ -n "$unit" ]] || continue
            case "$pre" in
                enabled) systemctl enable -- "$unit" >&2 && _record restore_enabled "$unit" ok ||
                             _issue "Could not re-enable $unit." restore_enabled "$unit" ;;
            esac
        done < <(jq -r '[.[] | select(.type == "service_enable" and .phase == "intent")] | .[] | [.pre.unit, .pre.state] | @tsv' <<< "$entries")
        while IFS=$'\t' read -r unit pre; do
            [[ -n "$unit" && "$pre" == active ]] || continue
            [[ "$(tx_unit_file_state "$unit")" != not-found ]] || continue
            systemctl start -- "$unit" >&2 && _record restore_active "$unit" ok ||
                _issue "Could not restart $unit." restore_active "$unit"
        done < <(jq -r '[.[] | select(.type == "service_start" and .phase == "intent")] | .[] | [.pre.unit, .pre.state] | @tsv' <<< "$entries")
    else
        issues+=("Service state restoration skipped: rollback is incomplete.")
    fi

    local result
    result="$(jq -cn --argjson complete "$ok" \
        --argjson actions "$(printf '%s\n' "${actions[@]}" | jq -cs 'map(select(. != null))')" \
        --argjson issues "$(printf '%s\n' "${issues[@]}" | jq -R . | jq -cs 'map(select(length > 0))')" \
        '{complete: $complete, actions: $actions, issues: $issues}')"
    tx_journal "$plan_id" "$(jq -cn --arg c "$component" --argjson r "$result" \
        '{component: $c, phase: "rollback_end", result: $r}')" || true
    printf '%s\n' "$result"
}

# Usage: resume_rollback PLAN_ID MANAGER
# Rolls back every component of an unfinished transaction that began but
# never ended, then closes the transaction. Prints one result JSON.
resume_rollback() {
    local plan_id="$1" manager="$2" component results='[]' result
    _tx_require_privilege || return 1
    _tx_valid_plan_id "$plan_id" || return 1
    [[ -f "$(_tx_dir "$plan_id")/journal.jsonl" ]] || { _tx_fail "No such transaction."; return 1; }
    [[ ! -f "$(_tx_dir "$plan_id")/result.json" ]] || { _tx_fail "Transaction already finished."; return 1; }

    exec 8>"$(_tx_state_dir)/run.lock" || return 1
    flock -n 8 || { exec 8>&-; _tx_fail "Another DSSA run holds the lock on this server."; return 1; }

    while IFS= read -r component; do
        [[ -n "$component" ]] || continue
        result="$(rollback_component "$plan_id" "$manager" "$component")" ||
            result='{"complete": false, "actions": [], "issues": ["Rollback could not run."]}'
        results="$(jq -c --arg c "$component" --argjson r "$result" \
            '. + [{component: $c, outcome: (if $r.complete then "failed_rolled_back" else "rollback_incomplete" end),
                   failure: "Run was interrupted.", rollback: $r}]' <<< "$results")"
        tx_journal "$plan_id" "$(jq -cn --arg c "$component" \
            --arg o "$(jq -r 'if .complete then "failed_rolled_back" else "rollback_incomplete" end' <<< "$result")" \
            '{component: $c, phase: "component_end", outcome: $o}')" || true
    done < <(tx_entries "$plan_id" | jq -r '
        ([.[] | select(.phase == "component_begin") | .component]) as $began
        | ([.[] | select(.phase == "component_end") | .component]) as $ended
        | $began[] | select(. as $c | $ended | index($c) | not)')

    end_transaction "$plan_id" "$results" || return 1
    jq -cn --arg p "$plan_id" --argjson r "$results" '{plan_id: $p, resumed: true, results: $r}'
}
