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
#     remove repository files, remove created paths, reload firewalld
#     when its saved config changed, daemon-reload, and
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
DSSA_SUPPORTED_STEPS=" package_metadata_refresh packages_install apt_vendor_repository dnf_vendor_repository firewalld_reload service_enable service_start "
# Hooks a definition may name. Hook files (engine/hooks/*.sh) register
# theirs here, so a definition can never call an arbitrary engine function.
DSSA_HOOKS="${DSSA_HOOKS:- }"
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

# Every installed version, one "name.arch<TAB>epoch:version-release" line
# each, sorted (4.36, dnf only). Unlike tx_package_set this keeps
# packages installed side by side, such as several kernels. apt has one
# version per package name, so it prints nothing there.
tx_package_versions() {
    [[ "$1" == dnf ]] || return 0
    rpm -qa --qf '%{NAME}.%{ARCH}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n' 2>/dev/null |
        grep -v '^gpg-pubkey\.' | LC_ALL=C sort -u
}

# Versions added side by side: in AFTER but not BEFORE, for a name.arch
# that BEFORE already had, while every BEFORE version of it is still
# installed (a new kernel next to the old one, not an upgrade).
# Usage: _tx_added_versions BEFORE_FILE AFTER_FILE; prints JSON
# [{package, version}].
_tx_added_versions() {
    jq -cn --rawfile b "$1" --rawfile a "$2" '
        def lines: split("\n") | map(select(length > 0));
        ($b | lines) as $B | ($a | lines) as $A
        | (reduce $A[] as $l ({}; .[$l] = true)) as $inA
        | (reduce $B[] as $l ({}; .[$l] = true)) as $inB
        | (reduce $B[] as $l ({}; .[$l | split("\t")[0]] += [$l])) as $old
        | [$A[] | select($inB[.] != true)
           | split("\t") as [$n, $v]
           | select($old[$n] != null and all($old[$n][]; $inA[.] == true))
           | {package: $n, version: $v}]'
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

# Every apt-get the engine runs (4.37). apt waits for a held dpkg lock
# (unattended-upgrades, another admin) forever by default; DSSA waits at
# most DSSA_APT_LOCK_TIMEOUT seconds, then the step fails and rolls back.
# "update" also gets --error-on=any from its callers: without it apt-get
# exits 0 when a repository's signature cannot be verified and the old
# lists are used.
_tx_apt() {
    local wait="${DSSA_APT_LOCK_TIMEOUT:-60}"
    [[ "$wait" =~ ^[0-9]{1,4}$ ]] || wait=60
    apt-get -o "DPkg::Lock::Timeout=$wait" "$@"
}

_tx_exec_metadata_refresh() {
    local plan_id="$1" component="$2" index="$3" manager="$4"
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh intent)" || return 1
    case "$manager" in
        apt) _tx_apt update --error-on=any >&2 ;;
        dnf) dnf makecache -y >&2 ;;
    esac || { tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh failed)"; return 1; }
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" package_metadata_refresh done)"
}

