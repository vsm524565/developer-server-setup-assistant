#!/usr/bin/env python3
"""Mock package manager and systemd for journal/rollback tests (4.33).

One script, dispatched by the name it is invoked as (apt-get, dpkg-query,
dpkg, rpm, dnf, systemctl, journalctl, curl, gpg, firewall-cmd). State lives in JSON files under
$MOCK_STATE, and filesystem effects under $DSSA_ROOT:

  packages.json   {"name": "version"}         installed packages
  deps.json       {"pkg": ["dep", ...]}       pulled in on install (with their bins)
  upgrades.json   {"pkg": "new-version"}      applied when any install runs
  pkg_units.json  {"pkg": ["unit", ...]}      units a package provides
  pkg_paths.json  {"pkg": ["/path", ...]}     directories created on install
  pkg_bins.json   {"pkg": ["name", ...]}      executables placed in $MOCK_STATE/bin
  side.json       {"pkg": ["version", ...]}   extra versions installed side by
                                              side (rpm -qa lists them; kernels)
  side_deps.json  {"pkg": {"name": "version"}} side versions added on install
  units.json      {"unit": {"file_state", "active"}}
  unit_props.json {"unit": {"type", "sub"}}   what systemctl show reports
  restarts.json   {"unit": n}                 NRestarts systemctl show reports
  start_files.json {"unit": ["/path", ...]}    files a unit writes when it starts
                                              (dockerd adds a firewalld zone)
  fail.json       {"install": [pkg], "purge": bool, "purge_extra": [pkg],
                   "start": [unit], "stop": [unit], "update": bool,
                   "update_from": n (fail the nth and later updates), "curl": bool,
                   "crash_on_install": [pkg] (kill the detached DSSA run's session),
                   "firewalld_reload": bool, "apt_lock": bool (apt-get update
                   times out on the lock), "unstable": [unit] (NRestarts grows
                   on each read), "no_pid": [unit] (MainPID 0)}
  key.asc, fingerprint                        what curl downloads and gpg reports
  rpm_keys.json   ["gpg-pubkey-…", ...]        rpm's imported signing keys; dnf
                                              imports one at install when a
                                              repo file names a gpgkey
  calls.log       one line per invocation

Removal (4.42):
  rdeps.json      {"pkg": ["dependent", ...]}  installed packages that need pkg:
                                              a simulated purge lists them too,
                                              rpm -e --test refuses
  fail.json keys  "download": [pkg] (no package file), "unavailable": [pkg]
                  (apt-cache show / dnf repoquery find no such version)
Updates (4.43):
  candidates.json {"pkg": "version"}          newest version in the package lists
                                              (apt-cache policy, dnf repoquery --upgrades)
  fail.json keys  "update_adds": [pkg] (a simulated/real upgrade also
                  installs these new packages), "real_update_adds": [pkg]
                  (only the real upgrade, not the simulation), "upgrade": bool
  Package files written by "apt-get download" / "dnf download" hold JSON
  {"name", "version"}; installing a ./path.deb or .rpm installs that.
"""

import json
import re
import os
import signal
import sys
from pathlib import Path

STATE = Path(os.environ["MOCK_STATE"])
ROOT = Path(os.environ.get("DSSA_ROOT", "/nonexistent"))


def load(name, default):
    path = STATE / name
    return json.loads(path.read_text()) if path.exists() else default


def save(name, value):
    (STATE / name).write_text(json.dumps(value, indent=1, sort_keys=True))


def log(argv):
    with open(STATE / "calls.log", "a") as handle:
        handle.write(" ".join(argv) + "\n")


def fail(kind, item=None):
    rules = load("fail.json", {})
    rule = rules.get(kind)
    if isinstance(rule, list):
        return item in rule
    return bool(rule)


def from_files(names):
    """Installing local package files (rollback of a removal, 4.42)."""
    plain, files = [], {}
    for name in names:
        if name.endswith((".deb", ".rpm")):
            data = json.loads(Path(name).read_text())
            files[data["name"]] = data["version"]
        else:
            plain.append(name)
    return plain, files


