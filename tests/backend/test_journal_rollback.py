"""Journaled execution and rollback against a mocked system (4.33).

Every test builds a fake server: a temporary DSSA_ROOT, mock package
manager and systemd (tests/backend/fixtures/mock_system.py) on PATH, and
seeded packages/units. After each scenario the real package and unit
state is compared with the pre-run snapshot.
"""

import copy
import json
import re
import os
import shlex
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "tests/backend/fixtures/mock_system.py"
SOURCES = [
    ROOT / "backend/linux/modules/software/package_manager.sh",
    ROOT / "backend/linux/engine/definitions.sh",
    ROOT / "backend/linux/engine/journal.sh",
]
TOOLS = ("apt-get", "dnf", "dpkg-query", "dpkg", "rpm", "systemctl", "journalctl", "curl", "gpg", "firewall-cmd")
FPR = "0123456789ABCDEF0123456789ABCDEF01234567"
PLAN_A = "11111111-1111-4111-8111-111111111111"
PLAN_B = "22222222-2222-4222-8222-222222222222"
INITIAL_PACKAGES = {"base-files": "1.0", "libshared": "1.0"}
INITIAL_UNITS = {"ssh.service": {"file_state": "enabled", "active": "active"}}


def simple_definition(created_paths=("/var/lib/testtool",), support="supported"):
    return {
        "schema_version": "1.0", "id": "testtool", "display_name": "Test Tool",
        "category": "tool", "depends_on": [],
        "variants": [{
            "variant_id": "apt-test", "support": support,
            "platform": {"package_manager": "apt"},
            "detection": {"packages_all": ["testtool"]},
            "conflicts": {},
            "steps": [
                {"type": "package_metadata_refresh"},
                {"type": "packages_install", "role": "main", "packages": ["testtool"]},
            ],
            "verification": [
                {"type": "packages_installed", "packages": ["testtool"]},
                {"type": "executable", "name": "testtool", "version_args": ["--version"]},
            ],
            "rollback": {"created_paths": list(created_paths)},
        }],
    }


def vendor_definition():
    return {
        "schema_version": "1.0", "id": "vendorapp", "display_name": "Vendor App",
        "category": "tool", "depends_on": [],
        "variants": [{
            "variant_id": "apt-vendor", "support": "supported",
            "platform": {"package_manager": "apt"},
            "detection": {"packages_all": ["vendorapp"]},
            "conflicts": {},
            "steps": [
                {"type": "package_metadata_refresh"},
                {"type": "packages_install", "role": "prerequisite", "packages": ["helper"]},
                {"type": "apt_vendor_repository", "repository_id": "vendor-repo",
                 "key_url": "https://repo.example.com/key.asc", "key_fingerprint": FPR,
                 "key_path": "/etc/apt/keyrings/vendor.asc",
                 "source_path": "/etc/apt/sources.list.d/vendor.sources",
                 "uris": "https://repo.example.com/apt", "suites": ["noble"],
                 "components": ["stable"], "architectures": ["amd64"]},
                {"type": "package_metadata_refresh"},
                {"type": "packages_install", "role": "main", "packages": ["vendorapp"]},
                {"type": "service_enable", "unit": "vendorapp.service"},
                {"type": "service_start", "unit": "vendorapp.service"},
            ],
            "verification": [
                {"type": "packages_installed", "packages": ["vendorapp"]},
                {"type": "service_active", "unit": "vendorapp.service"},
            ],
            "rollback": {"created_paths": ["/var/lib/vendorapp"]},
        }],
    }


PATHS = json.dumps([
    "/var/lib/testtool", "/var/lib/vendorapp", "/opt",
    "/etc/apt/keyrings/vendor.asc", "/etc/apt/sources.list.d/vendor.sources",
])


class FakeServer(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.state = base / "state"
        self.tools = base / "tools"
        for directory in (self.root / "etc/apt/sources.list.d", self.state / "bin", self.tools):
            directory.mkdir(parents=True)
        (self.root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        MOCK.chmod(0o755)
        for tool in TOOLS:
            (self.tools / tool).symlink_to(MOCK)

        self.write("packages.json", INITIAL_PACKAGES)
        self.write("units.json", INITIAL_UNITS)
        self.write("deps.json", {"testtool": ["libtt"], "vendorapp": ["libvendor"]})
        self.write("pkg_bins.json", {"testtool": ["testtool"]})
        self.write("pkg_paths.json", {"testtool": ["/var/lib/testtool"],
                                      "vendorapp": ["/var/lib/vendorapp"]})
        self.write("pkg_units.json", {"vendorapp": ["vendorapp.service"]})
        (self.state / "key.asc").write_text("mock key\n")
        (self.state / "fingerprint").write_text(FPR + "\n")

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def read(self, name):
        return json.loads((self.state / name).read_text())

    def bash(self, body, with_root=True):
        env = {
            "PATH": f"{self.state / 'bin'}:{self.tools}:/usr/bin:/bin",
            "MOCK_STATE": str(self.state),
            "HOME": self.tmp.name,
            # The stability watch (4.37) is tested on its own; elsewhere
            # one check right after start keeps the suite fast.
            "DSSA_SERVICE_SETTLE_SECONDS": "0",
        }
        if with_root:
            env["DSSA_ROOT"] = str(self.root)
        sources = "\n".join(f"source {shlex.quote(str(s))}" for s in SOURCES)
        # umask 022 as under sudo: DSSA's files must still be private.
        return subprocess.run(
            ["bash", "-c", f"set -uo pipefail\numask 022\n{sources}\n{body}"],
            text=True, capture_output=True, timeout=60, env=env, check=False,
        )

    def apply(self, definition, variant_id, plan_id=PLAN_A, manager="apt", prelude="", paths=PATHS):
        result = self.bash(f"""
{prelude}
begin_transaction {plan_id} {manager} {shlex.quote(paths)} || exit 90
result="$(apply_component {plan_id} {manager} {shlex.quote(json.dumps(definition))} {variant_id})"
rc=$?
end_transaction {plan_id} "[${{result:-null}}]" || exit 91
printf '%s\\n' "$result"
exit $rc
""")
        self.assertNotIn(result.returncode, (90, 91), result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, f"stdout must be one JSON line: {result.stdout!r}")
        return result.returncode, json.loads(lines[0]), result

    def tx(self, plan_id=PLAN_A):
        return self.root / "var/lib/dssa/transactions" / plan_id

    def journal(self, plan_id=PLAN_A):
        return [json.loads(line) for line in (self.tx(plan_id) / "journal.jsonl").read_text().splitlines()]

    def assert_back_to_snapshot(self, plan_id=PLAN_A):
        snapshot = json.loads((self.tx(plan_id) / "snapshot.json").read_text())
        self.assertEqual(self.read("packages.json"), snapshot["packages"])
        units = {u: {"file_state": s["file_state"], "active": s["active"]}
                 for u, s in snapshot["units"].items()}
        self.assertEqual(self.read("units.json"), units)


class SuccessTests(FakeServer):

    def test_simple_install_is_journaled_and_verified(self):
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 0)
        self.assertEqual(result["outcome"], "installed")
        self.assertEqual(result["verification"], "passed")
        self.assertIn("testtool", self.read("packages.json"))
        self.assertIn("libtt", self.read("packages.json"))

        snapshot = json.loads((self.tx() / "snapshot.json").read_text())
        self.assertEqual(snapshot["packages"], INITIAL_PACKAGES)
        self.assertEqual(snapshot["paths"]["/var/lib/testtool"], "absent")
        self.assertIn("ssh.service", snapshot["units"])

        entries = self.journal()
        self.assertEqual([e["seq"] for e in entries], list(range(1, len(entries) + 1)))
        phases = [(e.get("step"), e["phase"]) for e in entries if "step" in e]
        self.assertEqual(phases, [(0, "intent"), (0, "done"), (1, "intent"), (1, "done")])
        self.assertTrue((self.tx() / "result.json").exists())
        for name in ("snapshot.json", "result.json", "journal.jsonl"):
            self.assertEqual((self.tx() / name).stat().st_mode & 0o777, 0o600, name)
        pkgsets = list((self.tx() / "pkgsets").iterdir())
        self.assertTrue(pkgsets)
        for path in pkgsets:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path.name)
        for folder in (self.tx(), self.tx() / "backups", self.tx() / "pkgsets"):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700, folder.name)

    def test_vendor_repository_install(self):
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 0, result)
        for path in (self.tx() / "backups").iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0, path.name)
        # Installed repository files are public by design (apt must read them).
        key = self.root / "etc/apt/keyrings/vendor.asc"
        source = self.root / "etc/apt/sources.list.d/vendor.sources"
        self.assertEqual(key.read_text(), "mock key\n")
        self.assertEqual(key.stat().st_mode & 0o777, 0o644)
        self.assertIn("Signed-By: /etc/apt/keyrings/vendor.asc", source.read_text())
        self.assertIn("Suites: noble", source.read_text())
        self.assertEqual(self.read("units.json")["vendorapp.service"],
                         {"file_state": "enabled", "active": "active"})

    def test_upgraded_package_is_reported(self):
        self.write("upgrades.json", {"libshared": "2.0"})
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 0)
        self.assertEqual(result["upgraded_not_reverted"],
                         [{"package": "libshared", "before": "1.0", "after": "2.0"}])


