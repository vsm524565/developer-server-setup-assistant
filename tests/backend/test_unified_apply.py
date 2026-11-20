"""Unified plan -> approve -> apply against a mocked server (4.34).

The real dssa-run.sh, engine and Python bridge run; only ssh, sudo, ss
and the package/systemd tools are mocked. The mock ssh runs the "remote"
side locally and enforces host-key pinning like OpenSSH.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = ROOT / "backend/linux/bin/dssa-run.sh"
MOCK_SYSTEM = ROOT / "tests/backend/fixtures/mock_system.py"
MOCK_SSH = ROOT / "tests/backend/fixtures/mock_ssh.py"
TOOLS = ("apt-get", "dnf", "dpkg-query", "dpkg", "rpm", "systemctl", "journalctl", "curl", "gpg")
HOST = "192.0.2.10"

from bridge.components import run  # noqa: E402
from bridge.components.approval import (  # noqa: E402
    ApprovalError, approve_plan, consume_approval, known_host_fingerprints, plan_digest,
)


def definition(component_id, package=None, depends_on=(), support="supported", hook=None):
    package = package or component_id
    verification = [
        {"type": "packages_installed", "packages": [package]},
        {"type": "executable", "name": package, "version_args": ["--version"]},
    ]
    variant = {
        "variant_id": f"apt-{component_id}", "support": support,
        "platform": {"package_manager": "apt"},
        "detection": {"packages_all": [package]}, "conflicts": {},
        "steps": [{"type": "package_metadata_refresh"},
                  {"type": "packages_install", "role": "main", "packages": [package]}],
        "verification": verification,
        "rollback": {"created_paths": []},
    }
    if hook:
        variant["hooks"] = {"verify": hook}
        verification.append({"type": "hook", "name": hook})
    return {
        "schema_version": "1.0", "id": component_id, "display_name": component_id.title(),
        "category": "tool",
        "depends_on": [{"id": d, "required": True} for d in depends_on],
        "variants": [variant],
    }


class Lab(unittest.TestCase):
    """A laptop, an inventory and one mocked target."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.base = base
        self.laptop = base / "laptop"
        self.remote = base / "remote-home"
        self.root = base / "root"
        self.state = base / "mock"
        self.tools = base / "tools"
        self.defs = base / "definitions"
        for d in (self.laptop / ".ssh", self.remote, self.root / "etc", self.state / "bin",
                  self.tools, self.defs):
            d.mkdir(parents=True)
        (self.root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')

        MOCK_SYSTEM.chmod(0o755)
        MOCK_SSH.chmod(0o755)
        for tool in TOOLS:
            (self.tools / tool).symlink_to(MOCK_SYSTEM)
        (self.tools / "ssh").symlink_to(MOCK_SSH)
        self.script(self.tools / "sudo", '[ "$1" = "--" ] && shift\nexec "$@"\n')
        self.script(self.tools / "ss", "exit 0\n")

        # Two real host keys, both trusted in known_hosts (like rocky10).
        self.keys = {}
        lines = []
        for name in ("a", "b"):
            path = base / f"hostkey_{name}"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
                           check=True, stdin=subprocess.DEVNULL)
            pub = " ".join(path.with_suffix(".pub").read_text().split()[:2])
            fp = subprocess.run(["ssh-keygen", "-l", "-f", str(path.with_suffix(".pub"))],
                                check=True, capture_output=True, text=True).stdout.split()[1]
            self.keys[name] = (pub, fp)
            lines.append(f"{HOST} {pub}")
        (self.laptop / ".ssh/known_hosts").write_text("\n".join(lines) + "\n")
        (self.laptop / ".ssh/id_ed25519").write_text("dummy\n")

        self.inventory = base / "servers.json"
        self.inventory.write_text(json.dumps({"schema_version": "1.0", "servers": [
            {"target_id": "lab1", "host": HOST, "ssh_user": "tester", "ssh_port": 22,
             "platform": "linux", "enabled": True, "lab": True},
            {"target_id": "prod1", "host": HOST, "ssh_user": "tester", "ssh_port": 22,
             "platform": "linux", "enabled": True},
        ]}))
        self.inventory.chmod(0o600)

        self.write_mock("packages.json", {"base-files": "1.0"})
        self.write_mock("units.json", {"ssh.service": {"file_state": "enabled", "active": "active"}})
        self.write_mock("pkg_bins.json", {p: [p] for p in ("testtool", "testapp", "othertool", "drafttool")})
        for d in (definition("testtool"), definition("testapp", depends_on=["testtool"]),
                  definition("othertool"), definition("drafttool", support="draft")):
            self.write_definition(d)

        self.env = {
            "MOCK_HOST_PUB": self.keys["a"][0],
            "MOCK_HOST_FP": self.keys["a"][1],
            "PATH": f"{self.state / 'bin'}:{self.tools}:/usr/bin:/bin",
            "HOME": str(self.laptop),
            "DSSA_SERVICE_SETTLE_SECONDS": "0",
            "XDG_STATE_HOME": str(base / "xdg"),
            "DSSA_LOG_DIR": str(base / "audit"),
            "DSSA_INVENTORY_FILE": str(self.inventory),
            "DSSA_DEFINITIONS_DIR": str(self.defs),
            "DSSA_ROOT": str(self.root),
            "DSSA_SSH_BIN": str(MOCK_SSH),  # never the real ssh
            "MOCK_STATE": str(self.state),
            "MOCK_REMOTE_HOME": str(self.remote),
            "MOCK_SSH_LOG": str(base / "ssh.log"),
        }
        # Like a desktop session: parent folders must still end up private.
        self.addCleanup(os.umask, os.umask(0o002))
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers -----------------------------------------------------------

    def script(self, path, body):
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def write_mock(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def read_mock(self, name):
        return json.loads((self.state / name).read_text())

    def write_definition(self, d):
        (self.defs / f"{d['id']}.json").write_text(json.dumps(d, indent=2) + "\n")

    def present(self, name):
        """Make the server present host key ``name``."""
        os.environ["MOCK_HOST_PUB"], os.environ["MOCK_HOST_FP"] = self.keys[name]

    def ssh_calls(self):
        log = self.base / "ssh.log"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def dssa(self, *args):
        return subprocess.run(["bash", str(RUN_SCRIPT), *args], capture_output=True, text=True,
                              timeout=120, check=False, stdin=subprocess.DEVNULL, env=dict(os.environ))

    def plan(self, *components, target="lab1", allow_draft=False):
        plan_bytes, plan = run.make_plan(target, list(components), allow_draft)
        path = run.save_plan(plan_bytes)
        return plan_bytes, plan, path

    def keys_now(self):
        return known_host_fingerprints(HOST)

    def approve(self, plan_bytes):
        approve_plan(plan_bytes, self.keys_now(), "APPROVE", operator="tester",
                     definitions_dir=str(self.defs))

    def apply(self, plan, plan_bytes):
        consume_approval(plan["plan_id"], self.keys_now(), definitions_dir=str(self.defs))
        path = Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals" / plan["plan_id"] / "plan.json"
        out = io.BytesIO()
        outcome = run.apply_plan("lab1", path, plan_digest(path.read_bytes()), plan["plan_id"], out=out)
        return outcome, out.getvalue().decode(errors="replace")

    def outcomes(self, outcome):
        return {r["component"]: r["outcome"] for r in outcome.summary["results"]}


class PlanTests(Lab):

    def test_plan_binds_the_presented_key_and_pins_it(self):
        _, plan, _ = self.plan("testtool")
        self.assertEqual(plan["target"]["host_key_sha256"], self.keys["a"][1])
        self.assertFalse(plan["allow_draft"])
        self.assertEqual(plan["runnable"], ["testtool"])
        planning = [c for c in self.ssh_calls() if c["command"] == "bash -s"]
        self.assertEqual(len(planning), 1)
        pinned = planning[0]["options"]
        self.assertEqual(pinned["HostKeyAlgorithms"], "ssh-ed25519")
        self.assertEqual(pinned["StrictHostKeyChecking"], "yes")
        self.assertFalse(any(c["tty"] for c in self.ssh_calls()), "planning never needs a terminal")

    def test_draft_is_held_without_allow_draft(self):
        _, plan, _ = self.plan("drafttool")
        self.assertEqual(plan["runnable"], [])
        self.assertEqual(plan["held"][0]["action"], "REVIEW")

    def test_allow_draft_refused_for_non_lab_target(self):
        result = self.dssa("plan", "--allow-draft", "prod1", "drafttool")
        self.assertEqual(result.returncode, 2)
        self.assertIn("lab", result.stderr)
        self.assertEqual(self.ssh_calls(), [], "refused before connecting")

    def test_allow_draft_on_lab_target_plans_install(self):
        _, plan, _ = self.plan("drafttool", allow_draft=True)
        self.assertTrue(plan["allow_draft"])
        step = plan["steps"][0]
        self.assertEqual((step["support"], step["action"]), ("draft", "INSTALL"))
        self.assertIn("Draft variant allowed", " ".join(step["reasons"]))

    def test_unknown_target_refused(self):
        result = self.dssa("plan", "nope", "testtool")
        self.assertEqual(result.returncode, 3)


class ApplyTests(Lab):

    def test_plan_approve_apply_installs_and_saves_laptop_copy(self):
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 0, terminal)
        self.assertEqual(outcome.summary["status"], "completed")
        self.assertEqual(self.outcomes(outcome), {"testtool": "installed"})
        self.assertIn("testtool", self.read_mock("packages.json"))
        self.assertNotIn("DSSA_FILE", terminal, "markers are hidden from the terminal")
        self.assertIn("step 2/2 packages_install", terminal, "the run log is streamed")

        copy = outcome.directory
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(copy.parent.stat().st_mode), 0o700, "runs/ itself is private")
        for name in ("summary.json", "run.log", "journal.jsonl", "result.json", "snapshot.json"):
            self.assertEqual(stat.S_IMODE((copy / name).stat().st_mode), 0o600, name)
        journal = [json.loads(l) for l in (copy / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(journal[-1]["phase"], "transaction_end")

        # Apply used a terminal, sudo, and the pinned key only.
        sudo_calls = [c for c in self.ssh_calls() if c["tty"]]
        self.assertEqual(len(sudo_calls), 1)
        self.assertTrue(sudo_calls[0]["command"].startswith("sudo -- bash -c"))
        self.assertNotIn("$'", sudo_calls[0]["command"], "POSIX quoting only")
        self.assertNotIn(".ssh/known_hosts", sudo_calls[0]["options"]["UserKnownHostsFile"])
        # Nothing staged is left behind.
        self.assertEqual(list((self.remote / ".cache/dssa").iterdir()), [])
        self.assertEqual(list((self.root / "var/lib/dssa/stage").iterdir()), [])

    def test_approval_is_single_use(self):
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        self.apply(plan, plan_bytes)
        with self.assertRaises(ApprovalError):
            self.apply(plan, plan_bytes)

    def test_target_refuses_a_plan_that_already_ran(self):
        plan_bytes, plan, path = self.plan("testtool")
        first = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(second.returncode, 2)
        self.assertIn("already been run", second.stdout + second.stderr)
        # 4.35: the refused bundle's root-only staged copy is removed too.
        self.assertEqual(list((self.root / "var/lib/dssa/stage").iterdir()), [])

    def test_other_trusted_host_key_is_refused_before_anything_runs(self):
        plan_bytes, plan, path = self.plan("testtool")
        before = self.read_mock("packages.json")
        self.present("b")  # also in known_hosts, but not the planned key
        result = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(result.returncode, 255)
        self.assertEqual(self.read_mock("packages.json"), before)
        self.assertFalse((self.root / "var/lib/dssa").exists())

    def test_killed_ssh_client_means_completion_unknown(self):
        plan_bytes, _, path = self.plan("testtool")
        os.environ["MOCK_SSH_KILL_TTY"] = "1"
        result = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(result.returncode, 255, "signal exits become 'connection failed'")

    def test_wrong_plan_digest_refused(self):
        plan_bytes, _, path = self.plan("testtool")
        result = self.dssa("apply", "lab1", str(path), "sha256:" + "0" * 64)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match", result.stderr)

    def test_changed_definition_refused_locally(self):
        plan_bytes, _, path = self.plan("testtool")
        d = definition("testtool")
        d["display_name"] = "Changed"
        self.write_definition(d)
        result = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed since planning", result.stderr)

    def test_world_writable_definition_refused_at_apply(self):
        plan_bytes, _, path = self.plan("testtool")
        (self.defs / "testtool.json").chmod(0o646)
        result = self.dssa("apply", "lab1", str(path), plan_digest(plan_bytes))
        self.assertEqual(result.returncode, 2)
        self.assertIn("writable by other users", result.stderr)
        self.assertFalse((self.root / "var/lib/dssa").exists(), "refused before connecting")

    def test_world_writable_definition_refused_at_plan(self):
        (self.defs / "othertool.json").chmod(0o646)
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 2)
        self.assertIn("writable by other users", result.stderr)

    def test_failure_rolls_back_skips_dependent_and_continues(self):
        plan_bytes, plan, _ = self.plan("testapp", "othertool")
        self.assertEqual(plan["runnable"], ["testtool", "testapp", "othertool"])
        self.approve(plan_bytes)
        self.write_mock("fail.json", {"install": ["testtool"]})
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 3, terminal)
        self.assertEqual(self.outcomes(outcome), {
            "testtool": "failed_rolled_back", "testapp": "not_attempted", "othertool": "installed"})
        testapp = outcome.summary["results"][1]
        self.assertEqual(testapp["blocked_by"], ["testtool"])
        self.assertEqual(outcome.summary["status"], "partial")
        packages = self.read_mock("packages.json")
        self.assertNotIn("testtool", packages)
        self.assertIn("othertool", packages)

    def test_rollback_incomplete_stops_the_whole_run(self):
        plan_bytes, plan, _ = self.plan("othertool", "testtool")
        self.approve(plan_bytes)
        # othertool installs, its executable check fails, and the undo fails.
        self.write_mock("pkg_bins.json", {"testtool": ["testtool"]})
        self.write_mock("fail.json", {"purge": True})
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 4, terminal)
        self.assertEqual(self.outcomes(outcome), {
            "othertool": "rollback_incomplete", "testtool": "not_attempted"})
        self.assertIn("rollback was incomplete", outcome.summary["results"][1]["reason"])
        self.assertEqual(outcome.summary["status"], "needs_review")
        self.assertNotIn("testtool", self.read_mock("packages.json"))

    def test_drift_skips_component_and_dependents(self):
        self.write_mock("packages.json", {"base-files": "1.0", "testtool": "1.0"})
        plan_bytes, plan, _ = self.plan("testapp", "othertool")
        self.assertEqual(plan["steps"][0]["action"], "SKIP")
        self.approve(plan_bytes)
        # Someone removes testtool by hand between approval and apply.
        self.write_mock("packages.json", {"base-files": "1.0"})
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 3, terminal)
        self.assertEqual(self.outcomes(outcome), {
            "testtool": "drift_skipped", "testapp": "not_attempted", "othertool": "installed"})
        drift = outcome.summary["results"][0]
        self.assertEqual((drift["planned"]["action"], drift["now"]["action"]), ("SKIP", "INSTALL"))
        self.assertNotIn("testapp", self.read_mock("packages.json"))

    def test_install_now_present_is_verified_not_drift(self):
        plan_bytes, plan, _ = self.plan("testapp", "othertool")
        self.approve(plan_bytes)
        # Someone installs testtool by hand: doing less than approved is allowed.
        packages = self.read_mock("packages.json")
        packages["testtool"] = "1.0"
        self.write_mock("packages.json", packages)
        bin_path = self.state / "bin/testtool"
        bin_path.write_text("#!/bin/sh\necho mock\n")
        bin_path.chmod(0o755)
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 0, terminal)
        self.assertEqual(self.outcomes(outcome), {
            "testtool": "verified", "testapp": "installed", "othertool": "installed"})
        self.assertIn("already installed at re-check", terminal)
        self.assertIn("verified only", outcome.summary["results"][0]["note"])

    def test_package_pulled_in_by_earlier_component_is_verified(self):
        # Like Docker pulling in the Compose plugin as an apt Recommends.
        self.write_mock("deps.json", {"testtool": ["testapp"]})
        plan_bytes, plan, _ = self.plan("testapp")
        self.assertEqual([(s["component"], s["action"]) for s in plan["steps"]],
                         [("testtool", "INSTALL"), ("testapp", "INSTALL")])
        self.approve(plan_bytes)
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 0, terminal)
        self.assertEqual(self.outcomes(outcome), {"testtool": "installed", "testapp": "verified"})

    def test_verification_only_plan_runs_without_approval(self):
        self.write_mock("packages.json", {"base-files": "1.0", "testtool": "1.0"})
        bin_path = self.state / "bin/testtool"
        bin_path.write_text("#!/bin/sh\necho mock\n")
        bin_path.chmod(0o755)
        plan_bytes, plan, path = self.plan("testtool")
        self.assertEqual(plan["steps"][0]["action"], "SKIP")
        out = io.BytesIO()
        outcome = run.apply_plan("lab1", path, plan_digest(plan_bytes), plan["plan_id"], out=out)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(self.outcomes(outcome), {"testtool": "verified"})

    def test_draft_installs_only_with_an_allow_draft_plan(self):
        plan_bytes, plan, _ = self.plan("drafttool", allow_draft=True)
        self.approve(plan_bytes)
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.exit_code, 0, terminal)
        self.assertEqual(self.outcomes(outcome), {"drafttool": "installed"})