_tx_exec_packages_install() {
    local plan_id="$1" component="$2" index="$3" manager="$4" step="$5"
    local tx before after status=0 upgraded package
    local -a packages=() missing=() present=()
    tx="$(_tx_dir "$plan_id")"

    mapfile -t packages < <(jq -r '.packages[]' <<< "$step")

    # Packages already installed are left untouched: asking the package
    # manager for them again would change state the snapshot does not
    # record (apt marks them "manually installed"; it may also upgrade).
    for package in "${packages[@]}"; do
        if package_is_installed "$manager" "$package" >/dev/null 2>&1; then
            present+=("$package")
        else
            missing+=("$package")
        fi
    done
    if (( ${#present[@]} > 0 )); then
        printf 'Already installed, left untouched: %s\n' "${present[*]}" >&2
    fi
    before="$tx/pkgsets/$component-$index.before.json"
    after="$tx/pkgsets/$component-$index.after.json"
    local before_versions="${before%.json}.versions" after_versions="${after%.json}.versions" added='[]'

    (umask 077 && tx_package_set "$manager" > "$before") || { _tx_fail "Cannot record package state."; return 1; }
    (umask 077 && tx_package_versions "$manager" > "$before_versions") || { _tx_fail "Cannot record package versions."; return 1; }
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install intent \
        "$(jq -cn --arg b "${before##*/}" \
            --argjson p "$(printf '%s\n' "${packages[@]}" | jq -R . | jq -cs .)" \
            --argjson present "$(printf '%s\n' "${present[@]}" | jq -R 'select(length > 0)' | jq -cs .)" \
            '{packages_before: $b, packages: $p, already_installed: $present}')")" || return 1

    if (( ${#missing[@]} > 0 )); then
        case "$manager" in
            apt) DEBIAN_FRONTEND=noninteractive _tx_apt install -y -- "${missing[@]}" >&2 ;;
            dnf) dnf install -y -- "${missing[@]}" >&2 ;;
        esac || status=$?
    fi

    # Record what actually changed, even after a partial failure.
    (umask 077 && tx_package_set "$manager" > "$after") || status=1
    (umask 077 && tx_package_versions "$manager" > "$after_versions") || status=1
    added="$(_tx_added_versions "$before_versions" "$after_versions")" || added='[]'
    # A version added side by side is not an upgrade: rollback removes it.
    upgraded="$(jq -cn --slurpfile b "$before" --slurpfile a "$after" --argjson added "$added" \
        '[$b[0] | to_entries[] | select($a[0][.key] != null and $a[0][.key] != .value)
          | select(.key as $k | $added | any(.package == $k) | not)
          | {package: .key, before: .value, after: $a[0][.key]}]')" || upgraded='[]'

    if (( status != 0 )); then
        tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install failed null \
            "$(jq -cn --arg a "${after##*/}" --argjson u "$upgraded" --argjson v "$added" \
                '{packages_after: $a, upgraded: $u, added_versions: $v}')")"
        return 1
    fi
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" packages_install done null \
        "$(jq -cn --arg a "${after##*/}" --argjson u "$upgraded" --argjson v "$added" \
            '{packages_after: $a, upgraded: $u, added_versions: $v}')")"
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

# rpm's imported signing keys (gpg-pubkey entries) as a JSON array. The
# package snapshot excludes them, so vendor-repository steps record them.
tx_rpm_keys() {
    local out
    out="$(rpm -q gpg-pubkey --qf '%{NAME}-%{VERSION}-%{RELEASE}\n' 2>/dev/null)" || out=""
    { grep -E '^gpg-pubkey-[0-9a-f]+-[0-9a-f]+$' <<< "$out" || true; } | sort -u | jq -R . | jq -cs .
}

# dnf vendor repository (4.36). DSSA writes its own one-stanza repo
# file (never the vendor's downloaded .repo), so exactly what is trusted
# is in the definition and the journal. dnf imports the checked key into
# rpm at the first install; rollback removes keys that are new.
_tx_exec_dnf_vendor_repository() {
    local plan_id="$1" component="$2" index="$3" manager="$4" step="$5"
    local repo_id baseurl key_url fingerprint key_path repo_path key_dir
    local pre_key pre_repo pre_dir rpm_keys tmp actual key_real repo_real

    [[ "$manager" == dnf ]] || { _tx_fail "DNF repository on non-DNF system."; return 1; }
    repo_id="$(jq -r .repository_id <<< "$step")"
    baseurl="$(jq -r .baseurl <<< "$step")"
    key_url="$(jq -r .key_url <<< "$step")"
    fingerprint="$(jq -r .key_fingerprint <<< "$step")"
    key_path="$(jq -r .key_path <<< "$step")"
    repo_path="$(jq -r .repo_path <<< "$step")"
    key_dir="$(dirname -- "$key_path")"

    # Every dnf system has its repo folder; DSSA never creates it.
    [[ -d "$(_tx_path "$(dirname -- "$repo_path")")" ]] ||
        { _tx_fail "Repository folder $(dirname -- "$repo_path") is missing; not a normal dnf system."; return 1; }

    pre_key="$(_tx_backup_pre "$plan_id" "$key_path" "$component-$index-key")" || return 1
    pre_repo="$(_tx_backup_pre "$plan_id" "$repo_path" "$component-$index-repo")" || return 1
    if [[ -d "$(_tx_path "$key_dir")" ]]; then pre_dir=directory; else pre_dir=absent; fi
    rpm_keys="$(tx_rpm_keys)" || { _tx_fail "Cannot read rpm signing keys."; return 1; }

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" dnf_vendor_repository intent \
        "$(jq -cn --argjson k "$pre_key" --argjson r "$pre_repo" --arg d "$key_dir" --arg ds "$pre_dir" \
            --argjson keys "$rpm_keys" \
            '{key: $k, source: $r, keyring_dir: {path: $d, state: $ds}, rpm_keys: $keys}')")" || return 1

    _dnf_repo_failed() {
        tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" dnf_vendor_repository failed null \
            "$(jq -cn --arg r "$1" '{reason: $r}')")"
        _tx_fail "$1"
    }

    tmp="$(_tx_dir "$plan_id")/backups/$component-$index-download"
    (umask 077 && curl -fsSL --proto '=https' --connect-timeout 10 --max-time 60 -o "$tmp" -- "$key_url" >&2) ||
        { _dnf_repo_failed "Signing key download failed."; return 1; }
    actual="$(gpg --batch --show-keys --with-colons -- "$tmp" 2>/dev/null |
        awk -F: '$1 == "fpr" { print $10; exit }')"
    [[ "$actual" == "$fingerprint" ]] ||
        { _dnf_repo_failed "Signing key fingerprint mismatch."; return 1; }

    key_real="$(_tx_path "$key_path")"
    repo_real="$(_tx_path "$repo_path")"
    install -d -m 0755 -- "$(_tx_path "$key_dir")" ||
        { _dnf_repo_failed "Cannot create key directory."; return 1; }
    install -m 0644 -- "$tmp" "$key_real" ||
        { _dnf_repo_failed "Cannot install signing key."; return 1; }

    {
        printf '[%s]\n' "$repo_id"
        printf 'name=%s (managed by DSSA)\n' "$repo_id"
        printf 'baseurl=%s\n' "$baseurl"
        printf 'enabled=1\n'
        printf 'gpgcheck=1\n'
        printf 'gpgkey=file://%s\n' "$key_path"
    } > "$repo_real.dssa-tmp" && chmod 0644 -- "$repo_real.dssa-tmp" &&
        mv -f -- "$repo_real.dssa-tmp" "$repo_real" ||
        { rm -f -- "$repo_real.dssa-tmp"; _dnf_repo_failed "Cannot write repository file."; return 1; }

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" dnf_vendor_repository done)"
}

# firewalld reads which iptables backends exist only when it starts or
# reloads. When this run installed one of the listed packages (iptables-nft
# for Docker on EL) while firewalld was running, reload it so the next
# service can use firewalld's direct rules (4.36). The reload is not
# undone: it only loads firewalld's saved configuration.
_tx_exec_firewalld_reload() {
    local plan_id="$1" component="$2" index="$3" manager="$4" step="$5"
    local snapshot active=inactive reload=false pkg
    local -a added=()
    snapshot="$(_tx_dir "$plan_id")/snapshot.json"

    systemctl is-active --quiet firewalld.service 2>/dev/null && active=active
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] || continue
        jq -e --arg p "$pkg" '.packages | has($p) | not' "$snapshot" >/dev/null 2>&1 || continue
        package_is_installed "$manager" "$pkg" >/dev/null 2>&1 && added+=("$pkg")
    done < <(jq -r '.when_packages_added[]' <<< "$step")
    [[ "$active" == active && ${#added[@]} -gt 0 ]] && reload=true

    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" firewalld_reload intent \
        "$(jq -cn --arg s "$active" --argjson r "$reload" \
            --argjson a "$(printf '%s\n' "${added[@]}" | jq -R 'select(length > 0)' | jq -cs .)" \
            '{firewalld: $s, packages_added: $a, reload: $r}')")" || return 1

    if [[ "$reload" == true ]]; then
        printf 'firewalld: reloading so it sees the newly installed %s\n' "${added[*]}" >&2
        firewall-cmd --reload >&2 ||
            { tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" firewalld_reload failed)"; return 1; }
    elif [[ "$active" != active ]]; then
        echo "firewalld: not running; no reload needed" >&2
    else
        echo "firewalld: no listed package was added by this run; no reload needed" >&2
    fi
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" firewalld_reload done)"
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

    local detail="null"
    if [[ "$kind" == service_start ]]; then
        detail="$(_tx_service_settle "$unit")" || {
            tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" "$kind" failed null "${detail:-null}")"
            return 1
        }
    fi
    tx_journal "$plan_id" "$(_tx_step_entry "$component" "$index" "$kind" done null "$detail")"
}

# A started service must stay up (4.37). "systemctl start" returns once
# the unit is up, and a single is-active check right after it passes a
# crash loop: units with Restart= (docker.service has Restart=always)
# come back each time. For DSSA_SERVICE_SETTLE_SECONDS (default 15) the
# unit must stay active/running with the same main process and the same
# NRestarts count. A oneshot unit has no main process; it only has to
# stay active. Prints the result as JSON; on failure the unit's last
# journal lines go to stderr, which the run log keeps for RCA.
_tx_service_settle() {
    local unit="$1" settle="${DSSA_SERVICE_SETTLE_SECONDS:-15}"
    local type pid restarts state now_pid now_restarts elapsed=0 reason=""
    [[ "$settle" =~ ^[0-9]{1,3}$ ]] || settle=15

    _prop() { systemctl show --property="$1" --value -- "$unit" 2>/dev/null; }
    type="$(_prop Type)"
    pid="$(_prop MainPID)"
    restarts="$(_prop NRestarts)"
    [[ "$restarts" =~ ^[0-9]+$ ]] || restarts=0

    if [[ "$type" != oneshot && ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
        reason="no main process after start"
    fi
    while [[ -z "$reason" ]]; do
        state="$(_prop ActiveState)/$(_prop SubState)"
        now_pid="$(_prop MainPID)"
        now_restarts="$(_prop NRestarts)"
        [[ "$now_restarts" =~ ^[0-9]+$ ]] || now_restarts=0
        if [[ "$type" == oneshot ]]; then
            [[ "$state" == active/* ]] || reason="state $state"
        elif [[ "$state" != active/running ]]; then
            reason="state $state"
        elif [[ "$now_pid" != "$pid" ]]; then
            reason="main process changed $pid -> $now_pid"
        fi
        if [[ -z "$reason" && "$now_restarts" != "$restarts" ]]; then
            reason="restarted $restarts -> $now_restarts"
        fi
        (( elapsed >= settle )) && break
        [[ -n "$reason" ]] && break
        sleep 1
        elapsed=$((elapsed + 1))
    done
    unset -f _prop

    if [[ -n "$reason" ]]; then
        printf 'Service %s is not stable after %ss: %s\n' "$unit" "$elapsed" "$reason" >&2
        printf 'Recent journal for %s:\n' "$unit" >&2
        journalctl --unit="$unit" --lines=20 --no-pager >&2 2>/dev/null ||
            echo "(journal could not be read)" >&2
        jq -cn --arg u "$unit" --argjson s "$elapsed" --arg r "$reason" \
            '{stable: false, unit: $u, seconds: $s, reason: $r}'
        return 1
    fi
    jq -cn --arg u "$unit" --argjson s "$settle" --arg p "$pid" --argjson n "$restarts" \
        '{stable: true, unit: $u, seconds: $s, main_pid: $p, restarts: $n}'
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
            [[ "$DSSA_HOOKS" == *" $hook "* ]] && declare -F "$hook" >/dev/null ||
                { echo "Verification hook not available: $hook" >&2; return 1; }
            "$hook" >&2 ;;
        *)
            return 1 ;;
    esac
}

# --- apply one component ------------------------------------------------------

# Usage: apply_component PLAN_ID MANAGER DEFINITION_JSON VARIANT_ID [ALLOW_DRAFT]
# Runs inside an open transaction. Prints one result JSON.
# outcome: installed | verified | failed_rolled_back | rollback_incomplete
apply_component() {
    local plan_id="$1" manager="$2" definition="$3" variant_id="$4" allow_draft="${5:-false}"
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
    # Draft variants run only when the approved plan allows them (4.34,
    # lab targets only); otherwise they are refused.
    [[ "$(jq -r .support <<< "$variant")" == supported || "$allow_draft" == true ]] ||
        { _tx_fail "Variant $variant_id is a draft; installation is not allowed."; return 1; }
    jq -e '(.hooks.precheck // null) == null' <<< "$variant" >/dev/null ||
        { _tx_fail "Precheck hooks are not supported by this engine."; return 1; }

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
            dnf_vendor_repository) _tx_exec_dnf_vendor_repository "$plan_id" "$component" "$index" "$manager" "$step" ;;
            firewalld_reload) _tx_exec_firewalld_reload "$plan_id" "$component" "$index" "$manager" "$step" ;;
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

        # 2b. dnf: versions installed side by side during the run (a new
        #     kernel next to the old one), never the running kernel (4.36).
        local -a remove=() remove_versions=() version_labels=()
        [[ -n "$new_packages" ]] && mapfile -t remove <<< "$new_packages"
        if [[ "$manager" == dnf ]]; then
            local versions_now="$tx/pkgsets/rollback-$component.now.versions" before_file vfile afile
            local running name_arch version label
            running="$(uname -r 2>/dev/null)" || running=""
            (umask 077 && tx_package_versions dnf > "$versions_now") || : > "$versions_now"
            while IFS=$'\t' read -r name_arch version; do
                [[ -n "$name_arch" ]] || continue
                label="${name_arch%.*}-${version}.${name_arch##*.}"
                if [[ -n "$running" && "${version#*:}.${name_arch##*.}" == "$running" ]]; then
                    _issue "Not removing $label: it is the running kernel." remove_version "$label"
                    continue
                fi
                remove_versions+=("$label")
                version_labels+=("$name_arch"$'\t'"$version")
            done < <(
                jq -r '[.[] | select(.type == "packages_install" and .phase == "intent") | .pre.packages_before]
                       | unique | .[]' <<< "$entries" |
                while IFS= read -r before_file; do
                    vfile="$tx/pkgsets/${before_file%.json}.versions"
                    [[ -f "$vfile" ]] || continue   # journal from before 4.36
                    afile="$tx/pkgsets/${before_file%.before.json}.after.versions"
                    [[ -f "$afile" ]] || afile="$versions_now"   # interrupted step
                    _tx_added_versions "$vfile" "$afile"
                done | jq -rs --rawfile now "$versions_now" '
                    ($now | split("\n") | map(select(length > 0))
                     | reduce .[] as $l ({}; .[$l] = true)) as $installed
                    | [.[][]] | unique[]
                    | select($installed[.package + "\t" + .version] == true)
                    | [.package, .version] | @tsv'
            )
        fi

        if (( ${#remove[@]} > 0 || ${#remove_versions[@]} > 0 )); then
            local removed=true
            case "$manager" in
                apt) DEBIAN_FRONTEND=noninteractive _tx_apt purge -y -- "${remove[@]}" >&2 || removed=false ;;
                # Never let dnf remove "unneeded" dependencies: they may
                # have been installed before the run.
                dnf) dnf remove -y --setopt=clean_requirements_on_remove=False -- \
                         "${remove[@]}" "${remove_versions[@]}" >&2 || removed=false ;;
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
            if (( ${#remove_versions[@]} > 0 )); then
                local now_text i
                now_text="$(tx_package_versions dnf)" || now_text=""
                for i in "${!remove_versions[@]}"; do
                    if grep -qxF -- "${version_labels[$i]}" <<< "$now_text"; then
                        _issue "Version still installed: ${remove_versions[$i]}" remove_version "${remove_versions[$i]}"
                    else
                        _record remove_version "${remove_versions[$i]}" ok
                    fi
                done
            fi
            if [[ "$removed" != true ]]; then
                ok=false
                issues+=("Package removal command failed.")
                # DSSA never repairs the package database itself (4.35 B).
                [[ "$manager" == apt ]] && issues+=("If dpkg was interrupted, run 'sudo dpkg --configure -a' on the server, then remove the packages listed above by hand.")
            fi
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
    local file_entry path state backup repo_changed=false
    while IFS= read -r file_entry; do
        [[ -n "$file_entry" ]] || continue
        path="$(jq -r .path <<< "$file_entry")"
        state="$(jq -r .state <<< "$file_entry")"
        backup="$(jq -r '.backup // empty' <<< "$file_entry")"
        if [[ "$state" == absent ]]; then
            if rm -f -- "$(_tx_path "$path")"; then _record remove_file "$path" ok; repo_changed=true
            else _issue "Could not remove $path." remove_file "$path"; fi
        elif [[ -n "$backup" ]]; then
            if cp -p -- "$tx/backups/$backup" "$(_tx_path "$path")"; then _record restore_file "$path" ok; repo_changed=true
            else _issue "Could not restore $path." restore_file "$path"; fi
        fi
    done < <(jq -c '[.[] | select((.type == "apt_vendor_repository" or .type == "dnf_vendor_repository")
                                  and .phase == "intent")] | reverse[]
                    | .pre.source, .pre.key' <<< "$entries")

    # Keyring directories that did not exist before: remove only if empty.
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        if [[ -d "$(_tx_path "$path")" ]] && ! _tx_protected "$path"; then
            if rmdir -- "$(_tx_path "$path")" 2>/dev/null; then _record remove_directory "$path" ok
            else _issue "Directory $path is not empty; left in place." remove_directory "$path"; fi
        fi
    done < <(jq -r '[.[] | select((.type == "apt_vendor_repository" or .type == "dnf_vendor_repository")
                     and .phase == "intent" and .pre.keyring_dir.state == "absent")
                     | .pre.keyring_dir.path] | unique[]' <<< "$entries")

    # rpm signing keys imported since the first dnf vendor step (4.36 B).
    local keys_before keys_now key
    keys_before="$(jq -c '[.[] | select(.type == "dnf_vendor_repository" and .phase == "intent")]
                          | if length == 0 then null else .[0].pre.rpm_keys end' <<< "$entries")"
    if [[ "$keys_before" != null ]]; then
        if keys_now="$(tx_rpm_keys)"; then
            while IFS= read -r key; do
                [[ "$key" =~ ^gpg-pubkey-[0-9a-f]+-[0-9a-f]+$ ]] || continue
                if rpm -e "$key" >&2; then _record remove_rpm_key "$key" ok
                else _issue "Could not remove rpm signing key $key." remove_rpm_key "$key"; fi
            done < <(jq -rn --argjson b "$keys_before" --argjson n "$keys_now" '$n - $b | .[]')
        else
            _issue "Cannot read rpm signing keys; imported keys not removed." remove_rpm_key "(unknown)"
        fi
    fi

    # Package lists from a repository that is gone stay until the next
    # update (4.35 D). They are a cache, not installed state: a failed
    # refresh is reported but does not make the rollback incomplete.
    if [[ "$repo_changed" == true && "$manager" == apt ]]; then
        if _tx_apt update --error-on=any >&2; then
            _record refresh_lists apt ok
        else
            _record refresh_lists apt "not refreshed"
            issues+=("Package lists not refreshed (apt-get update failed); run it when the server is online.")
        fi
    fi

    # 4. Created paths that did not exist when the run started.
    local firewalld_changed=false
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
        if rm -rf --one-file-system -- "$real"; then
            _record remove_path "$path" ok
            [[ "$path" == /etc/firewalld/* ]] && firewalld_changed=true
        else _issue "Could not remove $path." remove_path "$path"; fi
    done < <(jq -r '[.[] | select(.phase == "component_begin") | .created_paths[]] | unique[]' <<< "$entries")

    # 4b. firewalld keeps a removed zone or policy (Docker's "docker" zone)
    #     until it reloads, and this run may have reloaded it to add rules
    #     for a service that is now gone. Load the saved config again (4.36).
    jq -e '[.[] | select(.type == "firewalld_reload")] as $f
           | any($f[]; .phase == "intent" and .pre.reload == true
                 and (.step as $i | any($f[]; .step == $i and .phase == "done")))' \
        <<< "$entries" >/dev/null 2>&1 && firewalld_changed=true
    if [[ "$firewalld_changed" == true ]] && systemctl is-active --quiet firewalld.service 2>/dev/null; then
        if firewall-cmd --reload >&2; then _record reload firewalld ok
        else _issue "firewalld reload failed; its running rules may still include the removed zone." reload firewalld; fi
    fi

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