class AlreadyInstalledTests(FakeServer):
    """Packages present before the step are never passed to apt or dnf."""

    def definition(self, packages, manager="apt"):
        definition = simple_definition(created_paths=())
        variant = definition["variants"][0]
        variant["platform"]["package_manager"] = manager
        variant["steps"][1]["packages"] = list(packages)
        return definition

    def install_calls(self, tool="apt-get"):
        # apt-get's "-o Name=value" options (4.37) come before the action.
        return [line.split() for line in (self.state / "calls.log").read_text().splitlines()
                if re.match(rf"{tool}( -o \S+)* install ", line)]

    def intent(self):
        return next(e for e in self.journal()
                    if e.get("type") == "packages_install" and e["phase"] == "intent")

    def test_only_missing_packages_are_installed(self):
        rc, result, run = self.apply(self.definition(["libshared", "testtool"]), "apt-test")
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(result["outcome"], "installed")
        calls = self.install_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("testtool", calls[0])
        self.assertNotIn("libshared", calls[0])
        self.assertEqual(self.intent()["pre"]["already_installed"], ["libshared"])
        self.assertIn("Already installed, left untouched: libshared", run.stderr)

    def test_all_present_calls_no_package_manager(self):
        definition = self.definition(["libshared"])
        definition["variants"][0]["verification"] = [
            {"type": "packages_installed", "packages": ["libshared"]}]
        rc, result, _ = self.apply(definition, "apt-test")
        self.assertEqual(rc, 0)
        self.assertEqual(self.install_calls(), [])
        done = [e for e in self.journal() if e.get("type") == "packages_install" and e["phase"] == "done"]
        self.assertEqual(len(done), 1)

    def test_present_package_is_not_upgraded(self):
        # Asking apt for an installed package would have upgraded it.
        self.write("upgrades.json", {"libshared": "2.0"})
        definition = self.definition(["libshared"])
        definition["variants"][0]["verification"] = [
            {"type": "packages_installed", "packages": ["libshared"]}]
        rc, result, _ = self.apply(definition, "apt-test")
        self.assertEqual(rc, 0)
        self.assertEqual(self.read("packages.json")["libshared"], "1.0")
        self.assertEqual(result["upgraded_not_reverted"], [])

    def test_rollback_keeps_the_skipped_package(self):
        self.write("pkg_bins.json", {})  # executable check fails
        rc, result, _ = self.apply(self.definition(["libshared", "testtool"]), "apt-test")
        self.assertEqual(rc, 3)
        self.assertEqual(self.read("packages.json")["libshared"], "1.0")
        self.assert_back_to_snapshot()

    def test_dnf_skips_installed_packages_too(self):
        rc, result, run = self.apply(self.definition(["libshared", "testtool"], "dnf"),
                                     "apt-test", manager="dnf")
        self.assertEqual(rc, 0, run.stderr)
        calls = self.install_calls("dnf")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("libshared", calls[0])


def dnf_vendor_definition():
    return {
        "schema_version": "1.0", "id": "vendorapp", "display_name": "Vendor App",
        "category": "tool", "depends_on": [],
        "variants": [{
            "variant_id": "dnf-vendor", "support": "supported",
            "platform": {"package_manager": "dnf"},
            "detection": {"packages_all": ["vendorapp"]},
            "conflicts": {},
            "steps": [
                {"type": "dnf_vendor_repository", "repository_id": "vendor-stable",
                 "baseurl": "https://repo.example.com/el/10/x86_64/stable",
                 "key_url": "https://repo.example.com/gpg", "key_fingerprint": FPR,
                 "key_path": "/etc/pki/rpm-gpg/vendor.asc",
                 "repo_path": "/etc/yum.repos.d/vendor.repo"},
                {"type": "package_metadata_refresh"},
                {"type": "packages_install", "role": "main", "packages": ["vendorapp"]},
                {"type": "service_enable", "unit": "vendorapp.service"},
                {"type": "service_start", "unit": "vendorapp.service"},
            ],
            "verification": [
                {"type": "packages_installed", "packages": ["vendorapp"]},
                {"type": "service_active", "unit": "vendorapp.service"},
            ],
            "rollback": {"created_paths": []},
        }],
    }


