#!/usr/bin/env python3
"""Mock package manager and systemd for journal/rollback tests (4.33).

One script, dispatched by the name it is invoked as (apt-get, dpkg-query,
dpkg, rpm, dnf, systemctl, curl, gpg). State lives in JSON files under
$MOCK_STATE, and filesystem effects under $DSSA_ROOT:

  packages.json   {"name": "version"}         installed packages
  deps.json       {"pkg": ["dep", ...]}       pulled in on install
  upgrades.json   {"pkg": "new-version"}      applied when any install runs
  pkg_units.json  {"pkg": ["unit", ...]}      units a package provides
  pkg_paths.json  {"pkg": ["/path", ...]}     directories created on install
  pkg_bins.json   {"pkg": ["name", ...]}      executables placed in $MOCK_STATE/bin
  units.json      {"unit": {"file_state", "active"}}
  fail.json       {"install": [pkg], "purge": bool, "purge_extra": [pkg],
                   "start": [unit], "stop": [unit], "update": bool, "curl": bool}
  key.asc, fingerprint                        what curl downloads and gpg reports
  calls.log       one line per invocation
"""

import json
import os
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
    for name in names:
        for dep in deps.get(name, []):
            packages.setdefault(dep, "1.0")
        save("packages.json", packages)
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


def purge(names):
    # DNF removals name packages as name.arch, as rpm -qa reports them.
    names = [n[:-len(".x86_64")] if n.endswith(".x86_64") else n for n in names]
    if fail("purge"):
        print("E: mock failure removing packages", file=sys.stderr)
        return 100
    packages = load("packages.json", {})
    units = load("units.json", {})
    # Simulate a package manager that also removes extra packages.
    for extra in load("fail.json", {}).get("purge_extra", []):
        packages.pop(extra, None)
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
        return 100 if fail("update") else 0
    if action == "install":
        return install(positional(args)[1:])
    if action in ("purge", "remove"):
        return purge(positional(args)[1:])
    return 1


def dnf(args):
    action = positional(args)[0]
    if action == "makecache":
        return 1 if fail("update") else 0
    if action == "install":
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
    if "-qa" in args:
        for name, version in sorted(packages.items()):
            print(f"{name}.x86_64\t0:{version}")
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


TOOLS = {
    "apt-get": apt_get, "dnf": dnf, "dpkg-query": dpkg_query, "rpm": rpm,
    "systemctl": systemctl, "curl": curl, "gpg": gpg, "dpkg": dpkg,
}

if __name__ == "__main__":
    tool = Path(sys.argv[0]).name
    log([tool] + sys.argv[1:])
    sys.exit(TOOLS[tool](sys.argv[1:]))