def upgrade_to(specs):
    """apt-get install --only-upgrade pkg=ver / dnf upgrade pkg-ver (4.43)."""
    if fail("upgrade"):
        print("E: mock failure upgrading", file=sys.stderr)
        return 100
    packages = load("packages.json", {})
    for spec in specs:
        match = re.match(r"^(.+?)=(.+)$", spec) or re.match(r"^(.+)-(\d+:.+)\.x86_64$", spec)
        name, version = match.groups()
        packages[name] = version.split(":", 1)[1] if re.match(r"^\d+:", version) and "=" not in spec else version
    rules = load("fail.json", {})
    for extra in rules.get("update_adds", []) + rules.get("real_update_adds", []):
        packages[extra] = "1.0"
    save("packages.json", packages)
    return 0


def install(names):
    if "--only-upgrade" in sys.argv:
        return upgrade_to(names)
    names, from_file = from_files(names)
    if from_file:
        packages = load("packages.json", {})
        if fail("reinstall"):
            print("E: mock failure installing package files", file=sys.stderr)
            return 100
        for name, version in from_file.items():
            packages[name] = version
        save("packages.json", packages)
        units = load("units.json", {})
        for name in from_file:
            for unit in load("pkg_units.json", {}).get(name, []):
                units.setdefault(unit, {"file_state": "disabled", "active": "inactive"})
            for binary in load("pkg_bins.json", {}).get(name, []):
                (STATE / "bin" / binary).write_text("#!/bin/sh\necho mock-version\n")
                (STATE / "bin" / binary).chmod(0o755)
        save("units.json", units)
        if not names:
            return 0
    packages = load("packages.json", {})
    deps = load("deps.json", {})
    for package, version in load("upgrades.json", {}).items():
        if package in packages:
            packages[package] = version
    side = load("side.json", {})
    for name in names:
        for other, version in load("side_deps.json", {}).get(name, {}).items():
            side.setdefault(other, []).append(version)
        save("side.json", side)
        for dep in deps.get(name, []):
            packages.setdefault(dep, "1.0")
            for binary in load("pkg_bins.json", {}).get(dep, []):
                (STATE / "bin" / binary).write_text("#!/bin/sh\necho mock-version\n")
                (STATE / "bin" / binary).chmod(0o755)
        save("packages.json", packages)
        if name in load("fail.json", {}).get("crash_on_install", []):
            packages[name] = "1.0"
            save("packages.json", packages)
            return crash_session()
        if fail("install", name):
            print(f"E: mock failure installing {name}", file=sys.stderr)
            return 100
        packages[name] = "1.0"
        units = load("units.json", {})
        for unit in load("pkg_units.json", {}).get(name, []):
            units.setdefault(unit, {"file_state": "disabled", "active": "inactive"})
        save("units.json", units)
        for path in load("pkg_paths.json", {}).get(name, []):
            (ROOT / path.lstrip("/")).mkdir(parents=True, exist_ok=True)
        bindir = STATE / "bin"
        for binary in load("pkg_bins.json", {}).get(name, []):
            target = bindir / binary
            target.write_text("#!/bin/sh\necho mock-version\n")
            target.chmod(0o755)
    save("packages.json", packages)
    return 0