class DnfVendorRepositoryTests(FakeServer):
    """dnf vendor repositories (4.36): DSSA's own repo file, rpm key undo."""

    KEY = f"gpg-pubkey-{FPR[-8:].lower()}-5a1b2c3d"

    def setUp(self):
        super().setUp()
        # A real EL system has these folders.
        (self.root / "etc/yum.repos.d").mkdir(parents=True)
        (self.root / "etc/pki/rpm-gpg").mkdir(parents=True)

    def test_missing_repo_folder_is_refused(self):
        (self.root / "etc/yum.repos.d").rmdir()
        rc, result, run = self.apply_dnf()
        self.assertEqual(rc, 3)
        self.assertIn("not a normal dnf system", run.stderr)
        self.assertFalse((self.root / "etc/yum.repos.d").exists())

    def apply_dnf(self):
        return self.apply(dnf_vendor_definition(), "dnf-vendor", manager="dnf")

    def repo(self):
        return self.root / "etc/yum.repos.d/vendor.repo"

    def test_install_writes_its_own_repo_file_and_checked_key(self):
        rc, result, run = self.apply_dnf()
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(result["outcome"], "installed")
        self.assertEqual(self.repo().read_text(), (
            "[vendor-stable]\n"
            "name=vendor-stable (managed by DSSA)\n"
            "baseurl=https://repo.example.com/el/10/x86_64/stable\n"
            "enabled=1\n"
            "gpgcheck=1\n"
            "gpgkey=file:///etc/pki/rpm-gpg/vendor.asc\n"))
        self.assertEqual((self.root / "etc/pki/rpm-gpg/vendor.asc").read_text(), "mock key\n")
        self.assertEqual(self.read("rpm_keys.json"), [self.KEY])
        intent = next(e for e in self.journal()
                      if e.get("type") == "dnf_vendor_repository" and e["phase"] == "intent")
        self.assertEqual(intent["pre"]["rpm_keys"], [])
        self.assertEqual(intent["pre"]["source"]["state"], "absent")

    def test_rollback_removes_repo_key_file_and_rpm_key(self):
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, result, _ = self.apply_dnf()
        self.assertEqual(rc, 3)
        self.assertTrue(result["rollback"]["complete"], result)
        actions = {(a["action"], a["target"]): a["result"] for a in result["rollback"]["actions"]}
        self.assertEqual(actions[("remove_file", "/etc/yum.repos.d/vendor.repo")], "ok")
        self.assertEqual(actions[("remove_file", "/etc/pki/rpm-gpg/vendor.asc")], "ok")
        self.assertEqual(actions[("remove_rpm_key", self.KEY)], "ok")
        self.assertNotIn("refresh_lists", [a for a, _ in actions])
        self.assertFalse(self.repo().exists())
        self.assertEqual(self.read("rpm_keys.json"), [])
        # rpm names packages name.arch in the snapshot; compare the start state.
        self.assertEqual(self.read("packages.json"), INITIAL_PACKAGES)

    def test_key_present_before_the_run_is_kept(self):
        self.write("rpm_keys.json", [self.KEY])
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, result, _ = self.apply_dnf()
        self.assertEqual(rc, 3)
        self.assertEqual(self.read("rpm_keys.json"), [self.KEY])
        self.assertNotIn("remove_rpm_key", [a["action"] for a in result["rollback"]["actions"]])

    def test_failed_key_removal_makes_rollback_incomplete(self):
        self.write("fail.json", {"start": ["vendorapp.service"], "rpm_e": True})
        rc, result, _ = self.apply_dnf()
        self.assertEqual(rc, 4)
        self.assertTrue(any("signing key" in i for i in result["rollback"]["issues"]))

    def test_fingerprint_mismatch_writes_nothing(self):
        (self.state / "fingerprint").write_text("F" * 40 + "\n")
        rc, result, _ = self.apply_dnf()
        self.assertEqual(rc, 3)
        self.assertIn("Step 1 (dnf_vendor_repository) failed", result["failure"])
        self.assertFalse(self.repo().exists())
        self.assertFalse((self.root / "etc/pki/rpm-gpg/vendor.asc").exists())

    def test_existing_repo_file_is_restored(self):
        self.repo().write_text("original\n")
        self.write("fail.json", {"install": ["vendorapp"]})
        rc, _, _ = self.apply_dnf()
        self.assertEqual(rc, 3)
        self.assertEqual(self.repo().read_text(), "original\n")

    def test_dnf_step_refused_on_apt(self):
        definition = dnf_vendor_definition()
        definition["variants"][0]["platform"]["package_manager"] = "apt"
        result = self.bash(f"""
source {ROOT}/backend/linux/engine/definitions.sh
validate_component_definition {shlex.quote(json.dumps(definition))}
""")
        self.assertNotEqual(result.returncode, 0)


class SideBySideVersionTests(FakeServer):
    """Versions installed next to an existing one, like kernels (4.36)."""

    def setUp(self):
        super().setUp()
        self.write("packages.json", {**INITIAL_PACKAGES, "kernel-core": "1.0"})
        # Installing testtool pulls in a second kernel, as kernel-modules-extra
        # did on alma10; libshared is a real upgrade.
        self.write("side_deps.json", {"testtool": {"kernel-core": "2.0"}})
        self.write("upgrades.json", {"libshared": "1.1"})

    def definition(self):
        d = simple_definition()
        d["variants"][0]["platform"]["package_manager"] = "dnf"
        return d

    def test_new_version_is_journaled_not_reported_as_upgrade(self):
        rc, result, run = self.apply(self.definition(), "apt-test", manager="dnf")
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(result["upgraded_not_reverted"],
                         [{"package": "libshared.x86_64", "before": "0:1.0", "after": "0:1.1"}])
        done = next(e for e in self.journal()
                    if e.get("type") == "packages_install" and e["phase"] == "done")
        self.assertEqual(done["detail"]["added_versions"],
                         [{"package": "kernel-core.x86_64", "version": "0:2.0"}])

    def test_rollback_removes_the_added_version_only(self):
        self.write("fail.json", {"install": ["testtool"]})
        rc, result, run = self.apply(self.definition(), "apt-test", manager="dnf")
        self.assertEqual(rc, 3, run.stderr)
        self.assertTrue(result["rollback"]["complete"], result)
        actions = {(a["action"], a["target"]): a["result"] for a in result["rollback"]["actions"]}
        self.assertEqual(actions[("remove_version", "kernel-core-0:2.0.x86_64")], "ok")
        self.assertEqual(self.read("side.json"), {"kernel-core": []})
        self.assertEqual(self.read("packages.json")["kernel-core"], "1.0")

    def test_running_kernel_is_never_removed(self):
        self.write("fail.json", {"install": ["testtool"]})
        rc, result, run = self.apply(self.definition(), "apt-test", manager="dnf",
                                     prelude='uname() { [[ "$1" == -r ]] && echo 2.0.x86_64 || command uname "$@"; }')
        self.assertEqual(rc, 4, run.stderr)
        self.assertFalse(result["rollback"]["complete"])
        self.assertTrue(any("running kernel" in i for i in result["rollback"]["issues"]), result)
        self.assertEqual(self.read("side.json"), {"kernel-core": ["2.0"]})

    def test_apt_records_no_versions(self):
        rc, _, run = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 0, run.stderr)
        versions = self.tx() / "pkgsets"
        self.assertEqual([f.read_text() for f in sorted(versions.glob("*.versions"))], ["", ""])