class InterruptedRunTests(Lab):

    def interrupt(self, plan_id):
        """Leave an unfinished transaction, as after a target crash."""
        sources = " ".join(f"source {ROOT}/backend/linux/{f};" for f in (
            "modules/software/package_manager.sh", "engine/definitions.sh", "engine/journal.sh"))
        body = f"""{sources}
begin_transaction {plan_id} apt '[]' || exit 9
tx_journal {plan_id} '{{"component":"testtool","phase":"component_begin","variant_id":"apt-testtool","created_paths":[]}}'
# The install step is journaled, then the run stops (target crash):
# no component_end and no result.json.
_tx_exec_packages_install {plan_id} testtool 1 apt '{{"type":"packages_install","role":"main","packages":["testtool"]}}'
"""
        result = subprocess.run(["bash", "-c", body], env=dict(os.environ), capture_output=True,
                                text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unfinished_run_blocks_apply_until_resumed(self):
        old_bytes, old, _ = self.plan("testtool")
        self.interrupt(old["plan_id"])
        self.assertIn("testtool", self.read_mock("packages.json"))

        report = run.report("lab1", old["plan_id"], old["target"]["host_key_sha256"], out=io.BytesIO())
        self.assertEqual(report.status, "interrupted")

        plan_bytes, plan, _ = self.plan("othertool")
        self.approve(plan_bytes)
        refused, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(refused.exit_code, 2, terminal)
        self.assertEqual(refused.summary["status"], "refused")
        self.assertIn("resume rollback", terminal)

        resumed = run.resume_rollback("lab1", old["plan_id"], old["target"]["host_key_sha256"], out=io.BytesIO())
        self.assertEqual(resumed.exit_code, 0)
        self.assertEqual(resumed.summary["results"][0]["outcome"], "failed_rolled_back")
        self.assertNotIn("testtool", self.read_mock("packages.json"))

        again_bytes, again, _ = self.plan("othertool")
        self.approve(again_bytes)
        outcome, terminal = self.apply(again, again_bytes)
        self.assertEqual(outcome.exit_code, 0, terminal)

    def test_crash_during_apply_reports_interrupted_then_resumes(self):
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        self.write_mock("fail.json", {"crash_on_install": ["testtool"]})
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(outcome.status, "interrupted", terminal)
        self.assertEqual(outcome.exit_code, 4, "a killed run needs review")
        self.assertIsNone(outcome.summary)
        self.assertIn("testtool", self.read_mock("packages.json"))

        key = plan["target"]["host_key_sha256"]
        report = run.report("lab1", plan["plan_id"], key, out=io.BytesIO())
        self.assertEqual(report.status, "interrupted")

        self.write_mock("fail.json", {})
        resumed = run.resume_rollback("lab1", plan["plan_id"], key, out=io.BytesIO())
        self.assertEqual(resumed.exit_code, 0)
        self.assertEqual(resumed.summary["results"][0]["outcome"], "failed_rolled_back")
        self.assertNotIn("testtool", self.read_mock("packages.json"))

    def test_report_after_a_finished_run(self):
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        self.apply(plan, plan_bytes)
        shutil.rmtree(run.runs_dir() / plan["plan_id"])
        report = run.report("lab1", plan["plan_id"], plan["target"]["host_key_sha256"], out=io.BytesIO())
        self.assertEqual(report.status, "finished")
        self.assertEqual(self.outcomes(report), {"testtool": "installed"})
        self.assertTrue((report.directory / "journal.jsonl").exists())


class HookTests(Lab):

    def test_unregistered_hook_fails_verification_and_rolls_back(self):
        self.write_definition(definition("testtool", hook="rollback_component"))
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        outcome, terminal = self.apply(plan, plan_bytes)
        self.assertEqual(self.outcomes(outcome), {"testtool": "failed_rolled_back"})
        self.assertIn("Verification hook not available: rollback_component", terminal)

    def test_docker_hook_checks_the_daemon_read_only(self):
        hook = ROOT / "backend/linux/engine/hooks/docker.sh"
        for body, expected in (("echo 27.3.1", 0), ("echo 'Cannot connect' >&2; exit 1", 1)):
            self.script(self.state / "bin/docker", body + "\n")
            result = subprocess.run(
                ["bash", "-c", f'source {hook}; [[ "$DSSA_HOOKS" == *" verify_docker_daemon "* ]] || exit 7; verify_docker_daemon'],
                env=dict(os.environ), capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(result.stdout, "", "hooks never write to stdout")


class MarkerFilterTests(unittest.TestCase):

    def run_filter(self, chunks):
        out = io.BytesIO()
        f = run.MarkerFilter(out)
        for chunk in chunks:
            f.feed(chunk)
        f.close()
        return out.getvalue(), f

    def test_prompt_without_newline_is_shown_at_once(self):
        out = io.BytesIO()
        f = run.MarkerFilter(out)
        f.feed(b"[sudo] password for dssa-test: ")
        self.assertEqual(out.getvalue(), b"[sudo] password for dssa-test: ")

    def test_markers_are_hidden_and_collected(self):
        text, f = self.run_filter([b"Hello\r\nDSSA_FI", b"LE summary.json eyJhIjoxfQ==\r\n",
                                   b"DSSA_STATUS finished\r\nDSSA_EXIT 3\r\nbye\n"])
        self.assertEqual(text, b"Hello\r\nbye\n")
        self.assertEqual(f.files, {"summary.json": b'{"a":1}'})
        self.assertEqual((f.status, f.exit_code), ("finished", 3))

    def test_unknown_or_unlisted_markers_are_shown(self):
        text, f = self.run_filter([b"DSSA_OTHER x\nDSSA_FILE /etc/passwd eA==\nDSX\n"])
        self.assertEqual(text, b"DSSA_OTHER x\nDSSA_FILE /etc/passwd eA==\nDSX\n")
        self.assertEqual(f.files, {})


class MainTests(Lab):

    def setUp(self):
        super().setUp()
        import main
        self.main = main
        main.DEFINITIONS_DIR = str(self.defs)
        self.addCleanup(setattr, main, "DEFINITIONS_DIR", None)

    def test_draft_needs_its_name_typed(self):
        main = self.main
        with mock.patch("builtins.input", return_value="wrong"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertIsNone(main.step_plan("lab1", ["drafttool"], True, None))
        self.assertIn("discarded", stdout.getvalue())
        self.assertFalse(run.plans_dir().exists() and any(run.plans_dir().iterdir()))

        with mock.patch("builtins.input", return_value="drafttool"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            plan_id = main.step_plan("lab1", ["drafttool"], True, None)
        self.assertTrue((run.plans_dir() / f"{plan_id}.json").exists())

    def test_stale_verification_only_plan_shows_the_real_reason(self):
        self.write_mock("packages.json", {"base-files": "1.0", "testtool": "1.0"})
        bin_path = self.state / "bin/testtool"
        bin_path.write_text("#!/bin/sh\necho mock\n")
        bin_path.chmod(0o755)
        plan_bytes, plan, path = self.plan("testtool")
        stale = json.loads(plan_bytes)
        stale["created_at"] = "2000-01-01T00:00:00Z"
        path.write_bytes(json.dumps(stale).encode())
        with mock.patch.object(run, "load_plan",
                               return_value=(json.dumps(stale).encode(), stale, path)), \
             mock.patch.object(run, "apply_plan") as apply_plan, \
             mock.patch.object(self.main, "target_host", return_value={"host": HOST}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = self.main.step_apply(plan["plan_id"], None)
        self.assertEqual(code, 2)
        apply_plan.assert_not_called()
        self.assertIn("older than 30 minutes", stdout.getvalue())
        self.assertNotIn("No approval exists", stdout.getvalue())

    def test_guided_session_end_to_end(self):
        # targets: 1 = lab1; components sorted: drafttool othertool testapp testtool
        answers = iter(["1", "2", "APPROVE", "y"])
        real_apply = run.apply_plan
        with mock.patch("builtins.input", lambda _prompt="": next(answers)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             mock.patch.object(run, "apply_plan",
                               lambda *a, **k: real_apply(*a, **k, out=io.BytesIO())):
            code = self.main.main([])
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertIn("othertool    installed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