def crash_session():
    """Simulate a target crash: SIGKILL the whole detached run session.

    Acts only when the session leader is a DSSA run bundle (setsid in
    runner.sh), never on the test runner's own session.
    """
    sid = os.getsid(0)
    try:
        leader = Path(f"/proc/{sid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        leader = b""
    if b"bundle.sh run" not in leader:
        print("mock: refusing to crash a non-DSSA session", file=sys.stderr)
        return 99
    os.killpg(os.getpgid(0), signal.SIGKILL)
    return 137


def purge(names):
    # DNF removals name packages as name.arch, as rpm -qa reports them,
    # or one exact version as name-epoch:version-release.arch (4.36).
    versioned = [re.match(r"^(.+)-\d+:(.+)\.x86_64$", n) for n in names]
    names = [n[:-len(".x86_64")] if n.endswith(".x86_64") else n
             for n, m in zip(names, versioned) if not m]
    if fail("purge"):
        print("E: mock failure removing packages", file=sys.stderr)
        return 100
    packages = load("packages.json", {})
    units = load("units.json", {})
    # Simulate a package manager that also removes extra packages.
    for extra in load("fail.json", {}).get("purge_extra", []):
        packages.pop(extra, None)
    side = load("side.json", {})
    for match in filter(None, versioned):
        name, version = match.groups()
        if version in side.get(name, []):
            side[name].remove(version)
        elif packages.get(name) == version:
            packages.pop(name)
    save("side.json", side)
    for name in names:
        packages.pop(name, None)
        for unit in load("pkg_units.json", {}).get(name, []):
            units.pop(unit, None)
        for binary in load("pkg_bins.json", {}).get(name, []):
            (STATE / "bin" / binary).unlink(missing_ok=True)
    save("packages.json", packages)
    save("units.json", units)
    return 0


def positional(args):
    # "-o Name=value" (apt's option) takes the next word as its value.
    words, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a == "-o":
            skip = True
        elif not a.startswith("-") and a != "--":
            words.append(a)
    return words


def bare(name):
    return name[:-len(".x86_64")] if name.endswith(".x86_64") else name


def would_remove(names):
    """Names plus the installed packages that need them (rdeps.json)."""
    packages = load("packages.json", {})
    rdeps = load("rdeps.json", {})
    out = [bare(n) for n in names if bare(n) in packages]
    for name in list(out):
        out += [d for d in rdeps.get(name, []) if d in packages and d not in out]
    return out


def download(specs, directory):
    with open(STATE / "download_dirs.log", "a") as handle:  # where apt was asked to write
        handle.write(str(directory) + "\n")
    packages = load("packages.json", {})
    for spec in specs:
        match = re.match(r"^(.+?)=(.+)$", spec) or re.match(r"^(.+)-(\d+:.+)\.x86_64$", spec)
        name, version = match.groups()
        if fail("download", name):
            print(f"E: mock: no package file for {spec}", file=sys.stderr)
            return 100
        suffix = "_amd64.deb" if "=" in spec else ".x86_64.rpm"
        (Path(directory) / f"{name}_{version.replace(':', '%3a')}{suffix}").write_text(
            json.dumps({"name": name if "=" in spec else name, "version": version}))
    return 0


def apt_cache(args):
    words = positional(args)
    if words[:1] == ["policy"]:
        name = words[1]
        installed = load("packages.json", {}).get(name, "(none)")
        print(f"{name}:\n  Installed: {installed}\n  Candidate: {load('candidates.json', {}).get(name, installed)}")
        return 0
    if words[:1] == ["show"]:
        name = words[1].split("=")[0]
        return 100 if fail("unavailable", name) else 0
    return 1


def apt_get(args):
    action = positional(args)[0]
    if action == "download":
        return download(positional(args)[1:], os.getcwd())
    if action == "install" and "-s" in args:
        packages = load("packages.json", {})
        for spec in positional(args)[1:]:
            name, _, version = spec.partition("=")
            print(f"Inst {name} [{packages.get(name, '?')}] ({version} mock [amd64])")
        for extra in load("fail.json", {}).get("update_adds", []):
            print(f"Inst {extra} (1.0 mock [amd64])")
        return 0
    if action in ("purge", "remove") and "-s" in args:
        for name in would_remove(positional(args)[1:]):
            print(f"Purg {name} [1.0]")
        return 0
    if action == "update":
        if fail("apt_lock"):
            print("E: Could not get lock /var/lib/apt/lists/lock (timeout)", file=sys.stderr)
            return 100
        if fail("update"):
            return 100
        # "update_from": N fails the Nth and later update calls.
        start = load("fail.json", {}).get("update_from")
        if start is not None:
            calls = (STATE / "calls.log").read_text().splitlines()
            count = sum(1 for line in calls
                        if line.startswith("apt-get") and positional(line.split()[1:])[:1] == ["update"])
            if count >= start:
                print("E: mock failure refreshing lists", file=sys.stderr)
                return 100
        return 0
    if action == "install":
        return install(positional(args)[1:])
    if action in ("purge", "remove"):
        return purge(positional(args)[1:])
    return 1


def import_repo_keys():
    """Like dnf with gpgcheck=1: import a configured repo's key once."""
    repos = ROOT / "etc/yum.repos.d"
    if not repos.is_dir():
        return
    names = any("gpgkey=file://" in f.read_text() for f in repos.glob("*.repo"))
    if names:
        fpr = (STATE / "fingerprint").read_text().strip().lower()
        keys = load("rpm_keys.json", [])
        key = f"gpg-pubkey-{fpr[-8:]}-5a1b2c3d"
        if key not in keys:
            keys.append(key)
            save("rpm_keys.json", keys)


def dnf(args):
    action = positional(args)[0]
    if action == "download":
        destdir = args[args.index("--destdir") + 1]
        return download([w for w in positional(args)[1:] if w != destdir], destdir)
    if action == "repoquery" and "--upgrades" in args:
        name = bare(positional(args)[-1])
        packages, candidates = load("packages.json", {}), load("candidates.json", {})
        if name in candidates and candidates[name] != packages.get(name):
            print(f"0:{candidates[name]}")
        return 0
    if action == "upgrade":
        return upgrade_to(positional(args)[1:])
    if action == "downgrade":
        return install(positional(args)[1:])
    if action == "repoquery":
        spec = positional(args)[1]
        name = re.match(r"^(.+)-\d+:", spec).group(1)
        if fail("unavailable", name):
            return 0
        print(spec)
        return 0
    if action == "makecache":
        return 1 if fail("update") else 0
    if action == "install":
        import_repo_keys()
        return install(positional(args)[1:])
    if action == "remove":
        return purge(positional(args)[1:])
    return 1


def dpkg_query(args):
    packages = load("packages.json", {})
    names = positional(args)
    if names:  # package_is_installed: dpkg-query -W -f='${Status}' pkg
        if names[0] in packages:
            print("install ok installed", end="")
            return 0
        return 1
    for name, version in sorted(packages.items()):
        print(f"{name}\t{version}\tii ")
    return 0


def rpm(args):
    packages = load("packages.json", {})
    if "-e" in args and "--test" in args:
        names = [bare(n) for n in positional(args)]
        extra = [n for n in would_remove(names) if n not in names]
        if extra:
            print(f"error: Failed dependencies: needed by {' '.join(extra)}", file=sys.stderr)
            return 1
        return 0
    if "-e" in args:
        keys = load("rpm_keys.json", [])
        names = positional(args)
        if fail("rpm_e") or any(n not in keys for n in names):
            print("error: package not installed", file=sys.stderr)
            return 1
        save("rpm_keys.json", [k for k in keys if k not in names])
        return 0
    if positional(args)[:1] == ["gpg-pubkey"]:
        keys = load("rpm_keys.json", [])
        if not keys:
            print("package gpg-pubkey is not installed")
            return 1
        print("\n".join(keys))
        return 0
    if "-qa" in args:
        side = load("side.json", {})
        for name, version in sorted(packages.items()):
            for v in sorted(set([version] + side.get(name, []))):
                print(f"{name}.x86_64\t0:{v}")
        return 0
    names = positional(args)
    return 0 if names and bare(names[0]) in packages else 1


def journalctl(args):
    print("mock journal: " + " ".join(args))
    return 0


def systemctl(args):
    units = load("units.json", {})
    words = [a for a in args if a not in ("--no-legend", "--no-pager", "--plain", "--all", "--")]
    quiet = "--quiet" in words
    words = [w for w in words if w != "--quiet"]
    action = words[0]
    unit = words[1] if len(words) > 1 else None

    if action == "list-unit-files":
        for name, state in sorted(units.items()):
            print(f"{name} {state['file_state']} enabled")
        return 0
    if action == "list-units":
        for name, state in sorted(units.items()):
            print(f"{name} loaded {state['active']} running mock")
        return 0
    if action == "reload":  # 4.45: only a running unit can be reloaded
        unit = words[1]
        if fail("reload", unit) or load("units.json", {}).get(unit, {}).get("active") != "active":
            print(f"mock: cannot reload {unit}", file=sys.stderr)
            return 1
        return 0
    if action == "daemon-reload":
        return 0
    if action == "is-enabled":
        if unit not in units:
            return 1
        if not quiet:
            print(units[unit]["file_state"])
        return 0 if units[unit]["file_state"] == "enabled" else 1
    if action == "show":
        # systemctl show --property=Name --value -- unit (one property).
        prop = next(a.split("=", 1)[1] for a in words if a.startswith("--property="))
        unit = [w for w in words[1:] if not w.startswith("-")][0]
        state = units.get(unit, {})
        active = state.get("active", "inactive")
        rules = load("fail.json", {})
        restarts = load("restarts.json", {})
        if prop == "NRestarts" and unit in rules.get("unstable", []):
            restarts[unit] = restarts.get(unit, 0) + 1
            save("restarts.json", restarts)
        state = {**state, **load("unit_props.json", {}).get(unit, {})}
        values = {
            "Type": state.get("type", "simple"),
            "ActiveState": active,
            "SubState": state.get("sub", "running" if active == "active" else "dead"),
            "MainPID": 0 if active != "active" or unit in rules.get("no_pid", [])
                       else state.get("main_pid", 4242),
            "NRestarts": restarts.get(unit, 0),
        }
        print(values[prop])
        return 0
    if action == "is-active":
        state = units.get(unit, {}).get("active", "inactive")
        if not quiet:
            print(state)
        return 0 if state == "active" else 3
    if unit not in units:
        print(f"Failed to {action} {unit}: Unit not found.", file=sys.stderr)
        return 5
    if action in ("start", "stop") and fail(action, unit):
        print(f"mock failure: {action} {unit}", file=sys.stderr)
        return 1
    if action == "start":
        units[unit]["active"] = "active"
        for name in load("start_files.json", {}).get(unit, []):
            path = ROOT / name.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"written by {unit}\n")
    elif action == "stop":
        units[unit]["active"] = "inactive"
    elif action == "enable":
        units[unit]["file_state"] = "enabled"
    elif action == "disable":
        units[unit]["file_state"] = "disabled"
    else:
        return 1
    save("units.json", units)
    return 0