class FirewalldTests(FakeServer):
    """firewalld must see iptables installed by the run, and Docker's
    saved zone must not outlive a rollback (4.36, alma10)."""

    ZONE = "/etc/firewalld/zones/docker.xml"

    def setUp(self):
        super().setUp()
        self.write("units.json", {**INITIAL_UNITS,
                                  "firewalld.service": {"file_state": "enabled", "active": "active"}})
        self.write("deps.json", {"vendorapp": ["iptables-nft"]})
        self.write("start_files.json", {"vendorapp.service": [self.ZONE]})

    def definition(self):
        d = vendor_definition()
        variant = d["variants"][0]
        variant["steps"].insert(5, {"type": "firewalld_reload", "when_packages_added": ["iptables-nft"]})
        variant["rollback"]["created_paths"].append(self.ZONE)
        return d

    def run_it(self):
        paths = json.dumps(json.loads(PATHS) + [self.ZONE])
        return self.apply(self.definition(), "apt-vendor", paths=paths)

    def reloads(self):
        return [l for l in (self.state / "calls.log").read_text().splitlines()
                if l == "firewall-cmd --reload"]

    def step(self):
        return next(e for e in self.journal()
                    if e.get("type") == "firewalld_reload" and e["phase"] == "intent")

    def test_reloads_when_the_run_installed_iptables(self):
        rc, result, run = self.run_it()
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(self.step()["pre"],
                         {"firewalld": "active", "packages_added": ["iptables-nft"], "reload": True})
        self.assertEqual(len(self.reloads()), 1)

    def test_no_reload_when_iptables_was_already_there(self):
        self.write("packages.json", {**INITIAL_PACKAGES, "iptables-nft": "1.0"})
        rc, _, run = self.run_it()
        self.assertEqual(rc, 0, run.stderr)
        self.assertFalse(self.step()["pre"]["reload"])
        self.assertEqual(self.reloads(), [])

    def test_no_reload_when_firewalld_is_not_running(self):
        self.write("units.json", INITIAL_UNITS)
        rc, _, run = self.run_it()
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(self.step()["pre"]["firewalld"], "inactive")
        self.assertEqual(self.reloads(), [])

    def test_failed_reload_fails_the_step_and_rolls_back(self):
        self.write("fail.json", {"firewalld_reload": True})
        rc, result, run = self.run_it()
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(result["failure"], "Step 6 (firewalld_reload) failed.")
        self.assert_back_to_snapshot()

    def test_rollback_removes_the_zone_and_reloads(self):
        # Like alma10: the service writes its zone, then fails to start.
        self.write("fail.json", {"start": ["vendorapp.service"]})
        (self.root / "etc/firewalld/zones").mkdir(parents=True)
        self.write("start_files.json", {})
        rc, result, run = self.run_it()
        self.assertEqual(rc, 3, run.stderr)
        self.assertTrue(result["rollback"]["complete"], result)
        actions = [(a["action"], a["target"], a["result"]) for a in result["rollback"]["actions"]]
        self.assertIn(("reload", "firewalld", "ok"), actions)
        self.assert_back_to_snapshot()

    def test_zone_written_by_the_service_is_removed(self):
        # The service starts and writes its zone; verification then fails.
        d = self.definition()
        d["variants"][0]["verification"].append({"type": "packages_installed", "packages": ["missing-pkg"]})
        paths = json.dumps(json.loads(PATHS) + [self.ZONE])
        rc, result, run = self.apply(d, "apt-vendor", paths=paths)
        self.assertEqual(rc, 3, run.stderr)
        self.assertFalse((self.root / self.ZONE.lstrip("/")).exists())
        actions = [(a["action"], a["target"]) for a in result["rollback"]["actions"]]
        self.assertIn(("remove_path", self.ZONE), actions)
        self.assertLess(actions.index(("remove_path", self.ZONE)), actions.index(("reload", "firewalld")))

    def test_zone_that_existed_before_is_left_in_place(self):
        zone = self.root / self.ZONE.lstrip("/")
        zone.parent.mkdir(parents=True)
        zone.write_text("operator zone\n")
        self.write("packages.json", {**INITIAL_PACKAGES, "iptables-nft": "1.0"})
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, result, run = self.run_it()
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(zone.read_text(), "operator zone\n")
        self.assertNotIn("reload", [a["action"] for a in result["rollback"]["actions"]])


class ListRefreshTests(FakeServer):
    """A repository undo refreshes apt's lists once (4.35 D)."""

    def updates(self):
        return [l for l in (self.state / "calls.log").read_text().splitlines()
                if re.match(r"apt-get( -o \S+)* update", l)]

    def test_repository_undo_refreshes_lists(self):
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3)
        actions = [(a["action"], a["result"]) for a in result["rollback"]["actions"]]
        self.assertIn(("refresh_lists", "ok"), actions)
        # Two metadata steps during install, one refresh after the undo.
        self.assertEqual(len(self.updates()), 3)
        self.assertTrue(result["rollback"]["complete"])

    def test_failed_refresh_is_noted_but_rollback_is_complete(self):
        # Updates 1 and 2 are the install's own; the third (rollback) fails.
        self.write("fail.json", {"start": ["vendorapp.service"], "update_from": 3})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertTrue(result["rollback"]["complete"])
        self.assertIn(("refresh_lists", "not refreshed"),
                      [(a["action"], a["result"]) for a in result["rollback"]["actions"]])
        self.assertTrue(any("lists not refreshed" in i for i in result["rollback"]["issues"]))

    def test_no_refresh_without_a_repository_change(self):
        self.write("pkg_bins.json", {})  # simple install, verification fails
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        self.assertNotIn("refresh_lists", [a["action"] for a in result["rollback"]["actions"]])
        self.assertEqual(len(self.updates()), 1)

    def test_failed_removal_gives_the_dpkg_hint(self):
        self.write("fail.json", {"start": ["vendorapp.service"], "purge": True})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 4)
        self.assertTrue(any("dpkg --configure -a" in i for i in result["rollback"]["issues"]))


