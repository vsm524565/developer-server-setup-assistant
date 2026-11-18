#!/usr/bin/env python3
"""Mock package manager and systemd for journal/rollback tests (4.33).

One script, dispatched by the name it is invoked as (apt-get, dpkg-query,
dpkg, rpm, dnf, systemctl, curl, gpg, firewall-cmd). State lives in JSON files under
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
  start_files.json {"unit": ["/path", ...]}    files a unit writes when it starts
                                              (dockerd adds a firewalld zone)
  fail.json       {"install": [pkg], "purge": bool, "purge_extra": [pkg],
                   "start": [unit], "stop": [unit], "update": bool,
                   "update_from": n (fail the nth and later updates), "curl": bool,
                   "crash_on_install": [pkg] (kill the detached DSSA run's session),
                   "firewalld_reload": bool}
  key.asc, fingerprint                        what curl downloads and gpg reports
  rpm_keys.json   ["gpg-pubkey-…", ...]        rpm's imported signing keys; dnf
                                              imports one at install when a
                                              repo file names a gpgkey
  calls.log       one line per invocation
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


def install(names):
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
    return [a for a in args if not a.startswith("-") and a != "--"]


def apt_get(args):
    action = positional(args)[0]
    if action == "update":
        if fail("update"):
            return 100
        # "update_from": N fails the Nth and later update calls.
        start = load("fail.json", {}).get("update_from")
        if start is not None:
            calls = (STATE / "calls.log").read_text().splitlines()
            count = sum(1 for line in calls if line.startswith("apt-get update"))
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
    return 0 if names and names[0] in packages else 1


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
    if action == "daemon-reload":
        return 0
    if action == "is-enabled":
        if unit not in units:
            return 1
        if not quiet:
            print(units[unit]["file_state"])
        return 0 if units[unit]["file_state"] == "enabled" else 1
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
    "systemctl": systemctl, "curl": curl, "gpg": gpg, "dpkg": dpkg,
    "firewall-cmd": firewall_cmd,
}

if __name__ == "__main__":
    tool = Path(sys.argv[0]).name
    log([tool] + sys.argv[1:])
    sys.exit(TOOLS[tool](sys.argv[1:]))
