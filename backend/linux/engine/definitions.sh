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
# A version_pattern is a POSIX extended regex, at most 200 characters,
# from a small set that means the same in bash and Python.
def version_pattern_re: "^[A-Za-z0-9 ^$.*+?()\\[\\]{}|\\\\:_,/-]{1,200}$";
def path_re: "^(/[A-Za-z0-9._@+-]+)+$";
def unit_re: "^[A-Za-z0-9][A-Za-z0-9@._-]{0,127}\\.(service|socket|timer|target|mount)$";
def id_re: "^[a-z][a-z0-9_-]{0,63}$";
def token_re: "^[a-z0-9][a-z0-9._-]{0,63}$";
# An apt suite may have path parts, e.g. noble/mongodb-org/8.0 for MongoDB (4.47).
def suite_re: "^[a-z0-9][a-z0-9._-]{0,63}(/[a-z0-9][a-z0-9._-]{0,63}){0,3}$";
def host_re: "^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$";
def url_re: "^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$";
# A signing key URL may carry a simple query (keyserver lookups); the key
# is checked against its fingerprint after download anyway (4.47).
def key_url_re: "^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?([?][A-Za-z0-9=&_.-]{1,200})?$";
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
    and (.repository_id | str(id_re)) and (.key_url | str(key_url_re))
    and (.key_fingerprint | str(fpr_re))
    and (.key_path | safe_path) and (.source_path | safe_path)
    and (.uris | str(url_re))
    and (.suites | nonempty_strs(suite_re))
    and (.components | nonempty_strs(token_re))
    and (.architectures | nonempty_strs(token_re))
  elif .type == "dnf_vendor_repository" then
    # DSSA writes the repo file itself (4.36); both files stay in their
    # fixed system folders and baseurl is a literal https URL.
    only(["type","repository_id","baseurl","key_url","key_fingerprint","key_path","repo_path"])
    and has_all(["repository_id","baseurl","key_url","key_fingerprint","key_path","repo_path"])
    and (.repository_id | str(id_re)) and (.baseurl | str(url_re))
    and (.key_url | str(key_url_re)) and (.key_fingerprint | str(fpr_re))
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
    # 4.45: DSSA writes only files under its own names (decision 3).
    and (.path | split("/") | last | test("^dssa-[A-Za-z0-9._-]+$"))
    and (.template | str("^[a-z0-9_-]+(/[a-z0-9_.-]+)*$"))
    and (.mode | str("^0[0-7]{3}$"))
    and (.owner | str("^[a-z_][a-z0-9_-]*(:[a-z_][a-z0-9_-]*)?$"))
  elif .type == "service_reload" then
    # 4.45: reload a running unit after its config test passes.
    only(["type","unit","config_test"]) and has_all(["unit"]) and (.unit | str(unit_re))
    and ((.config_test // "nginx -t") == "nginx -t")
  elif .type == "hook" then only(["type","name"]) and (.name | str(hook_re))
  elif .type == "firewall_enable" then
    # Components phase: allow SSH (always) and the listed services, then
    # enable the firewall (ufw on apt, firewalld on dnf).
    only(["type","allow"]) and has_all(["allow"])
    and (.allow | type == "array" and index("ssh") != null
         and all(.[]; IN("ssh", "http", "https")))
  elif .type == "vendor_installer" then
    # Panels (4.48): the vendor installer from its own HTTPS URL, run
    # unattended; {fqdn} and {password} are filled in on the server.
    only(["type","url","args","env","workdir","password_file","uninstall_url","uninstall_args"])
    and has_all(["url"]) and (.url | str(url_re))
    and ((.args // []) | strs("^[A-Za-z0-9._@{}=:/,+-]{1,128}$"))
    and ((.env // {}) | type == "object"
         and all(keys[]; test("^[A-Z][A-Z0-9_]{0,63}$"))
         and all(.[]; str("^[A-Za-z0-9._@{}=:/,+-]{1,128}$")))
    and ((.workdir // "/root") | safe_path)
    and ((.password_file // "/root/.dssa/x") | str("^/root/[.]dssa/[a-z0-9_-]{1,64}$"))
    and ((.uninstall_url // "https://x") | str(url_re))
    and ((.uninstall_args // []) | strs("^[A-Za-z0-9._@{}=:/,+-]{1,128}$"))
  elif .type == "swap_file" then
    # Components phase: one swap file at a fixed path, sized from RAM.
    only(["type","path"]) and (.path == "/swapfile.dssa")
  else false end;

def valid_check:
  if .type == "packages_installed" then
    only(["type","packages"]) and (.packages | nonempty_strs(pkg_re))
  elif .type == "executable" then
    only(["type","name","version_args","version_pattern"]) and (.name | str(exe_re))
    and ((.version_args // []) | strs("^-{1,2}[a-z][a-z-]*$"))
    # 4.39: the first output line must match; only with version_args.
    and ((has("version_pattern") | not)
         or ((.version_args // []) | length > 0)
            and (.version_pattern | str(version_pattern_re)))
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
  # A component is detected by its packages, or (components phase) by a
  # state: swap or a firewall that is on. With a state, packages_all is [].
  and (.detection | only(["packages_all","state","panel"])
       and (if has("state") then (.state | IN("swap_active", "firewall_active", "panel_present")) and .packages_all == []
            else (.packages_all | nonempty_strs(pkg_re)) end)
       # panel_present (4.48) names the panel discover_panels reports.
       and ((.state == "panel_present") == has("panel"))
       and ((.panel // "x") | str(id_re)))
  and (.conflicts | valid_conflicts)
  and (.steps | type == "array" and length > 0 and all(.[]; valid_step))
  and (.verification | type == "array" and length > 0 and all(.[]; valid_check))
  and (.rollback | only(["created_paths"]) and (.created_paths | safe_paths))
  and ((.hooks // {}) | only(["precheck","verify","setup"]) and all(.[]; str(hook_re)))
  # 4.36: packages that must match the running kernel (dnf only).
  and ((.requirements // {}) | only(["running_kernel_packages","fresh_server","conflicting_packages",
                                     "min_ram_mb","min_disk_mb","min_swap_mb","fqdn_hostname"])
       and ((.running_kernel_packages // []) | strs(pkg_re))
       and ((.conflicting_packages // []) | strs(pkg_re))
       and ((.fresh_server // false) | type == "boolean")
       and ((.fqdn_hostname // false) | type == "boolean")
       and all((.min_ram_mb, .min_disk_mb, .min_swap_mb) // 0; type == "number" and . >= 0 and . == floor))
  and (((.requirements.running_kernel_packages // []) == []) or .platform.package_manager == "dnf")
  and (((.hooks // {}) | [.[]]) as $declared
       | all((.steps + .verification)[] | select(.type == "hook"); .name as $n | $declared | index($n)))
  and (.platform.package_manager as $m
       | all(.steps[]; (.type != "apt_vendor_repository" or $m == "apt")
                       and (.type != "dnf_vendor_repository" or $m == "dnf")));

only(["schema_version","id","display_name","description","category","depends_on","variants"])
and has_all(["schema_version","id","display_name","category","depends_on","variants"])
and .schema_version == "1.0"
and (.id | str(id_re))
and (.display_name | type == "string" and length > 0)
and ((has("description") | not) or (.description | type == "string" and length > 0 and length <= 160))
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
      # A state (components phase: swap, firewall) decides, not packages.
      | (if $v.detection.state != null then ($f.state == true)
         else ($present | length) == ($v.detection.packages_all | length) end) as $installed
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
      # Panels (4.48): a strict fresh-server check (no override).
      | ($v.requirements // {}) as $req | ($f.fresh // {}) as $fresh
      | [ if ($req.fresh_server // false) then
            ( ([$fresh.panels[]? | select(. != ($v.detection.panel // ""))]) as $others
              | if ($others | length) > 0 then "Another control panel is installed: \($others | join(", "))." else empty end ),
            ( if ($fresh.record_components // 0) > 0 then "DSSA has already installed components here (install record not empty); a panel needs a fresh server." else empty end ),
            ( if (($fresh.conflicting_present // []) | length) > 0 then "Already installed, a panel needs a fresh server: \($fresh.conflicting_present | join(", "))." else empty end )
          else empty end,
          ( if ($req.min_ram_mb // 0) > ($fresh.ram_mb // 0) then "Needs at least \($req.min_ram_mb) MiB of RAM (has \($fresh.ram_mb // 0))." else empty end ),
          ( if ($req.min_swap_mb // 0) > ($fresh.swap_mb // 0) then "Needs at least \($req.min_swap_mb) MiB of swap (has \($fresh.swap_mb // 0)); add the swap component first." else empty end ),
          ( if ($req.min_disk_mb // 0) > ($fresh.disk_mb // 0) then "Needs at least \($req.min_disk_mb) MiB of free disk (has \($fresh.disk_mb // 0))." else empty end ),
          ( if ($req.fqdn_hostname // false) and (($fresh.fqdn // "") | test("^[A-Za-z0-9-]+([.][A-Za-z0-9-]+)+$") | not)
            then "Needs a fully qualified host name (hostname -f gives \"\($fresh.fqdn // "")\")." else empty end )
        ] as $fresh_blocks
      | if ($package_conflicts | length) > 0 then
          result("REVIEW"; $package_conflicts; $v)
        elif $installed then
          result("SKIP"; [if $v.detection.state == "swap_active" then "Swap is already active; verification required."
                          elif $v.detection.state == "firewall_active" then "The firewall is already on; verification required."
                          else "\($d.display_name) is installed; verification required." end]; $v)
        elif ($kernel_blocks | length) > 0 then
          result("BLOCKED"; $kernel_blocks; $v)
        elif ($fresh_blocks | length) > 0 then
          result("BLOCKED"; $fresh_blocks; $v)
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
               then ["Managed by \(join(", ")); use its own tools (DSSA installs only what the panel does not own)."]
               else [] end)
          + [($c.tcp_ports // [])[] as $port
             | select(any($ports.listening_tcp_ports[]; . == $port))
             | "TCP port \($port) is in use."]
          ) as $reasons
          | if ($reasons | length) > 0 then result("REVIEW"; $reasons; $v)
            elif $v.detection.state == "panel_present" then
              result("INSTALL"; ["Fresh-server checks passed.",
                                 "Not reversible: take a VM snapshot first. The panel takes over the web server, PHP, databases and mail."]; $v)
              | .details = {panel: $v.detection.panel,
                            reversible: ([$v.steps[] | select(.type == "vendor_installer" and has("uninstall_url"))] | length > 0)}
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

    # Each version_pattern must compile as a bash regex (=~ returns 2 if not).
    local pattern rc
    while IFS= read -r pattern; do
        rc=0
        [[ "" =~ $pattern ]] || rc=$?
        if (( rc == 2 )); then
            echo "ERROR: Component definition failed validation: invalid version_pattern." >&2
            return 1
        fi
    done < <(jq -r '.variants[].verification[] | select(.type == "executable") | .version_pattern // empty' \
                 <<< "$definition")
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
        --argjson state "$(jq -cn --arg s "$(jq -r '.detection.state // empty' <<< "$variant")" \
            --arg swap "$(dssa_swap_active)" --arg fw "$(dssa_firewall_active "$manager")" \
            --arg panel "$(jq -r '.detection.panel // empty' <<< "$variant")" \
            --argjson panels "$(discover_panels 2>/dev/null | jq -c '.panels' 2>/dev/null || echo '[]')" \
            'if $s == "swap_active" then ($swap == "true") elif $s == "firewall_active" then ($fw == "true")
             elif $s == "panel_present" then ($panels | index($panel) != null) else null end')" \
        --argjson fresh "$(dssa_fresh_server_facts "$variant" "$manager")" \
        '{packages: $packages, executables: $executables, paths: $paths,
          repository_patterns: $patterns, units: $units, running_kernel: $kernel,
          state: $state, fresh: $fresh}'
}

# Panels (4.48): what the fresh-server requirements need, read-only and
# without sudo. Prints {} when the variant has none.
dssa_fresh_server_facts() {
    local variant="$1" manager="$2" req pkg present='[]' record_count
    req="$(jq -c '.requirements // {}' <<< "$variant")"
    jq -e 'has("fresh_server") or has("min_ram_mb") or has("min_disk_mb") or has("min_swap_mb") or has("fqdn_hostname")' \
        <<< "$req" >/dev/null || { echo '{}'; return 0; }
    while IFS= read -r pkg; do
        [[ -n "$pkg" ]] && package_is_installed "$manager" "$pkg" >/dev/null 2>&1 &&
            present="$(jq -c --arg p "$pkg" '. + [$p]' <<< "$present")"
    done < <(jq -r '(.conflicting_packages // [])[]' <<< "$req")
    record_count="$( (declare -F dssa_record_read >/dev/null && dssa_record_read 2>/dev/null || echo '{"components":{}}') |
                     jq '.components | length' 2>/dev/null || echo 0)"
    jq -cn --argjson present "$present" --argjson records "${record_count:-0}" \
        --argjson panels "$(discover_panels 2>/dev/null | jq -c '.panels' 2>/dev/null || echo '[]')" \
        --arg ram "$(awk '/^MemTotal:/ {print int($2/1024)}' "${DSSA_ROOT:-}/proc/meminfo" 2>/dev/null)" \
        --arg swap "$(awk '/^SwapTotal:/ {print int($2/1024)}' "${DSSA_ROOT:-}/proc/meminfo" 2>/dev/null)" \
        --arg disk "$(df --output=avail -m "${DSSA_ROOT:-/}" 2>/dev/null | tail -1 | tr -d ' ')" \
        --arg fqdn "$(hostname -f 2>/dev/null)" '
        {conflicting_present: $present, record_components: $records, panels: $panels,
         ram_mb: ($ram | tonumber? // 0), swap_mb: ($swap | tonumber? // 0),
         disk_mb: ($disk | tonumber? // 0), fqdn: $fqdn}'
}

# Is the firewall on? Read without root: ufw's own config on apt systems,
# firewalld's unit state on dnf systems (components phase).
dssa_firewall_active() {
    local conf="${DSSA_ROOT:-}/etc/ufw/ufw.conf"
    case "${1:-${DSSA_MANAGER:-$(select_package_manager 2>/dev/null)}}" in
        apt) [[ -r "$conf" ]] && grep -Eq '^ENABLED=yes' "$conf" && { echo true; return; } ;;
        dnf) [[ "$(systemctl is-active firewalld 2>/dev/null)" == active ]] && { echo true; return; } ;;
    esac
    echo false
}

# Is any swap active? (components phase: the swap component is detected
# by this, as it has no package). Reads /proc/swaps (DSSA_ROOT for tests).
dssa_swap_active() {
    local file="${DSSA_ROOT:-}/proc/swaps"
    if [[ -r "$file" ]] && (( $(wc -l < "$file") > 1 )); then echo true; else echo false; fi
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