class RollbackTests(FakeServer):

    def test_verification_failure_rolls_back(self):
        self.write("pkg_bins.json", {})  # executable check will fail
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertIn("Verification failed: executable", result["failure"])
        self.assert_back_to_snapshot()
        self.assertFalse((self.root / "var/lib/testtool").exists())
        removed = {a["target"] for a in result["rollback"]["actions"] if a["action"] == "remove_package"}
        self.assertEqual(removed, {"testtool", "libtt"})

    def test_partial_install_failure_removes_pulled_dependencies(self):
        self.write("fail.json", {"install": ["testtool"]})
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertIn("Step 2 (packages_install) failed", result["failure"])
        self.assert_back_to_snapshot()

    def test_preexisting_dependency_is_kept(self):
        self.write("deps.json", {"testtool": ["libshared", "libtt"]})
        self.write("pkg_bins.json", {})
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        self.assertEqual(self.read("packages.json")["libshared"], "1.0")
        self.assert_back_to_snapshot()

    def test_upgrade_is_not_reverted_on_rollback(self):
        self.write("upgrades.json", {"libshared": "2.0"})
        self.write("pkg_bins.json", {})
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        packages = self.read("packages.json")
        self.assertEqual(packages["libshared"], "2.0")
        self.assertNotIn("testtool", packages)
        self.assertEqual(result["upgraded_not_reverted"][0]["package"], "libshared")

    def test_fingerprint_mismatch_leaves_no_repository(self):
        (self.state / "fingerprint").write_text("F" * 40 + "\n")
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3)
        self.assertIn("apt_vendor_repository", result["failure"])
        self.assertFalse((self.root / "etc/apt/keyrings").exists())
        self.assertFalse((self.root / "etc/apt/sources.list.d/vendor.sources").exists())
        self.assert_back_to_snapshot()
        reasons = [e["detail"]["reason"] for e in self.journal() if e["phase"] == "failed"]
        self.assertEqual(reasons, ["Signing key fingerprint mismatch."])

    def test_service_start_failure_undoes_everything(self):
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3, result)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertFalse((self.root / "etc/apt/keyrings/vendor.asc").exists())
        self.assertFalse((self.root / "etc/apt/sources.list.d/vendor.sources").exists())
        self.assertFalse((self.root / "etc/apt/keyrings").exists())
        self.assertFalse((self.root / "var/lib/vendorapp").exists())
        self.assert_back_to_snapshot()

    def test_existing_repository_file_is_restored_from_backup(self):
        source = self.root / "etc/apt/sources.list.d/vendor.sources"
        source.write_text("original\n")
        self.write("fail.json", {"install": ["vendorapp"]})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3)
        self.assertEqual(source.read_text(), "original\n")

    def test_path_present_before_the_run_is_kept(self):
        existing = self.root / "var/lib/testtool"
        existing.mkdir(parents=True)
        (existing / "data").write_text("keep me\n")
        self.write("pkg_bins.json", {})
        rc, _, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3)
        self.assertEqual((existing / "data").read_text(), "keep me\n")

    def test_protected_path_is_never_removed(self):
        self.write("pkg_paths.json", {"testtool": ["/opt"]})
        self.write("pkg_bins.json", {})
        rc, result, _ = self.apply(simple_definition(created_paths=("/opt",)), "apt-test")
        self.assertEqual(rc, 4)
        self.assertEqual(result["outcome"], "rollback_incomplete")
        self.assertTrue((self.root / "opt").exists())
        self.assertTrue(any("protected" in issue for issue in result["rollback"]["issues"]))


class RollbackFailureTests(FakeServer):

    def test_failed_package_removal_is_incomplete_and_skips_service_restore(self):
        self.write("fail.json", {"start": ["vendorapp.service"], "purge": True})
        rc, result, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 4)
        self.assertEqual(result["outcome"], "rollback_incomplete")
        issues = result["rollback"]["issues"]
        self.assertIn("Service state restoration skipped: rollback is incomplete.", issues)
        self.assertTrue(any("still installed" in issue for issue in issues))
        self.assertFalse(any(a["action"].startswith("restore_") for a in result["rollback"]["actions"]))


    def test_preexisting_package_removed_by_package_manager_is_detected(self):
        self.write("pkg_bins.json", {})
        self.write("fail.json", {"purge_extra": ["libshared"]})
        rc, result, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 4)
        self.assertEqual(result["outcome"], "rollback_incomplete")
        self.assertIn("Pre-existing package is missing after rollback: libshared",
                      result["rollback"]["issues"])

    def test_dnf_removal_never_cleans_unneeded_dependencies(self):
        definition = simple_definition()
        definition["variants"][0]["platform"]["package_manager"] = "dnf"
        self.write("pkg_bins.json", {})
        rc, result, _ = self.apply(definition, "apt-test", manager="dnf")
        self.assertEqual(rc, 3, result)
        calls = (self.state / "calls.log").read_text()
        self.assertIn("dnf remove -y --setopt=clean_requirements_on_remove=False --", calls)
        # The snapshot uses rpm's name.arch form; the mock stores plain names.
        self.assertEqual(self.read("packages.json"), INITIAL_PACKAGES)
        snapshot = json.loads((self.tx() / "snapshot.json").read_text())
        self.assertEqual(sorted(snapshot["packages"]), ["base-files.x86_64", "libshared.x86_64"])


