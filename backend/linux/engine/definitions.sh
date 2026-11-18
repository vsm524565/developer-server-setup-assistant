#!/usr/bin/env bash

# Definition-driven component planning (Milestone 4.30).
#
# Planning is split into two parts:
#   1. collect_*_facts  Read-only probes of only what a definition names.
#   2. decide_component A pure jq function: definition + facts -> decision.
#
# Nothing here installs, starts or changes anything. All diagnostics go
# to stderr; each public function prints at most one JSON value on stdout.
#
# Requires: jq, and package_is_installed from
# backend/linux/modules/software/package_manager.sh.
#
# DSSA_ROOT (default empty) prefixes filesystem probes, so tests can use
# a temporary directory instead of the real /etc and /var.

# Structural and safe-value validation, mirroring
# bridge/components/contracts.py. The backend validates independently.
DSSA_DEFINITION_VALIDATE_JQ='
def str($re): type == "string" and test($re);
def strs($re): type == "array" and all(.[]; str($re));
def nonempty_strs($re): strs($re) and length > 0;
def only($allowed): type == "object" and ((keys - $allowed) | length == 0);
def has_all($required): . as $o | all($required[]; . as $k | $o | has($k));

def pkg_re: "^[a-z0-9][a-z0-9+._-]{0,127}$";
def exe_re: "^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$";
def path_re: "^(/[A-Za-z0-9._@+-]+)+$";
def unit_re: "^[A-Za-z0-9][A-Za-z0-9@._-]{0,127}\\.(service|socket|timer|target|mount)$";
def id_re: "^[a-z][a-z0-9_-]{0,63}$";
def token_re: "^[a-z0-9][a-z0-9._-]{0,63}$";
def host_re: "^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$";
def url_re: "^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$";
def fpr_re: "^[0-9A-F]{40}$";
def hook_re: "^[a-z][a-z0-9_]{0,63}$";

def safe_path: str(path_re) and (split("/") | all(.[]; . != "." and . != ".."));
def safe_paths: type == "array" and all(.[]; safe_path);
def port: type == "number" and . == floor and . >= 1 and . <= 65535;

def valid_step:
  if .type == "package_metadata_refresh" then only(["type"])
  elif .type == "packages_install" then
    only(["type","role","packages"]) and has_all(["role","packages"])
    and (.role == "prerequisite" or .role == "main")
    and (.packages | nonempty_strs(pkg_re))
  elif .type == "apt_vendor_repository" then
    only(["type","repository_id","key_url","key_fingerprint","key_path",
          "source_path","uris","suites","components","architectures"])
    and has_all(["repository_id","key_url","key_fingerprint","key_path",
                 "source_path","uris","suites","components","architectures"])
    and (.repository_id | str(id_re)) and (.key_url | str(url_re))
    and (.key_fingerprint | str(fpr_re))
    and (.key_path | safe_path) and (.source_path | safe_path)
    and (.uris | str(url_re))
    and (.suites | nonempty_strs(token_re))
    and (.components | nonempty_strs(token_re))
    and (.architectures | nonempty_strs(token_re))
  elif .type == "dnf_vendor_repository" then
    # DSSA writes the repo file itself (4.36); both files stay in their
    # fixed system folders and baseurl is a literal https URL.
    only(["type","repository_id","baseurl","key_url","key_fingerprint","key_path","repo_path"])
    and has_all(["repository_id","baseurl","key_url","key_fingerprint","key_path","repo_path"])
    and (.repository_id | str(id_re)) and (.baseurl | str(url_re))
    and (.key_url | str(url_re)) and (.key_fingerprint | str(fpr_re))
    and (.key_path | safe_path and str("^/etc/pki/rpm-gpg/[A-Za-z0-9._-]{1,128}$"))
    and (.repo_path | safe_path and str("^/etc/yum\\.repos\\.d/[A-Za-z0-9._-]{1,128}\\.repo$"))
  elif .type == "service_enable" or .type == "service_start" then
    only(["type","unit"]) and (.unit | str(unit_re))
  elif .type == "firewalld_reload" then
    only(["type","when_packages_added"]) and has_all(["when_packages_added"])
    and (.when_packages_added | nonempty_strs(pkg_re))
  elif .type == "managed_file" then
    only(["type","path","template","mode","owner"])
    and has_all(["path","template","mode","owner"])
    and (.path | safe_path)
    and (.template | str("^[a-z0-9_-]+(/[a-z0-9_.-]+)*$"))
    and (.mode | str("^0[0-7]{3}$"))
    and (.owner | str("^[a-z_][a-z0-9_-]*(:[a-z_][a-z0-9_-]*)?$"))
  elif .type == "hook" then only(["type","name"]) and (.name | str(hook_re))
  else false end;