def curl(args):
    if fail("curl"):
        return 22
    out = args[args.index("-o") + 1]
    Path(out).write_bytes((STATE / "key.asc").read_bytes())
    return 0


def gpg(args):
    fingerprint = (STATE / "fingerprint").read_text().strip()
    print(f"pub:-:4096:1:0000000000000000:0:::-:::scESC::::::23::0:")
    print(f"fpr:::::::::{fingerprint}:")
    return 0


def dpkg(args):
    if "--print-architecture" in args:
        print("amd64")
        return 0
    return 1



def firewall_cmd(args):
    if args == ["--reload"]:
        if fail("firewalld_reload"):
            print("mock failure: firewall-cmd --reload", file=sys.stderr)
            return 1
        print("success")
        return 0
    return 2


TOOLS = {
    "apt-get": apt_get, "dnf": dnf, "dpkg-query": dpkg_query, "rpm": rpm,
    "systemctl": systemctl, "journalctl": journalctl, "curl": curl, "gpg": gpg, "dpkg": dpkg,
    "firewall-cmd": firewall_cmd, "apt-cache": apt_cache,
}

if __name__ == "__main__":
    tool = Path(sys.argv[0]).name
    log([tool] + sys.argv[1:])
    sys.exit(TOOLS[tool](sys.argv[1:]))