class GuardTests(FakeServer):

    def test_second_run_is_refused_while_lock_is_held(self):
        result = self.bash(f"""
begin_transaction {PLAN_A} apt '[]' || exit 90
bash -c 'set -uo pipefail; for f in "$@"; do source "$f"; done
         begin_transaction {PLAN_B} apt "[]"' _ {' '.join(shlex.quote(str(s)) for s in SOURCES)}
echo "child=$?"
""")
        self.assertIn("child=1", result.stdout)
        self.assertIn("Another DSSA run holds the lock", result.stderr)

    def test_unfinished_transaction_blocks_and_resume_rolls_back(self):
        definition = shlex.quote(json.dumps(simple_definition()))
        # Simulate a crash: component and install step began, no end.
        crash = self.bash(f"""
begin_transaction {PLAN_A} apt {shlex.quote(PATHS)} || exit 90
tx_journal {PLAN_A} '{{"component":"testtool","phase":"component_begin","variant_id":"apt-test","created_paths":["/var/lib/testtool"]}}'
_tx_exec_packages_install {PLAN_A} testtool 1 apt '{{"type":"packages_install","role":"main","packages":["testtool"]}}'
exit 0
""")
        self.assertEqual(crash.returncode, 0, crash.stderr)
        self.assertIn("testtool", self.read("packages.json"))

        blocked = self.bash(f"begin_transaction {PLAN_B} apt '[]'")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(f"Unfinished transaction requires resume rollback: {PLAN_A}", blocked.stderr)

        resumed = self.bash(f"resume_rollback {PLAN_A} apt")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        report = json.loads(resumed.stdout)
        self.assertTrue(report["resumed"])
        self.assertEqual(report["results"][0]["outcome"], "failed_rolled_back")
        self.assert_back_to_snapshot()
        self.assertFalse((self.root / "var/lib/testtool").exists())

        again = self.bash(f"begin_transaction {PLAN_B} apt '[]'")
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_interrupted_step_without_after_state_is_undone(self):
        # intent written, packages changed, then crash before 'done'.
        crash = self.bash(f"""
begin_transaction {PLAN_A} apt {shlex.quote(PATHS)} || exit 90
tx_journal {PLAN_A} '{{"component":"testtool","phase":"component_begin","variant_id":"apt-test","created_paths":[]}}'
tx_package_set apt > "$(_tx_dir {PLAN_A})/pkgsets/testtool-1.before.json"
tx_journal {PLAN_A} '{{"component":"testtool","step":1,"type":"packages_install","phase":"intent","pre":{{"packages_before":"testtool-1.before.json","packages":["testtool"]}}}}'
apt-get install -y -- testtool
""")
        self.assertEqual(crash.returncode, 0, crash.stderr)
        resumed = self.bash(f"resume_rollback {PLAN_A} apt")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assert_back_to_snapshot()

    def test_unsupported_step_is_refused_before_any_change(self):
        definition = simple_definition()
        definition["variants"][0]["steps"].append({
            "type": "managed_file", "path": "/etc/testtool.conf",
            "template": "testtool/conf", "mode": "0644", "owner": "root:root"})
        result = self.bash(f"""
begin_transaction {PLAN_A} apt '[]' || exit 90
apply_component {PLAN_A} apt {shlex.quote(json.dumps(definition))} apt-test
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Step type not supported by this engine: managed_file", result.stderr)
        self.assertEqual(self.read("packages.json"), INITIAL_PACKAGES)
        self.assertFalse(any(e["phase"] == "component_begin" for e in self.journal()))

    def test_draft_variant_is_refused(self):
        result = self.bash(f"""
begin_transaction {PLAN_A} apt '[]' || exit 90
apply_component {PLAN_A} apt {shlex.quote(json.dumps(simple_definition(support="draft")))} apt-test
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is a draft; installation is not allowed", result.stderr)
        self.assertEqual(self.read("packages.json"), INITIAL_PACKAGES)

    def test_one_transaction_per_plan(self):
        self.apply(simple_definition(), "apt-test")
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("A transaction already exists for this plan", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "running as root")
    def test_real_system_requires_root(self):
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'", with_root=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Root privileges are required", result.stderr)

    def test_old_finished_transactions_are_removed(self):
        old = self.root / "var/lib/dssa/transactions" / PLAN_B
        old.mkdir(parents=True)
        # As an earlier run leaves them: private (4.39 refuses anything else).
        for folder in (old.parent.parent, old.parent):
            folder.chmod(0o700)
        (old / "journal.jsonl").write_text("")
        (old / "result.json").write_text("{}")
        past = time.time() - 31 * 86400
        os.utime(old, (past, past))
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())


class StdinSafetyTests(FakeServer):
    """On a server the bundle arrives on stdin (ssh ... 'bash -s').

    Any command that reads stdin would swallow the rest of the script,
    so every engine path must leave stdin untouched.
    """

    SENTINEL = "SENTINEL-REST-OF-BUNDLE"

    def run_with_stdin(self, body):
        env = {
            "PATH": f"{self.state / 'bin'}:{self.tools}:/usr/bin:/bin",
            "MOCK_STATE": str(self.state), "HOME": self.tmp.name,
            "DSSA_ROOT": str(self.root), "DSSA_SERVICE_SETTLE_SECONDS": "0",
        }
        sources = "\n".join(f"source {shlex.quote(str(s))}" for s in SOURCES)
        script = f"set -uo pipefail\n{sources}\n{body}\nIFS= read -r rest || rest=''\necho \"REST=$rest\"\n"
        return subprocess.run(
            ["bash", "-c", script], input=self.SENTINEL + "\n",
            text=True, capture_output=True, timeout=60, env=env, check=False,
        )

    def assert_stdin_untouched(self, body):
        result = self.run_with_stdin(body)
        self.assertIn(f"REST={self.SENTINEL}", result.stdout, result.stderr[-2000:])

    def test_successful_apply_leaves_stdin(self):
        self.assert_stdin_untouched(f"""
begin_transaction {PLAN_A} apt {shlex.quote(PATHS)}
apply_component {PLAN_A} apt {shlex.quote(json.dumps(vendor_definition()))} apt-vendor >/dev/null
end_transaction {PLAN_A} '[]'
""")

    def test_rollback_leaves_stdin(self):
        self.write("fail.json", {"start": ["vendorapp.service"]})
        self.assert_stdin_untouched(f"""
begin_transaction {PLAN_A} apt {shlex.quote(PATHS)}
apply_component {PLAN_A} apt {shlex.quote(json.dumps(vendor_definition()))} apt-vendor >/dev/null
end_transaction {PLAN_A} '[]'
""")

    def test_verify_and_resume_leave_stdin(self):
        self.write("packages.json", {**INITIAL_PACKAGES, "testtool": "1.0"})
        self.assert_stdin_untouched(
            f"verify_component apt {shlex.quote(json.dumps(simple_definition()))} apt-test >/dev/null")
        self.assert_stdin_untouched(f"""
begin_transaction {PLAN_B} apt '[]'
tx_journal {PLAN_B} '{{"component":"testtool","phase":"component_begin","variant_id":"apt-test","created_paths":[]}}'
exec 8>&-
resume_rollback {PLAN_B} apt >/dev/null
""")

    def test_planning_engine_leaves_stdin(self):
        planner = ROOT / "backend/linux/engine/plan.sh"
        definitions = ROOT / "definitions/components"
        self.assert_stdin_untouched(f"""
source {shlex.quote(str(planner))}
req="$(build_plan_request rocky10 SHA256:{'A' * 43} {shlex.quote(str(definitions))} git nginx docker)"
build_definition_plan "$req" apt '{{"panels":[]}}' '{{"listening_tcp_ports":[]}}' >/dev/null
""")