def valid_check:
  if .type == "packages_installed" then
    only(["type","packages"]) and (.packages | nonempty_strs(pkg_re))
  elif .type == "executable" then
    only(["type","name","version_args"]) and (.name | str(exe_re))
    and ((.version_args // []) | strs("^-{1,2}[a-z][a-z-]*$"))
  elif .type == "service_active" or .type == "service_enabled" then
    only(["type","unit"]) and (.unit | str(unit_re))
  elif .type == "tcp_listening" then only(["type","port"]) and (.port | port)
  elif .type == "http_local" then
    only(["type","port","expect_status"]) and (.port | port)
    and (.expect_status | str("^[1-5](xx|[0-9]{2})$"))
  elif .type == "config_test" then
    only(["type","command"]) and (.command == "nginx -t")
  elif .type == "hook" then only(["type","name"]) and (.name | str(hook_re))
  else false end;

def valid_conflicts:
  only(["packages","partial_packages","unmanaged_executables","paths",
        "repository_patterns","systemd_units","tcp_ports","panels"])
  and ((.packages // []) | strs(pkg_re))
  and ((.partial_packages // false) | type == "boolean")
  and ((.unmanaged_executables // []) | strs(exe_re))
  and ((.paths // []) | safe_paths)
  and ((.repository_patterns // []) | strs(host_re))
  and ((.systemd_units // []) | strs(unit_re))
  and ((.tcp_ports // []) | type == "array" and all(.[]; port))
  and ((.panels // []) | . == "any" or strs(id_re));

def valid_platform:
  only(["package_manager","os","architectures"])
  and (.package_manager == "apt" or .package_manager == "dnf")
  and ((.os // [{"id":"x","versions":["0"]}]) | type == "array" and length > 0
       and all(.[]; only(["id","versions"]) and (.id | str("^[a-z][a-z0-9._-]{0,31}$"))
               and (.versions | nonempty_strs("^[0-9][0-9.]{0,15}$"))))
  and ((.architectures // ["x86_64"]) | type == "array" and length > 0
       and all(.[]; . == "x86_64" or . == "aarch64"));

def valid_variant:
  only(["variant_id","support","platform","detection","conflicts",
        "steps","verification","rollback","hooks","requirements"])
  and has_all(["variant_id","support","platform","detection","conflicts",
               "steps","verification","rollback"])
  and (.variant_id | str(id_re))
  and (.support == "draft" or .support == "supported")
  and (.platform | valid_platform)
  and (.detection | only(["packages_all"]) and (.packages_all | nonempty_strs(pkg_re)))
  and (.conflicts | valid_conflicts)
  and (.steps | type == "array" and length > 0 and all(.[]; valid_step))
  and (.verification | type == "array" and length > 0 and all(.[]; valid_check))
  and (.rollback | only(["created_paths"]) and (.created_paths | safe_paths))
  and ((.hooks // {}) | only(["precheck","verify"]) and all(.[]; str(hook_re)))
  # 4.36: packages that must match the running kernel (dnf only).
  and ((.requirements // {}) | only(["running_kernel_packages"])
       and ((.running_kernel_packages // []) | strs(pkg_re)))
  and (.requirements == null or .platform.package_manager == "dnf")
  and (((.hooks // {}) | [.[]]) as $declared
       | all((.steps + .verification)[] | select(.type == "hook"); .name as $n | $declared | index($n)))
  and (.platform.package_manager as $m
       | all(.steps[]; (.type != "apt_vendor_repository" or $m == "apt")
                       and (.type != "dnf_vendor_repository" or $m == "dnf")));

only(["schema_version","id","display_name","category","depends_on","variants"])
and has_all(["schema_version","id","display_name","category","depends_on","variants"])
and .schema_version == "1.0"
and (.id | str(id_re))
and (.display_name | type == "string" and length > 0)
and (.category | type == "string" and length > 0)
and (.depends_on | type == "array"
     and all(.[]; only(["id","required"]) and (.id | str(id_re))
                  and ((has("required") | not) or (.required | type == "boolean"))))
and (.id as $self | all(.depends_on[]; .id != $self))
and (.variants | type == "array" and length > 0 and all(.[]; valid_variant))
and ([.variants[].variant_id] | length == (unique | length))
'

# Variant selection and the plan decision, as a pure jq library.
DSSA_DEFINITION_DECIDE_JQ='
# A version with a dot matches exactly ("24.04"). A version without a
# dot also matches its point releases (4.36): "10" matches "10" and
# "10.2", so an EL major release keeps matching after a minor update.
def version_ok($v; $have):
  $have == $v or (($v | contains(".") | not) and ($have | startswith($v + ".")));

def os_ok($p):
  (.platform.os // null) as $os
  | $os == null
    or any($os[]; .id == $p.os_id and any(.versions[]; version_ok(.; $p.os_version)));

def arch_ok($p):
  (.platform.architectures // null) as $a
  | $a == null or any($a[]; . == $p.arch);

def repo_arch_ok($p):
  all(.steps[] | select(.type == "apt_vendor_repository");
      any(.architectures[]; . == $p.dpkg_arch));

def describe:
  ([(.platform.os // [])[] | "\(.id) \(.versions | join("/"))"] | join(", ")) as $os
  | ((.platform.architectures // []) | join("/")) as $arch
  | [ (if $os == "" then "any OS" else $os end),
      (if $arch == "" then empty else "(\($arch))" end) ] | join(" ");

# Returns {variant, reason}. Variants are chosen by package manager;
# OS and architecture are checked only where a variant declares them.
def select_variant($p):
  . as $d
  | [.variants[] | select(.platform.package_manager == $p.package_manager)] as $m
  | if ($m | length) == 0 then
      {variant: null,
       reason: "No \($d.display_name) variant supports package manager \($p.package_manager)."}
    else
      ([$m[] | select(os_ok($p) and arch_ok($p) and repo_arch_ok($p))] | first) as $v
      | if $v == null then
          {variant: null,
           reason: "\($d.display_name) requires: \([$m[] | describe] | join("; ")); detected \($p.os_id) \($p.os_version) (\($p.arch))."}
        else {variant: $v, reason: null} end
    end;

def result($decision; $reasons; $variant):
  {component: .id,
   variant_id: ($variant.variant_id // null),
   support: ($variant.support // null),
   decision: $decision,
   reasons: $reasons};

# Package conflicts are always checked, even for an installed component.
# State conflicts (executables, paths, repositories, units, panels and
# ports) are only checked for an install candidate, because a working
# installation creates exactly that state.
def decide($p; $f; $panels; $ports):
  . as $d
  | select_variant($p) as $s
  | if $s.variant == null then result("BLOCKED"; [$s.reason]; null)
    else
      $s.variant as $v | $v.conflicts as $c
      | [($c.packages // [])[] | select($f.packages[.] == true)
          | "Conflicting package requires review: \(.)"] as $package_conflicts
      | [$v.detection.packages_all[] | select($f.packages[.] == true)] as $present
      # 4.36: a package built for one kernel (kernel-modules-extra) must
      # exist for the RUNNING kernel. If the repositories only offer a
      # newer build, dnf installs a whole new kernel and the component
      # cannot work until a reboot. DSSA never reboots, so it blocks.
      | [($v.requirements.running_kernel_packages // [])[] as $k
         | (($f.running_kernel // {})[$k] // {checked: false}) as $r
         | if $r.installed == true then empty
           elif $r.checked != true then
             "Could not check \($k) for the running kernel; plan again when the repositories are reachable."
           elif $r.latest == null then
             "\($k) is not available in the enabled repositories."
           elif $r.latest == $r.release then empty
           else
             "Running kernel \($r.release) is older than the newest \($k) (\($r.latest)): installing would add a new kernel, and \($d.display_name) could not start until a reboot. Update the server and reboot first, then plan again."
           end] as $kernel_blocks
      | if ($package_conflicts | length) > 0 then
          result("REVIEW"; $package_conflicts; $v)
        elif ($present | length) == ($v.detection.packages_all | length) then
          result("SKIP"; ["\($d.display_name) is installed; verification required."]; $v)
        elif ($kernel_blocks | length) > 0 then
          result("BLOCKED"; $kernel_blocks; $v)
        else
          ( (if ($c.partial_packages // false) and ($present | length) > 0
             then ["Partial \($d.display_name) installation requires review: \($present | join(", "))"]
             else [] end)
          + [($c.unmanaged_executables // [])[] | select($f.executables[.] == true)
             | "Executable exists outside the package installation: \(.)"]
          + [($c.paths // [])[] | select($f.paths[.] == true)
             | "Existing state requires review: \(.)"]
          + [($c.repository_patterns // [])[] | select($f.repository_patterns[.] == true)
             | "Existing repository reference requires review: \(.)"]
          + [($c.systemd_units // [])[] | select($f.units[.] == true)
             | "Existing systemd unit requires review: \(.)"]
          + (($c.panels // []) as $want
             | [$panels.panels[] | . as $panel
                | select($want == "any" or ($want | index($panel)) != null)]
             | if length > 0
               then ["Control panel evidence requires review: \(join(", "))"]
               else [] end)
          + [($c.tcp_ports // [])[] as $port
             | select(any($ports.listening_tcp_ports[]; . == $port))
             | "TCP port \($port) is in use."]
          ) as $reasons
          | if ($reasons | length) > 0 then result("REVIEW"; $reasons; $v)
            else result("INSTALL"; ["No conflicts found by definition checks."]; $v)
            end
        end
    end
  # A draft variant may be planned but never applied.
  | . + {action: (if .decision == "INSTALL" and .support == "draft"
                  then "REVIEW" else .decision end)}
  | if .decision == "INSTALL" and .support == "draft"
    then .reasons += ["Variant \(.variant_id) is a draft; installation is not yet validated."]
    else . end
  | . + {package_manager: $p.package_manager,
         planning_only: true,
         execution_allowed: false};
'

# Validate one definition. Prints nothing on success.
validate_component_definition() {
    local definition="$1"

    if ! jq -e "$DSSA_DEFINITION_VALIDATE_JQ" <<< "$definition" \
        >/dev/null 2>&1; then
        echo "ERROR: Component definition failed validation." >&2
        return 1
    fi
}

# Read one field from os-release without sourcing it as shell code.
read_os_release_field() {
    local field="$1"
    local file="${DSSA_ROOT:-}/etc/os-release"
    local line value

    [[ -r "$file" ]] || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == "$field="* ]] || continue
        value="${line#*=}"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        printf '%s\n' "$value"
        return 0
    done < "$file"

    return 1
}

# Probes, kept as small functions so tests can replace them.
dssa_executable_present() {
    command -v -- "$1" >/dev/null 2>&1
}

dssa_path_present() {
    local path="${DSSA_ROOT:-}$1"
    [[ -e "$path" || -L "$path" ]]
}

dssa_repository_pattern_present() {
    local manager="$1"
    local pattern="$2"
    local root="${DSSA_ROOT:-}"
    local location
    local -a candidates=() locations=()

    case "$manager" in
        apt) candidates=("$root/etc/apt/sources.list" "$root/etc/apt/sources.list.d") ;;
        dnf) candidates=("$root/etc/yum.repos.d") ;;
        *) return 2 ;;
    esac

    # Search only existing locations. A missing file must not hide a
    # match elsewhere (grep exits 2 for a missing file even on a match).
    for location in "${candidates[@]}"; do
        [[ -e "$location" ]] && locations+=("$location")
    done
    (( ${#locations[@]} > 0 )) || return 1

    grep -RIqiF -- "$pattern" "${locations[@]}" 2>/dev/null
}

dssa_unit_known() {
    local unit="$1"

    systemctl list-unit-files "$unit" --no-legend 2>/dev/null |
        awk '{ print $1 }' | grep -Fxq -- "$unit"
}

# State of one package that must match the running kernel (4.36, dnf).
# Read-only, no root needed. Prints
#   {"release": "<uname -r>", "installed": bool, "latest": "<v-r.arch>"|null, "checked": bool}
# "latest" is the newest build the enabled repositories offer.
dssa_running_kernel_package_state() {
    local package="$1" release arch installed=false latest="" checked=true version

    release="$(uname -r)" || release=""
    arch="$(uname -m)" || arch=""
    while IFS= read -r version; do
        [[ -n "$release" && "$version" == "$release" ]] && installed=true
    done < <(rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' -- "$package" 2>/dev/null || true)

    if [[ "$installed" != true ]]; then
        if latest="$(timeout 120 dnf -q repoquery --latest-limit 1 --arch "$arch" \
                --qf '%{version}-%{release}.%{arch}' -- "$package" 2>/dev/null)"; then
            latest="$(head -n 1 <<< "$latest" | tr -d '[:space:]')"
        else
            checked=false
            latest=""
        fi
    fi
    [[ -n "$release" ]] || checked=false

    jq -cn --arg release "$release" --argjson installed "$installed" \
        --arg latest "$latest" --argjson checked "$checked" \
        '{release: $release, installed: $installed,
          latest: (if $latest == "" then null else $latest end), checked: $checked}'
}

# Facts that select a variant.
collect_platform_facts() {
    local manager="$1"
    local os_id os_version arch dpkg_arch=""

    os_id="$(read_os_release_field ID)" || os_id=""
    os_version="$(read_os_release_field VERSION_ID)" || os_version=""
    arch="$(uname -m)" || arch=""

    if [[ "$manager" == "apt" ]]; then
        dpkg_arch="$(dpkg --print-architecture 2>/dev/null)" || dpkg_arch=""
    fi

    jq -cn \
        --arg manager "$manager" \
        --arg os_id "$os_id" \
        --arg os_version "$os_version" \
        --arg arch "$arch" \
        --arg dpkg_arch "$dpkg_arch" \
        '{package_manager: $manager, os_id: $os_id, os_version: $os_version,
          arch: $arch, dpkg_arch: $dpkg_arch}'
}

# Probe only the items one variant names. Prints a facts object.
collect_variant_facts() {
    local variant="$1"
    local manager="$2"
    local item
    local -a packages=() executables=() paths=() patterns=() units=()
    local kernel='{}' state

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        if package_is_installed "$manager" "$item"; then
            packages+=("$item=true")
        else
            packages+=("$item=false")
        fi
    done < <(jq -r '(.detection.packages_all + (.conflicts.packages // []))
                    | unique | .[]' <<< "$variant")

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        if dssa_executable_present "$item"; then
            executables+=("$item=true")
        else
            executables+=("$item=false")
        fi
    done < <(jq -r '(.conflicts.unmanaged_executables // [])[]' <<< "$variant")

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        if dssa_path_present "$item"; then
            paths+=("$item=true")
        else
            paths+=("$item=false")
        fi
    done < <(jq -r '((.conflicts.paths // []) + .rollback.created_paths)
                    | unique | .[]' <<< "$variant")

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        if dssa_repository_pattern_present "$manager" "$item"; then
            patterns+=("$item=true")
        else
            patterns+=("$item=false")
        fi
    done < <(jq -r '(.conflicts.repository_patterns // [])[]' <<< "$variant")

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        if dssa_unit_known "$item"; then
            units+=("$item=true")
        else
            units+=("$item=false")
        fi
    done < <(jq -r '(.conflicts.systemd_units // [])[]' <<< "$variant")

    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        state="$(dssa_running_kernel_package_state "$item")" || state='{"checked": false}'
        kernel="$(jq -c --arg k "$item" --argjson s "$state" '. + {($k): $s}' <<< "$kernel")"
    done < <(jq -r '(.requirements.running_kernel_packages // [])[]' <<< "$variant")

    # Convert name=bool pairs into objects. Names never contain "=".
    _dssa_pairs() {
        jq -Rn '[inputs | select(length > 0) | capture("^(?<k>[^=]+)=(?<v>true|false)$")
                 | {(.k): (.v == "true")}] | add // {}'
    }

    jq -cn \
        --argjson packages "$(printf '%s\n' "${packages[@]}" | _dssa_pairs)" \
        --argjson executables "$(printf '%s\n' "${executables[@]}" | _dssa_pairs)" \
        --argjson paths "$(printf '%s\n' "${paths[@]}" | _dssa_pairs)" \
        --argjson patterns "$(printf '%s\n' "${patterns[@]}" | _dssa_pairs)" \
        --argjson units "$(printf '%s\n' "${units[@]}" | _dssa_pairs)" \
        --argjson kernel "$kernel" \
        '{packages: $packages, executables: $executables, paths: $paths,
          repository_patterns: $patterns, units: $units, running_kernel: $kernel}'
}

# Pure decision: definition + facts -> plan entry. No probing.
decide_component() {
    local definition="$1"
    local platform_facts="$2"
    local variant_facts="$3"
    local panel_facts="$4"
    local port_facts="$5"

    jq -c \
        --argjson p "$platform_facts" \
        --argjson f "$variant_facts" \
        --argjson panels "$panel_facts" \
        --argjson ports "$port_facts" \
        "$DSSA_DEFINITION_DECIDE_JQ decide(\$p; \$f; \$panels; \$ports)" \
        <<< "$definition"
}

# Plan one component from its definition on the current host.
# Prints one plan entry (JSON) and returns 0, or returns non-zero.
plan_component_from_definition() {
    local definition="$1"
    local manager="$2"
    local panel_facts="$3"
    local port_facts="$4"
    local platform_facts variant variant_facts='{}'

    validate_component_definition "$definition" || return 2

    # Reject incomplete discovery rather than assume no conflicts.
    jq -e 'type == "object" and (.panels | type == "array")
           and all(.panels[]; type == "string")' \
        <<< "$panel_facts" >/dev/null 2>&1 || return 3
    jq -e 'type == "object" and (.listening_tcp_ports | type == "array")
           and all(.listening_tcp_ports[];
                   type == "number" and . >= 1 and . <= 65535)' \
        <<< "$port_facts" >/dev/null 2>&1 || return 3

    platform_facts="$(collect_platform_facts "$manager")" || return 3

    variant="$(
        jq -c --argjson p "$platform_facts" \
            "$DSSA_DEFINITION_DECIDE_JQ select_variant(\$p) | .variant" \
            <<< "$definition"
    )" || return 3

    if [[ "$variant" != "null" ]]; then
        variant_facts="$(collect_variant_facts "$variant" "$manager")" ||
            return 3
    fi

    decide_component "$definition" "$platform_facts" "$variant_facts" \
        "$panel_facts" "$port_facts"
}
