"""Journaled execution and rollback against a mocked system (4.33).

Every test builds a fake server: a temporary DSSA_ROOT, mock package
manager and systemd (tests/backend/fixtures/mock_system.py) on PATH, and
seeded packages/units. After each scenario the real package and unit
state is compared with the pre-run snapshot.
"""

import copy
import json
import os
import shlex
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
TOOLS = ("apt-get", "dnf", "dpkg-query", "dpkg", "rpm", "systemctl", "curl", "gpg")
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
        }
        if with_root:
            env["DSSA_ROOT"] = str(self.root)
        sources = "\n".join(f"source {shlex.quote(str(s))}" for s in SOURCES)
        # umask 022 as under sudo: DSSA's files must still be private.
        return subprocess.run(
            ["bash", "-c", f"set -uo pipefail\numask 022\n{sources}\n{body}"],
            text=True, capture_output=True, timeout=60, env=env, check=False,
        )

    def apply(self, definition, variant_id, plan_id=PLAN_A, manager="apt"):
        result = self.bash(f"""
begin_transaction {plan_id} {manager} {shlex.quote(PATHS)} || exit 90
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
        return [line.split() for line in (self.state / "calls.log").read_text().splitlines()
                if line.startswith(f"{tool} install")]

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
            "DSSA_ROOT": str(self.root),
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


if __name__ == "__main__":
    unittest.main()