class VerifyOnlyTests(FakeServer):

    def test_verify_component_changes_nothing(self):
        self.write("packages.json", {**INITIAL_PACKAGES, "testtool": "1.0"})
        (self.state / "bin/testtool").write_text("#!/bin/sh\necho v\n")
        (self.state / "bin/testtool").chmod(0o755)
        ok = self.bash(f"verify_component apt {shlex.quote(json.dumps(simple_definition()))} apt-test")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["outcome"], "verified")

        (self.state / "bin/testtool").unlink()
        bad = self.bash(f"verify_component apt {shlex.quote(json.dumps(simple_definition()))} apt-test")
        self.assertEqual(bad.returncode, 5)
        self.assertEqual(json.loads(bad.stdout)["outcome"], "verification_failed")
        self.assertIn("testtool", self.read("packages.json"))


class AptSafetyTests(FakeServer):
    """apt never waits forever on a lock, and a bad refresh fails (4.37)."""

    def apt_calls(self):
        return [l for l in (self.state / "calls.log").read_text().splitlines()
                if l.startswith("apt-get")]

    def test_every_apt_call_has_a_lock_timeout_and_update_fails_on_warnings(self):
        rc, _, _ = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 0)
        calls = self.apt_calls()
        self.assertIn("apt-get -o DPkg::Lock::Timeout=60 update --error-on=any", calls)
        self.assertIn("apt-get -o DPkg::Lock::Timeout=60 install -y -- testtool", calls)
        self.assertTrue(all(c.startswith("apt-get -o DPkg::Lock::Timeout=60 ") for c in calls), calls)

    def test_rollback_purge_and_list_refresh_have_the_timeout(self):
        self.write("fail.json", {"start": ["vendorapp.service"]})
        rc, _, _ = self.apply(vendor_definition(), "apt-vendor")
        self.assertEqual(rc, 3)
        calls = self.apt_calls()
        self.assertTrue(any(" purge -y -- " in c for c in calls), calls)
        self.assertTrue(all(c.startswith("apt-get -o DPkg::Lock::Timeout=60 ") for c in calls), calls)
        self.assertEqual(calls[-1], "apt-get -o DPkg::Lock::Timeout=60 update --error-on=any")

    def test_invalid_timeout_falls_back_to_sixty(self):
        rc, _, _ = self.apply(simple_definition(), "apt-test", prelude="DSSA_APT_LOCK_TIMEOUT='5; reboot'")
        self.assertEqual(rc, 0)
        self.assertTrue(all("DPkg::Lock::Timeout=60 " in c for c in self.apt_calls()))

    def test_custom_timeout_is_used(self):
        rc, _, _ = self.apply(simple_definition(), "apt-test", prelude="DSSA_APT_LOCK_TIMEOUT=5")
        self.assertEqual(rc, 0)
        self.assertTrue(all("DPkg::Lock::Timeout=5 " in c for c in self.apt_calls()))

    def test_lock_timeout_fails_the_refresh_and_rolls_back(self):
        self.write("fail.json", {"apt_lock": True})
        rc, result, run = self.apply(simple_definition(), "apt-test")
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertIn("Could not get lock", run.stderr)
        self.assertNotIn("testtool", self.read("packages.json"))
        self.assert_back_to_snapshot()


class ServiceStabilityTests(FakeServer):
    """A started service must stay up for the settle time (4.37)."""

    UNIT = "vendorapp.service"

    def run_it(self, settle=2):
        return self.apply(vendor_definition(), "apt-vendor",
                          prelude=f"DSSA_SERVICE_SETTLE_SECONDS={settle}")

    def start_entries(self, phase):
        return [e for e in self.journal() if e.get("type") == "service_start" and e["phase"] == phase]

    def test_stable_service_passes_and_records_the_watch(self):
        rc, result, run = self.run_it()
        self.assertEqual(rc, 0, run.stderr)
        self.assertEqual(result["outcome"], "installed")
        detail = self.start_entries("done")[0]["detail"]
        self.assertEqual(detail, {"stable": True, "unit": self.UNIT, "seconds": 2,
                                  "main_pid": "4242", "restarts": 0})

    def test_crash_loop_fails_the_step_and_rolls_back(self):
        # Restart= brings the unit back each time: active, but NRestarts grows.
        self.write("fail.json", {"unstable": [self.UNIT]})
        rc, result, run = self.run_it()
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        failed = self.start_entries("failed")[0]["detail"]
        self.assertFalse(failed["stable"])
        self.assertIn("restarted", failed["reason"])
        self.assertIn(f"Service {self.UNIT} is not stable", run.stderr)
        self.assertIn(f"mock journal: --unit={self.UNIT} --lines=20 --no-pager", run.stderr)
        self.assertEqual(self.start_entries("done"), [])
        self.assert_back_to_snapshot()

    def test_service_without_main_process_fails(self):
        self.write("fail.json", {"no_pid": [self.UNIT]})
        rc, _, run = self.run_it(settle=0)
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(self.start_entries("failed")[0]["detail"]["reason"],
                         "no main process after start")
        self.assert_back_to_snapshot()

    def test_service_that_exits_fails(self):
        self.write("unit_props.json", {self.UNIT: {"sub": "exited"}})
        rc, _, run = self.run_it(settle=0)
        self.assertEqual(rc, 3, run.stderr)
        self.assertEqual(self.start_entries("failed")[0]["detail"]["reason"], "state active/exited")

    def test_oneshot_unit_only_has_to_stay_active(self):
        self.write("unit_props.json", {self.UNIT: {"type": "oneshot", "sub": "exited"}})
        self.write("fail.json", {"no_pid": [self.UNIT]})
        rc, _, run = self.run_it(settle=1)
        self.assertEqual(rc, 0, run.stderr)
        self.assertTrue(self.start_entries("done")[0]["detail"]["stable"])

    def test_service_enable_is_not_watched(self):
        rc, _, _ = self.run_it(settle=0)
        self.assertEqual(rc, 0)
        enable = [e for e in self.journal() if e.get("type") == "service_enable" and e["phase"] == "done"]
        self.assertNotIn("detail", enable[0])


class StateFolderTests(FakeServer):
    """The state folder is refused, never repaired, when unsafe (4.39)."""

    def state_dir(self):
        return self.root / "var/lib/dssa"

    def assert_refused(self, result, text):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(text, result.stderr)
        self.assertIn("Nothing was changed", result.stderr)
        self.assertEqual(self.read("packages.json"), INITIAL_PACKAGES)

    def test_symlinked_state_folder_is_refused(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        (self.root / "var/lib").mkdir(parents=True)
        self.state_dir().symlink_to(elsewhere)
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'")
        self.assert_refused(result, "var/lib/dssa is a symlink")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_symlinked_parent_or_subfolder_is_refused(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        (elsewhere / "lib").mkdir(parents=True)
        (self.root / "var").symlink_to(elsewhere)
        self.assert_refused(self.bash(f"begin_transaction {PLAN_A} apt '[]'"), "/var is a symlink")
        (self.root / "var").unlink()

        self.state_dir().mkdir(parents=True, mode=0o700)
        self.state_dir().chmod(0o700)
        (self.state_dir() / "transactions").symlink_to(elsewhere)
        self.assert_refused(self.bash(f"begin_transaction {PLAN_A} apt '[]'"), "transactions is a symlink")
        self.assertFalse((elsewhere / PLAN_A).exists())

    def test_group_writable_state_folder_is_refused_not_repaired(self):
        self.state_dir().mkdir(parents=True)
        self.state_dir().chmod(0o770)
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'")
        self.assert_refused(result, "writable by group or others (mode 770)")
        self.assertEqual(stat.S_IMODE(self.state_dir().stat().st_mode), 0o770)
        self.assertFalse((self.state_dir() / "transactions").exists())

    def test_folder_owned_by_someone_else_is_refused(self):
        self.state_dir().mkdir(parents=True)
        self.state_dir().chmod(0o700)
        # A non-root test cannot chown; a stat that reports another owner
        # for the state folder stands in for it.
        fake = self.state / "bin/stat"
        fake.write_text('#!/bin/sh\ncase "$*" in *var/lib/dssa*"%u %a"*|*"%u %a"*var/lib/dssa*) '
                        'echo "4321 700"; exit 0 ;; esac\nexec /usr/bin/stat "$@"\n')
        fake.chmod(0o755)
        result = self.bash(f"begin_transaction {PLAN_A} apt '[]'")
        self.assert_refused(result, "is owned by uid 4321")

    def test_resume_rollback_checks_the_folder_too(self):
        self.apply(simple_definition(), "apt-test")
        self.state_dir().chmod(0o757)
        result = self.bash(f"resume_rollback {PLAN_A} apt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("writable by group or others", result.stderr)

    def test_private_folder_from_an_earlier_run_is_accepted(self):
        self.apply(simple_definition(), "apt-test")
        code, result, _ = self.apply(simple_definition(), "apt-test", plan_id=PLAN_B)
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(self.state_dir().stat().st_mode), 0o700)


class VersionPatternTests(FakeServer):
    """An executable check can require its version output to match (4.39)."""

    def definition(self, pattern):
        definition = simple_definition()
        check = definition["variants"][0]["verification"][1]
        check["version_pattern"] = pattern
        return definition

    def verify(self, output, pattern=r"^testtool [0-9]+\."):
        self.write("packages.json", {**INITIAL_PACKAGES, "testtool": "1.0"})
        binary = self.state / "bin/testtool"
        binary.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(output)}\nexit 0\n")
        binary.chmod(0o755)
        return self.bash(f"verify_component apt {shlex.quote(json.dumps(self.definition(pattern)))} apt-test")

    def test_matching_output_passes(self):
        result = self.verify("testtool 2.4.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "verified")

    def test_usage_text_with_exit_0_fails(self):
        result = self.verify("usage: testtool [options] <file>")
        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stdout)["outcome"], "verification_failed")
        self.assertIn('Version output of testtool does not match "^testtool [0-9]+\\.": usage: testtool',
                      result.stderr)

    def test_only_the_first_line_counts(self):
        result = self.verify("Warning: old config\ntesttool 2.4.1")
        self.assertEqual(result.returncode, 5)

    def test_failed_match_after_install_rolls_back(self):
        self.write("pkg_bins.json", {})
        prelude = ("printf '#!/bin/sh\\necho usage: testtool\\n' > \"$MOCK_STATE/bin/testtool\"; "
                   "chmod 755 \"$MOCK_STATE/bin/testtool\"")
        code, result, _ = self.apply(self.definition(r"^testtool [0-9]+"), "apt-test", prelude=prelude)
        self.assertEqual(code, 3)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertNotIn("testtool", self.read("packages.json"))

    def test_invalid_pattern_fails_validation(self):
        for pattern in ("(", "a;b", "x`y", ""):
            with self.subTest(pattern=pattern):
                result = self.bash(f"validate_component_definition {shlex.quote(json.dumps(self.definition(pattern)))}")
                self.assertNotEqual(result.returncode, 0)


class AuditPathTests(unittest.TestCase):
    """The audit scripts honour XDG_STATE_HOME like the approvals (4.37)."""

    def test_logging_scripts_use_xdg_state_home(self):
        logging = ROOT / "backend/linux/logging"
        with tempfile.TemporaryDirectory() as tmp:
            home, xdg = Path(tmp) / "home", Path(tmp) / "xdg"
            home.mkdir()
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_STATE_HOME": str(xdg)}
            audit_dir = xdg / "developer-server-setup-assistant/audit"
            subprocess.run(["bash", str(logging / "rotate.sh")], env=env, check=True,
                           capture_output=True, text=True)
            self.assertTrue(audit_dir.is_dir())
            retention = subprocess.run(["bash", str(logging / "retention.sh")], env=env,
                                       check=True, capture_output=True, text=True)
            self.assertIn("Audit retention", retention.stdout)
            subprocess.run(["bash", str(logging / "audit.sh")], env=env, input="{}",
                           capture_output=True, text=True, check=False)
            self.assertFalse((home / ".local").exists())
            del env["XDG_STATE_HOME"]
            subprocess.run(["bash", str(logging / "rotate.sh")], env=env, check=True,
                           capture_output=True, text=True)
            self.assertTrue((home / ".local/state/developer-server-setup-assistant/audit").is_dir())


if __name__ == "__main__":
    unittest.main()
