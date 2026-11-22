"""Unified plan -> approve -> apply against a mocked server (4.34).

The real dssa-run.sh, engine and Python bridge run; only ssh, sudo, ss
and the package/systemd tools are mocked. The mock ssh runs the "remote"
side locally and enforces host-key pinning like OpenSSH.
"""

import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = ROOT / "backend/linux/bin/dssa-run.sh"
MOCK_SYSTEM = ROOT / "tests/backend/fixtures/mock_system.py"
MOCK_SSH = ROOT / "tests/backend/fixtures/mock_ssh.py"
TOOLS = ("apt-get", "apt-cache", "dnf", "dpkg-query", "dpkg", "rpm", "systemctl", "journalctl", "curl", "gpg")
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
        # 4.38: refused at the upload, so nothing ran: 5, not 255.
        self.assertEqual(result.returncode, 5)
        self.assertIn("nothing was run there", result.stderr)
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


class SshKeyTests(Lab):
    """Which key DSSA logs in with, and a clear error when it is refused (4.38)."""

    def set_key(self, value):
        data = json.loads(self.inventory.read_text())
        data["servers"][0]["ssh_key"] = value
        self.inventory.write_text(json.dumps(data))
        self.inventory.chmod(0o600)

    def keys_used(self):
        return {c["options"].get("-i") for c in self.ssh_calls()}

    def test_default_key_when_nothing_is_set(self):
        self.plan("testtool")
        self.assertEqual(self.keys_used(), {str(self.laptop / ".ssh/id_ed25519")})

    def test_refused_key_is_named_in_the_error(self):
        os.environ["MOCK_SSH_KEY"] = str(self.laptop / ".ssh/lab_key")
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"SSH key {self.laptop}/.ssh/id_ed25519 (default) was refused by lab1 "
                      f"(tester@{HOST})", result.stderr)
        self.assertIn('Set "ssh_key" for this target in the inventory, or DSSA_SSH_KEY.', result.stderr)

    def test_inventory_key_is_used_and_tilde_expanded(self):
        key = self.laptop / ".ssh/lab_key"
        key.write_text("dummy\n")
        os.environ["MOCK_SSH_KEY"] = str(key)
        self.set_key("~/.ssh/lab_key")
        _, plan, path = self.plan("testtool")
        self.approve(path.read_bytes())
        outcome, terminal = self.apply(plan, path.read_bytes())
        self.assertEqual(outcome.exit_code, 0, terminal)
        self.assertEqual(self.keys_used(), {str(key)})

    def test_environment_overrides_the_inventory(self):
        env_key = self.base / "env_key"
        env_key.write_text("dummy\n")
        self.set_key("~/.ssh/id_ed25519")
        os.environ["DSSA_SSH_KEY"] = str(env_key)
        os.environ["MOCK_SSH_KEY"] = str(self.laptop / ".ssh/id_ed25519")
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"SSH key {env_key} (from DSSA_SSH_KEY) was refused", result.stderr)

    def test_unsafe_inventory_key_paths_are_refused(self):
        for value in ("relative/key", "~/../etc/key", "/tmp/a key", "~/.ssh/k;rm"):
            self.set_key(value)
            result = self.dssa("plan", "lab1", "testtool")
            self.assertEqual(result.returncode, 3, value)
            self.assertIn('Invalid ssh_key for "lab1"', result.stderr, value)
        self.assertEqual(self.ssh_calls(), [])

    def test_missing_key_file_is_named(self):
        self.set_key("~/.ssh/nope")
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"SSH key {self.laptop}/.ssh/nope (from the inventory) is missing", result.stderr)


class LoginCheckTests(Lab):
    """apply logs in before it uses the approval; a failed upload is exit 5 (4.38)."""

    def setUp(self):
        super().setUp()
        import main
        self.main = main
        main.DEFINITIONS_DIR = str(self.defs)
        self.addCleanup(setattr, main, "DEFINITIONS_DIR", None)

    def consumed(self, plan_id):
        return (Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals"
                / plan_id / "consumed").exists()

    def test_check_command(self):
        plan_bytes, plan, _ = self.plan("testtool")
        ok = self.dssa("check", "lab1", plan["target"]["host_key_sha256"])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("Login to lab1 (tester@192.0.2.10) works with", ok.stderr)
        self.assertFalse(any(c["tty"] for c in self.ssh_calls()), "no sudo, no terminal")
        os.environ["MOCK_SSH_KEY"] = "/nowhere"
        refused = self.dssa("check", "lab1", plan["target"]["host_key_sha256"])
        self.assertEqual(refused.returncode, 3)
        self.assertIn("was refused by lab1", refused.stderr)
        bad = self.dssa("check", "lab1", "SHA256:short")
        self.assertEqual(bad.returncode, 2)

    def test_refused_login_keeps_the_approval(self):
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        os.environ["MOCK_SSH_KEY"] = "/nowhere"
        with mock.patch.object(self.main, "target_host", return_value={"host": HOST}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = self.main.step_apply(plan["plan_id"], None)
        self.assertEqual(code, 2)
        self.assertIn("cannot log in to the server. The approval was not used.", stdout.getvalue())
        self.assertFalse(self.consumed(plan["plan_id"]))

        # Once the key works, the same approval applies.
        del os.environ["MOCK_SSH_KEY"]
        real_apply = run.apply_plan
        with mock.patch.object(self.main, "target_host", return_value={"host": HOST}), \
             mock.patch.object(run, "apply_plan", lambda *a, **k: real_apply(*a, **k, out=io.BytesIO())), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = self.main.step_apply(plan["plan_id"], None)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertTrue(self.consumed(plan["plan_id"]))

    def test_failed_upload_is_exit_5_and_says_nothing_ran(self):
        plan_bytes, plan, _ = self.plan("testtool")
        before = self.read_mock("packages.json")
        self.approve(plan_bytes)
        os.environ["MOCK_SSH_FAIL_STAGE"] = "1"
        real_apply = run.apply_plan
        with mock.patch.object(self.main, "target_host", return_value={"host": HOST}), \
             mock.patch.object(run, "apply_plan", lambda *a, **k: real_apply(*a, **k, out=io.BytesIO())), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = self.main.step_apply(plan["plan_id"], None)
        self.assertEqual(code, 5)
        text = stdout.getvalue()
        self.assertIn("Nothing was run on the server. The approval is used up", text)
        self.assertIn("Exit code : 5 (the plan could not be sent to the server; nothing was run there)", text)
        self.assertNotIn("may still be going", text)
        self.assertFalse(any(c["tty"] for c in self.ssh_calls()), "sudo was never reached")
        self.assertEqual(self.read_mock("packages.json"), before)
        audit = (self.base / "audit/security.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(audit[-1])["status"], "not_run")

    def test_refusal_on_the_server_says_nothing_ran(self):
        # 4.39.1: exit 2 without a result is a refusal, not a dropped run.
        plan_bytes, plan, _ = self.plan("testtool")
        self.approve(plan_bytes)
        os.environ["MOCK_REMOTE_HIDE"] = "jq"
        real_apply = run.apply_plan
        with mock.patch.object(self.main, "target_host", return_value={"host": HOST}), \
             mock.patch.object(run, "apply_plan", lambda *a, **k: real_apply(*a, **k, out=io.BytesIO())), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = self.main.step_apply(plan["plan_id"], None)
        self.assertEqual(code, 2)
        text = stdout.getvalue()
        self.assertIn("The server refused the run before any change; the reason is shown above.", text)
        self.assertIn("Nothing was run there. Fix the cause. The approval is used up: plan and approve again.", text)
        self.assertNotIn("may still be going", text)
        self.assertIn("Exit code : 2 (refused before any change)", text)

    def test_verification_only_wording_has_no_approval(self):
        from types import SimpleNamespace
        for code, first in ((2, "The server refused the run"), (5, "Nothing was run on the server.")):
            outcome = SimpleNamespace(summary=None, exit_code=code, rc=code, status=None, directory=None)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.main.show_outcome(outcome)
            text = stdout.getvalue()
            self.assertIn(first, text)
            self.assertIn("Plan again, then apply.", text)
            self.assertNotIn("approval", text)
            self.assertNotIn("may still be going", text)


class FakeTty:
    """Stands in for /dev/tty: records the prompt, answers one line."""

    def __init__(self, answer):
        # One answer, or a list answered in order (one per prompt).
        self.answers = list(answer) if isinstance(answer, (list, tuple)) else None
        self.answer, self.written = answer, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, text):
        self.written += text

    def flush(self):
        pass

    def readline(self):
        if self.answers is not None:
            return self.answers.pop(0) + "\n"
        return self.answer + "\n"


class JsonLab(Lab):
    """Helpers to run main.py --json in-process (4.40)."""

    def setUp(self):
        super().setUp()
        import main
        self.main = main
        main.DEFINITIONS_DIR = str(self.defs)
        self.addCleanup(setattr, main, "DEFINITIONS_DIR", None)
        self.target = mock.patch.object(main, "target_host", return_value={"host": HOST})
        self.target.start()
        self.addCleanup(self.target.stop)

    def run_json(self, *argv, tty=None):
        stdout = io.StringIO()
        stderr = io.TextIOWrapper(io.BytesIO(), write_through=True)
        opener = (lambda: tty) if tty is not None else mock.Mock(side_effect=OSError("no tty"))
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr), \
             mock.patch.object(self.main, "_open_tty", opener), \
             mock.patch("builtins.input", side_effect=AssertionError("stdin must not be read")):
            code = self.main.main(["--json", *argv])
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.check_shape(lines)
        stderr.seek(0)
        return code, lines[:-1], lines[-1], stderr.buffer.getvalue().decode(errors="replace")

    def check_shape(self, lines):
        self.assertTrue(lines, "stdout must end with a result")
        for line in lines:
            self.assertEqual(line["schema_version"], "1.0")
        *events, result = lines
        for event in events:
            self.assertEqual(set(event), {"schema_version", "type", "event", "data"})
            self.assertEqual(event["type"], "event")
        self.assertEqual(set(result), {"schema_version", "type", "command", "ok", "exit_code", "data", "error"})
        self.assertEqual(result["type"], "result")
        self.assertEqual(result["ok"], result["exit_code"] == 0)



class JsonOutputTests(JsonLab):
    """main.py --json: JSON Lines on stdout, ending with one result (4.40)."""

    def test_targets(self):
        code, events, result, _ = self.run_json("targets")
        self.assertEqual((code, events), (0, []))
        self.assertEqual([t["target_id"] for t in result["data"]["targets"]], ["lab1", "prod1"])
        self.assertIsNone(result["error"])

    def test_plan_puts_text_on_stderr_and_the_plan_in_data(self):
        code, events, result, stderr = self.run_json("plan", "lab1", "testtool")
        self.assertEqual(code, 0)
        plan = result["data"]["plan"]
        self.assertEqual(plan["runnable"], ["testtool"])
        self.assertTrue(Path(result["data"]["plan_path"]).is_file())
        self.assertEqual(result["data"]["plan_sha256"], plan_digest(Path(result["data"]["plan_path"]).read_bytes()))
        self.assertEqual([e["event"] for e in events], ["plan.saved"])
        self.assertIn("=== PLAN ===", stderr)

    def test_approve_reads_the_terminal_not_stdin(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool")
        plan_id = planned["data"]["plan"]["plan_id"]
        tty = FakeTty("APPROVE")
        code, _, result, _ = self.run_json("approve", plan_id, tty=tty)
        self.assertEqual(code, 0, result)
        self.assertIn("Type APPROVE to approve", tty.written)
        self.assertEqual(result["data"]["approval"]["plan_id"], plan_id)
        self.assertFalse(result["data"]["verification_only"])

    def test_approve_without_a_terminal_is_refused(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool")
        plan_id = planned["data"]["plan"]["plan_id"]
        code, _, result, _ = self.run_json("approve", plan_id)
        self.assertEqual(code, 2)
        self.assertIn("needs a person at a terminal", result["error"]["message"])
        approvals = Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals" / plan_id
        self.assertFalse((approvals / "approval.json").exists())

    def test_wrong_confirmation_is_an_error(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool")
        code, _, result, _ = self.run_json("approve", planned["data"]["plan"]["plan_id"], tty=FakeTty("yes"))
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"]["message"])

    def test_apply_streams_events_and_returns_the_summary(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool")
        plan_id = planned["data"]["plan"]["plan_id"]
        self.run_json("approve", plan_id, tty=FakeTty("APPROVE"))
        code, events, result, stderr = self.run_json("apply", plan_id)
        self.assertEqual(code, 0, stderr)
        names = [e["event"] for e in events]
        self.assertEqual(names[:3], ["login.check", "approval.consumed", "run.started"])
        self.assertEqual(names[-1], "run.finished")
        logs = [e["data"]["line"] for e in events if e["event"] == "log"]
        self.assertTrue(any("packages_install" in line for line in logs), logs)
        self.assertFalse(any(line.startswith("DSSA_") for line in logs), "markers stay hidden")
        data = result["data"]
        self.assertEqual(data["summary"]["status"], "completed")
        self.assertTrue(data["approval_used"])
        self.assertTrue(Path(data["saved_copy"]).is_dir())
        self.assertIn("packages_install", stderr, "the operator still sees the live log")

    def test_server_refusal_is_an_error_with_the_reason(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool")
        plan_id = planned["data"]["plan"]["plan_id"]
        self.run_json("approve", plan_id, tty=FakeTty("APPROVE"))
        os.environ["MOCK_REMOTE_HIDE"] = "jq"
        code, _, result, _ = self.run_json("apply", plan_id)
        self.assertEqual(code, 2)
        self.assertIsNone(result["data"]["summary"])
        self.assertIn("refused before any change", result["error"]["message"])

    def test_errors_still_end_with_a_result(self):
        code, events, result, stderr = self.run_json("plan", "nope", "testtool")
        self.assertEqual(code, 2)
        self.assertEqual(result["command"], "plan")
        self.assertIn("Planning failed", result["error"]["message"])
        code, _, result, _ = self.run_json("report", "not-a-plan-id")
        self.assertEqual(code, 2)
        self.assertIn("Invalid plan ID", result["error"]["message"])

    def test_installed_shows_the_record_after_apply(self):
        code, _, result, _ = self.run_json("installed", "lab1")
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["record"]["components"], {})
        _, _, planned, _ = self.run_json("plan", "lab1", "testapp")
        plan_id = planned["data"]["plan"]["plan_id"]
        self.run_json("approve", plan_id, tty=FakeTty("APPROVE"))
        code, _, applied, stderr = self.run_json("apply", plan_id)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(all(r["recorded"] for r in applied["data"]["summary"]["results"]))
        code, _, result, stderr = self.run_json("installed", "lab1")
        components = result["data"]["record"]["components"]
        self.assertEqual(sorted(components), ["testapp", "testtool"])
        self.assertEqual(components["testapp"]["plan_id"], plan_id)
        self.assertIn("INSTALLED BY DSSA on lab1", stderr)
        self.assertFalse(any(c["tty"] for c in self.ssh_calls() if c["command"] == "bash -s"),
                         "reading the record needs no terminal")

    def test_guided_session_is_refused(self):
        code, events, result, _ = self.run_json()
        self.assertEqual((code, events, result["command"]), (2, [], "guided"))
        self.assertIn("needs a command", result["error"]["message"])


class FlowLab(JsonLab):
    """Install, plan an operation, approve and apply, all via --json."""

    def install(self, *components):
        _, _, planned, _ = self.run_json("plan", "lab1", *components)
        plan_id = planned["data"]["plan"]["plan_id"]
        self.run_json("approve", plan_id, tty=FakeTty("APPROVE"))
        code, _, result, stderr = self.run_json("apply", plan_id)
        self.assertEqual(code, 0, stderr)
        return result

    def plan_op(self, op, *components):
        code, _, result, stderr = self.run_json("plan", f"--{op}", "lab1", *components)
        self.assertEqual(code, 0, stderr)
        return result["data"]["plan"]

    def approve_and_apply(self, plan, answers):
        code, _, approved, _ = self.run_json("approve", plan["plan_id"], tty=FakeTty(answers))
        self.assertEqual(code, 0, approved)
        return self.run_json("apply", plan["plan_id"])

    def record(self):
        return self.run_json("installed", "lab1")[2]["data"]["record"]["components"]

    def steps(self, plan):
        return {s["component"]: s for s in plan["steps"]}



class RemoveAdoptTests(FlowLab):
    """plan --remove / --adopt through the whole flow (4.42)."""

    def test_remove_what_dssa_installed(self):
        self.write_mock("deps.json", {"testtool": ["libtt"]})
        self.install("testtool")
        self.assertIn("libtt", self.read_mock("packages.json"))
        plan = self.plan_op("remove", "testtool")
        self.assertEqual(plan["operation"], "remove")
        step = self.steps(plan)["testtool"]
        self.assertEqual(step["action"], "REMOVE")
        self.assertEqual([p["package"] for p in step["details"]["remove_packages"]], ["testtool", "libtt"])
        self.assertTrue(step["details"]["reversible"])
        code, events, result, stderr = self.approve_and_apply(plan, ["testtool", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["data"]["summary"]["results"][0]["outcome"], "removed")
        packages = self.read_mock("packages.json")
        self.assertNotIn("testtool", packages)
        self.assertNotIn("libtt", packages)
        self.assertEqual(self.record(), {})
        kept_dir = self.root / "var/lib/dssa/transactions" / plan["plan_id"] / "kept/testtool"
        kept = list(kept_dir.iterdir())
        self.assertEqual(len(kept), 2, "the removed versions' package files are kept")
        self.assertTrue(all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in kept))
        # apt downloads into its own temporary folder (its _apt sandbox can
        # write there), never straight into the root-only kept folder.
        dirs = (self.state / "download_dirs.log").read_text().split()
        self.assertTrue(dirs and all(Path(d) != kept_dir for d in dirs), dirs)
        self.assertFalse(any(Path(d).exists() for d in dirs), "temporary folders are removed")

    def test_remove_needs_approval_and_the_typed_name(self):
        self.install("testtool")
        plan = self.plan_op("remove", "testtool")
        code, _, result, _ = self.run_json("approve", plan["plan_id"], tty=FakeTty(["wrong", "APPROVE"]))
        self.assertEqual(code, 2)
        self.assertIn("not confirmed", result["error"]["message"])
        self.assertFalse(result["data"]["verification_only"])
        code, _, result, _ = self.run_json("apply", plan["plan_id"])
        self.assertEqual(code, 2, "no approval exists")
        self.assertIn("testtool", self.read_mock("packages.json"))

    def test_not_installed_by_dssa_is_blocked(self):
        self.write_mock("packages.json", {"base-files": "1.0", "testtool": "1.0"})
        plan = self.plan_op("remove", "testtool")
        step = self.steps(plan)["testtool"]
        self.assertEqual(step["action"], "BLOCKED")
        self.assertIn("Not installed by DSSA", step["reasons"][0])
        self.assertEqual(plan["runnable"], [])

    def test_dependency_needed_by_a_recorded_component_is_blocked(self):
        self.install("testapp")  # pulls in testtool
        plan = self.plan_op("remove", "testtool")
        self.assertEqual(self.steps(plan)["testtool"]["action"], "BLOCKED")
        self.assertIn("Still needed by testapp", self.steps(plan)["testtool"]["reasons"][0])
        both = self.plan_op("remove", "testtool", "testapp")
        self.assertEqual([s["component"] for s in both["steps"]], ["testapp", "testtool"], "dependents first")
        code, _, result, stderr = self.approve_and_apply(both, ["testapp", "testtool", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.record(), {})

    def test_packages_other_software_needs_are_kept_or_blocked(self):
        self.write_mock("deps.json", {"testtool": ["libtt"]})
        self.install("testtool")
        # Something DSSA did not install needs libtt: keep it, remove the rest.
        packages = self.read_mock("packages.json")
        self.write_mock("packages.json", {**packages, "otherapp": "1.0"})
        self.write_mock("rdeps.json", {"libtt": ["otherapp"]})
        step = self.steps(self.plan_op("remove", "testtool"))["testtool"]
        self.assertEqual([p["package"] for p in step["details"]["remove_packages"]], ["testtool"])
        self.assertEqual([p["package"] for p in step["details"]["kept_dependencies"]], ["libtt"])
        # If the main package itself is needed, nothing is removed.
        self.write_mock("rdeps.json", {"testtool": ["otherapp"]})
        step = self.steps(self.plan_op("remove", "testtool"))["testtool"]
        self.assertEqual(step["action"], "BLOCKED")
        self.assertIn("otherapp", step["reasons"][0])

    def test_failed_removal_is_undone_from_the_kept_files(self):
        self.install("testtool")
        before = self.read_mock("packages.json")
        plan = self.plan_op("remove", "testtool")
        self.write_mock("fail.json", {"purge_extra": ["base-files"]})
        code, _, result, stderr = self.approve_and_apply(plan, ["testtool", "APPROVE"])
        self.assertEqual(code, 3, stderr)
        outcome = result["data"]["summary"]["results"][0]
        self.assertEqual(outcome["outcome"], "failed_rolled_back")
        self.assertIn("also removed: base-files", outcome["failure"])
        packages = self.read_mock("packages.json")
        self.assertEqual(packages["testtool"], before["testtool"], "reinstalled from the kept file")
        self.assertIn("testtool", self.record(), "the record entry is back")

    def test_unavailable_version_is_marked_not_reversible(self):
        self.install("testtool")
        self.write_mock("fail.json", {"unavailable": ["testtool"]})
        plan = self.plan_op("remove", "testtool")
        step = self.steps(plan)["testtool"]
        self.assertFalse(step["details"]["reversible"])
        self.assertTrue(any("NOT reversible" in r for r in step["reasons"]))
        code, _, _, stderr = self.run_json("approve", plan["plan_id"], tty=FakeTty(["testtool", "APPROVE"]))
        self.assertEqual(code, 0)
        self.assertIn("This removal is NOT reversible (no package file for testtool 1.0)", stderr)

    def test_download_failure_stops_a_reversible_removal(self):
        self.install("testtool")
        plan = self.plan_op("remove", "testtool")
        self.write_mock("fail.json", {"download": ["testtool"]})
        code, _, result, stderr = self.approve_and_apply(plan, ["testtool", "APPROVE"])
        self.assertEqual(code, 3, stderr)
        self.assertIn("nothing was removed", result["data"]["summary"]["results"][0]["failure"])
        self.assertIn("testtool", self.read_mock("packages.json"))

    def test_adopt_then_remove(self):
        self.write_mock("packages.json", {"base-files": "1.0", "testtool": "1.0"})
        binary = self.state / "bin/testtool"
        binary.write_text("#!/bin/sh\necho mock-version\n")
        binary.chmod(0o755)
        plan = self.plan_op("adopt", "testtool")
        self.assertEqual(self.steps(plan)["testtool"]["action"], "ADOPT")
        code, _, result, stderr = self.approve_and_apply(plan, ["testtool", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["data"]["summary"]["results"][0]["outcome"], "adopted")
        entry = self.record()["testtool"]
        self.assertEqual(entry["how"], "adopted")
        self.assertEqual([p["package"] for p in entry["packages"]], ["testtool"])
        self.assertEqual(self.steps(self.plan_op("adopt", "testtool"))["testtool"]["action"], "BLOCKED",
                         "already managed")
        removal = self.plan_op("remove", "testtool")
        self.assertEqual(self.steps(removal)["testtool"]["action"], "REMOVE")

    def test_adopt_needs_an_existing_install(self):
        plan = self.plan_op("adopt", "testtool")
        self.assertEqual(self.steps(plan)["testtool"]["action"], "BLOCKED")
        self.assertIn("nothing to adopt", self.steps(plan)["testtool"]["reasons"][0])

    def test_drift_between_approval_and_apply_skips_the_removal(self):
        self.install("testtool")
        plan = self.plan_op("remove", "testtool")
        self.run_json("approve", plan["plan_id"], tty=FakeTty(["testtool", "APPROVE"]))
        packages = self.read_mock("packages.json")
        packages["otherapp"] = "1.0"
        self.write_mock("packages.json", packages)
        self.write_mock("rdeps.json", {"testtool": ["otherapp"]})
        code, _, result, stderr = self.run_json("apply", plan["plan_id"])
        self.assertEqual(code, 3, stderr)
        self.assertEqual(result["data"]["summary"]["results"][0]["outcome"], "drift_skipped")
        self.assertIn("testtool", self.read_mock("packages.json"))


class UpdateTests(FlowLab):
    """Update info in normal plans, and plan --update (4.43)."""

    def service_definition(self):
        d = definition("svcapp")
        variant = d["variants"][0]
        variant["steps"] += [{"type": "service_enable", "unit": "svcapp.service"},
                             {"type": "service_start", "unit": "svcapp.service"}]
        variant["verification"].append({"type": "service_active", "unit": "svcapp.service"})
        self.write_definition(d)
        self.write_mock("pkg_units.json", {"svcapp": ["svcapp.service"]})
        self.write_mock("pkg_bins.json", {"testtool": ["testtool"], "svcapp": ["svcapp"]})

    def test_normal_plan_reports_an_update_as_info_only(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "1.1"})
        _, _, result, _ = self.run_json("plan", "lab1", "testtool")
        step = result["data"]["plan"]["steps"][0]
        self.assertEqual(step["action"], "SKIP")
        self.assertIn("Update available: testtool 1.0 -> 1.1 (plan --update to apply it).", step["reasons"])

    def test_update_to_the_exact_planned_version(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "1.1"})
        plan = self.plan_op("update", "testtool")
        step = self.steps(plan)["testtool"]
        self.assertEqual(step["action"], "UPDATE")
        self.assertEqual(step["details"]["updates"], [{"package": "testtool", "from": "1.0", "to": "1.1"}])
        self.assertEqual(step["details"]["running_units"], [])
        code, _, result, stderr = self.approve_and_apply(plan, ["APPROVE"])  # no name needed
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["data"]["summary"]["results"][0]["outcome"], "updated")
        self.assertEqual(self.read_mock("packages.json")["testtool"], "1.1")
        self.assertEqual(self.record()["testtool"]["packages"][0]["version"], "1.1", "the record follows")
        calls = (self.state / "calls.log").read_text()
        self.assertIn("--only-upgrade", calls)
        self.assertIn("--force-confold", calls)

    def test_up_to_date_is_skip(self):
        self.install("testtool")
        plan = self.plan_op("update", "testtool")
        self.assertEqual(self.steps(plan)["testtool"]["action"], "SKIP")
        self.assertIn("Up to date", " ".join(self.steps(plan)["testtool"]["reasons"]))

    def test_major_version_change_is_blocked(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "2.0"})
        step = self.steps(self.plan_op("update", "testtool"))["testtool"]
        self.assertEqual(step["action"], "BLOCKED")
        self.assertIn("Major version change", step["reasons"][0])

    def test_update_that_adds_packages_is_blocked(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "1.1"})
        self.write_mock("fail.json", {"update_adds": ["newlib"]})
        step = self.steps(self.plan_op("update", "testtool"))["testtool"]
        self.assertEqual(step["action"], "BLOCKED")
        self.assertIn("newlib (new)", step["reasons"][0])

    def test_not_installed_is_blocked(self):
        step = self.steps(self.plan_op("update", "testtool"))["testtool"]
        self.assertEqual(step["action"], "BLOCKED")

    def test_failed_update_goes_back_to_the_kept_version(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "1.1"})
        plan = self.plan_op("update", "testtool")
        self.run_json("approve", plan["plan_id"], tty=FakeTty(["APPROVE"]))
        self.write_mock("fail.json", {"real_update_adds": ["newlib"]})  # only the real upgrade
        code, _, result, stderr = self.run_json("apply", plan["plan_id"])
        self.assertEqual(code, 3, stderr)
        outcome = result["data"]["summary"]["results"][0]
        self.assertEqual(outcome["outcome"], "failed_rolled_back")
        self.assertIn("added or removed packages: newlib", outcome["failure"])
        self.assertEqual(self.read_mock("packages.json")["testtool"], "1.0", "downgraded from the kept file")

    def test_candidate_moved_after_planning_changes_nothing(self):
        self.install("testtool")
        self.write_mock("candidates.json", {"testtool": "1.1"})
        plan = self.plan_op("update", "testtool")
        self.run_json("approve", plan["plan_id"], tty=FakeTty(["APPROVE"]))
        self.write_mock("candidates.json", {"testtool": "1.2"})
        code, _, result, stderr = self.run_json("apply", plan["plan_id"])
        self.assertEqual(code, 3, stderr)
        self.assertIn("available version changed", result["data"]["summary"]["results"][0]["failure"])
        self.assertEqual(self.read_mock("packages.json")["testtool"], "1.0")

    def test_restarting_a_running_service_needs_the_typed_name(self):
        self.service_definition()
        self.install("svcapp")
        self.write_mock("candidates.json", {"svcapp": "1.1"})
        plan = self.plan_op("update", "svcapp")
        step = self.steps(plan)["svcapp"]
        self.assertEqual(step["details"]["running_units"], ["svcapp.service"])
        code, _, result, _ = self.run_json("approve", plan["plan_id"], tty=FakeTty(["APPROVE", "APPROVE"]))
        self.assertEqual(code, 2, "APPROVE is not the component's name")
        code, _, result, stderr = self.approve_and_apply(plan, ["svcapp", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.read_mock("units.json")["svcapp.service"]["active"], "active", "running again")


class AllOrNothingTests(FlowLab):
    """--all-or-nothing: any failure undoes the whole run (4.44)."""

    def plan_atomic(self, *argv):
        code, _, result, stderr = self.run_json("plan", "--all-or-nothing", "lab1", *argv)
        self.assertEqual(code, 0, stderr)
        return result["data"]["plan"]

    def test_failure_undoes_the_components_already_installed(self):
        plan = self.plan_atomic("testtool", "othertool")
        self.assertTrue(plan["all_or_nothing"])
        self.write_mock("fail.json", {"install": ["othertool"]})
        code, _, result, stderr = self.approve_and_apply(plan, ["APPROVE"])
        self.assertEqual(code, 3, stderr)
        outcomes = {r["component"]: r["outcome"] for r in result["data"]["summary"]["results"]}
        self.assertEqual(outcomes, {"testtool": "undone_all_or_nothing", "othertool": "failed_rolled_back"})
        packages = self.read_mock("packages.json")
        self.assertNotIn("testtool", packages)
        self.assertNotIn("othertool", packages)
        self.assertEqual(self.record(), {}, "the undone install is not recorded")
        self.assertIn("undoing the others", stderr)

    def test_without_the_flag_the_others_stay(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "testtool", "othertool")
        self.write_mock("fail.json", {"install": ["othertool"]})
        code, _, result, _ = self.approve_and_apply(planned["data"]["plan"], ["APPROVE"])
        self.assertEqual(code, 3)
        self.assertIn("testtool", self.read_mock("packages.json"))

    def test_success_keeps_everything(self):
        plan = self.plan_atomic("testtool", "othertool")
        code, _, result, stderr = self.approve_and_apply(plan, ["APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sorted(self.record()), ["othertool", "testtool"])

    def test_held_components_cannot_be_approved(self):
        plan = self.plan_atomic("testtool", "drafttool")
        code, _, result, _ = self.run_json("approve", plan["plan_id"], tty=FakeTty(["APPROVE"]))
        self.assertEqual(code, 2)
        self.assertIn("All-or-nothing plan has held components (drafttool)", " ".join(result["error"]["issues"]))

    def test_failed_removal_puts_the_earlier_removal_back(self):
        self.install("testtool")
        self.install("othertool")
        code, _, result, stderr = self.run_json("plan", "--remove", "--all-or-nothing", "lab1", "testtool", "othertool")
        self.assertEqual(code, 0, stderr)
        plan = result["data"]["plan"]
        self.write_mock("fail.json", {"download": ["othertool"]})
        code, _, result, stderr = self.approve_and_apply(plan, ["othertool", "testtool", "APPROVE"])  # removal runs in reverse
        self.assertEqual(code, 3, stderr)
        self.assertIn("testtool", self.read_mock("packages.json"), "reinstalled from the kept file")
        self.assertEqual(sorted(self.record()), ["othertool", "testtool"])


class ManagedFileTests(FlowLab):
    """managed_file and service_reload: DSSA creates, you own (4.45)."""

    def setUp(self):
        super().setUp()
        templates = self.base / "templates/cfgtool"
        templates.mkdir(parents=True)
        (templates / "default.conf").write_text("# written by DSSA\nlisten 8080;\n")
        self.script(self.tools / "nginx", '[ -f "$MOCK_STATE/nginx_fail" ] && { echo "nginx: [emerg] bad config" >&2; exit 1; }\necho "nginx: configuration file test is successful" >&2\n')
        d = definition("cfgtool")
        variant = d["variants"][0]
        variant["steps"] += [
            {"type": "managed_file", "path": "/etc/cfgtool/dssa-default.conf",
             "template": "cfgtool/default.conf", "mode": "0640", "owner": "root:root"},
            {"type": "service_enable", "unit": "cfgtool.service"},
            {"type": "service_start", "unit": "cfgtool.service"},
            {"type": "service_reload", "unit": "cfgtool.service", "config_test": "nginx -t"},
        ]
        self.write_definition(d)
        self.write_mock("pkg_paths.json", {"cfgtool": ["/etc/cfgtool"]})
        self.write_mock("pkg_units.json", {"cfgtool": ["cfgtool.service"]})
        self.write_mock("pkg_bins.json", {"cfgtool": ["cfgtool"], "testtool": ["testtool"]})
        self.conf = self.root / "etc/cfgtool/dssa-default.conf"

    def test_file_is_created_from_the_approved_template_and_recorded(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "cfgtool")
        plan = planned["data"]["plan"]
        self.assertEqual(list(plan["templates"]), ["cfgtool/default.conf"])
        code, _, result, stderr = self.approve_and_apply(plan, ["APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.conf.read_text(), "# written by DSSA\nlisten 8080;\n")
        self.assertEqual(stat.S_IMODE(self.conf.stat().st_mode), 0o640)
        files = self.record()["cfgtool"]["files"]
        self.assertEqual([f["path"] for f in files], ["/etc/cfgtool/dssa-default.conf"])
        self.assertIn("configuration file test is successful", stderr)

    def test_existing_file_blocks_the_install(self):
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text("mine\n")
        _, _, planned, _ = self.run_json("plan", "lab1", "cfgtool")
        step = planned["data"]["plan"]["steps"][0]
        self.assertEqual(step["action"], "BLOCKED")
        self.assertIn("never overwrites a file", step["reasons"][0])
        self.assertEqual(self.conf.read_text(), "mine\n")

    def test_template_changed_after_planning_is_refused(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "cfgtool")
        (self.base / "templates/cfgtool/default.conf").write_text("listen 9999;\n")
        code, _, result, _ = self.run_json("approve", planned["data"]["plan"]["plan_id"], tty=FakeTty(["APPROVE"]))
        self.assertEqual(code, 2)
        self.assertIn("template cfgtool/default.conf", " ".join(result["error"]["issues"] + [result["error"]["message"]]))

    def test_failed_config_test_removes_the_new_file(self):
        _, _, planned, _ = self.run_json("plan", "lab1", "cfgtool")
        (self.state / "nginx_fail").write_text("")
        code, _, result, stderr = self.approve_and_apply(planned["data"]["plan"], ["APPROVE"])
        self.assertEqual(code, 3, stderr)
        self.assertIn("Config test failed", stderr)
        self.assertFalse(self.conf.exists(), "rollback removed the file DSSA created")
        self.assertNotIn("cfgtool", self.read_mock("packages.json"))

    def test_uninstall_removes_the_file_only_if_unchanged(self):
        self.install("cfgtool")
        step = self.steps(self.plan_op("remove", "cfgtool"))["cfgtool"]
        self.assertEqual(step["details"]["remove_files"], ["/etc/cfgtool/dssa-default.conf"])
        self.conf.write_text("edited by the admin\n")
        plan = self.plan_op("remove", "cfgtool")
        step = self.steps(plan)["cfgtool"]
        self.assertEqual(step["details"]["remove_files"], [])
        self.assertEqual(step["details"]["kept_files"], ["/etc/cfgtool/dssa-default.conf"])
        code, _, _, stderr = self.approve_and_apply(plan, ["cfgtool", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.conf.read_text(), "edited by the admin\n", "an edited file is the admin's")

    def test_unchanged_file_is_removed_with_the_component(self):
        self.install("cfgtool")
        plan = self.plan_op("remove", "cfgtool")
        code, _, _, stderr = self.approve_and_apply(plan, ["cfgtool", "APPROVE"])
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.conf.exists())


class WebFlowTests(Lab):
    """The web UI's App drives the same flow (4.46): plan, approve, apply as a terminal job."""

    def setUp(self):
        super().setUp()
        from bridge.webui.app import App, Config
        home = self.base / "webui"
        home.mkdir(mode=0o700)
        shutil.copy(self.inventory, home / "inventory.json")
        (home / "inventory.json").chmod(0o600)
        self.app = App(Config(home, definitions_dir=str(self.defs)))
        self.target = mock.patch.object(self.app, "host_of", return_value=HOST)
        self.target.start()
        self.addCleanup(self.target.stop)

    def wait(self, job):
        deadline = time.time() + 60
        while job.view()["running"] and time.time() < deadline:
            time.sleep(0.05)
        return job.view()

    def test_plan_approve_apply(self):
        (plan_id,) = self.app.make_plans("alice", ["lab1"], ["testtool"], "install", False)
        record = self.app.approve("alice", plan_id, {"_confirm": "APPROVE"})
        self.assertEqual(record["approved_by"], "alice")
        view = self.wait(self.app.start_apply("alice", plan_id))
        self.assertEqual(view["exit_code"], 0, view["text"])
        self.assertIn("packages_install", view["text"])
        self.assertNotIn("DSSA_FILE", view["text"])
        self.assertEqual(view["summary"]["status"], "completed")
        self.assertIn("testtool", self.read_mock("packages.json"))
        self.assertIn('"action": "apply"', (self.base / "webui/audit.jsonl").read_text())

    def test_plan_page_after_the_run_offers_only_the_report(self):
        from bridge.webui.app import Handler
        (plan_id,) = self.app.make_plans("alice", ["lab1"], ["testtool"], "install", False)
        self.app.approve("alice", plan_id, {"_confirm": "APPROVE"})
        self.wait(self.app.start_apply("alice", plan_id))
        sent = {}
        handler = Handler.__new__(Handler)
        handler.app = self.app
        handler.send_html = lambda body, *a, **k: sent.setdefault("body", body)
        handler.plan_page({"user": "alice", "csrf": "t"}, plan_id)
        self.assertIn("Already run: status <b>completed</b> (testtool: installed)", sent["body"])
        self.assertNotIn(f"/plans/{plan_id}/apply", sent["body"])
        self.assertIn(f"/plans/{plan_id}/report", sent["body"])

    def test_risky_actions_need_the_typed_name(self):
        (plan_id,) = self.app.make_plans("alice", ["lab1"], ["testtool"], "install", False)
        self.app.approve("alice", plan_id, {"_confirm": "APPROVE"})
        self.wait(self.app.start_apply("alice", plan_id))
        (removal,) = self.app.make_plans("alice", ["lab1"], ["testtool"], "remove", False)
        with self.assertRaisesRegex(ApprovalError, "testtool not confirmed"):
            self.app.approve("alice", removal, {"_confirm": "APPROVE"})
        self.app.approve("alice", removal, {"testtool": "testtool", "_confirm": "APPROVE"})
        view = self.wait(self.app.start_apply("alice", removal))
        self.assertEqual(view["exit_code"], 0, view["text"])
        self.assertNotIn("testtool", self.read_mock("packages.json"))

    def test_drafts_on_lab_servers_need_their_typed_name(self):
        (plan_id,) = self.app.make_plans("alice", ["lab1"], ["drafttool"], "install", False, allow_draft=True)
        with self.assertRaisesRegex(ApprovalError, "drafttool not confirmed"):
            self.app.approve("alice", plan_id, {"_confirm": "APPROVE"})
        self.app.approve("alice", plan_id, {"drafttool": "drafttool", "_confirm": "APPROVE"})
        view = self.wait(self.app.start_apply("alice", plan_id))
        self.assertEqual(view["exit_code"], 0, view["text"])
        self.assertIn("drafttool", self.read_mock("packages.json"))
        with self.assertRaises(run.RunError):
            self.app.make_plans("alice", ["prod1"], ["drafttool"], "install", False, allow_draft=True)

    def test_batch_stops_at_the_first_failed_server(self):
        plans = self.app.make_plans("alice", ["lab1", "prod1"], ["testtool"], "install", False)
        batch = self.app.new_batch("alice", plans)
        self.app.approve_batch("alice", batch, {"_confirm": "APPROVE"})
        self.write_mock("fail.json", {"install": ["testtool"]})
        self.app.apply_batch("alice", batch)
        deadline = time.time() + 60
        while not self.app.batches[batch]["state"].startswith(("stopped", "finished")) and time.time() < deadline:
            time.sleep(0.05)
        state = self.app.batches[batch]["state"]
        self.assertTrue(state.startswith("stopped at lab1 (exit 3)"), state)
        self.assertEqual(len(self.app.batches[batch]["jobs"]), 1, "prod1 was not started")


class ClockCheckTests(Lab):
    """plan compares the target's clock with the laptop's (4.39)."""

    def plan_with(self, skew, ntp="yes"):
        os.environ["MOCK_CLOCK_SKEW"] = str(skew)
        os.environ["MOCK_NTP"] = ntp
        return self.dssa("plan", "lab1", "testtool")

    def test_small_difference_is_shown_without_warning(self):
        result = self.plan_with(-2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stderr, r"Clock on lab1: [0-3] s behind this computer; NTP synchronized: yes")
        self.assertNotIn("WARNING", result.stderr)

    def test_difference_above_60_s_warns_but_plans(self):
        result = self.plan_with(150)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stderr, r"WARNING: The clock on lab1 is 1[45][0-9] s ahead of this computer")
        self.assertEqual(json.loads(result.stdout)["runnable"], ["testtool"])

    def test_difference_above_300_s_refuses_the_plan(self):
        result = self.plan_with(-400)
        self.assertEqual(result.returncode, 3)
        self.assertRegex(result.stderr, r"The clock on lab1 is (39[0-9]|40[0-9]) s behind this computer \(limit 300 s\)")
        self.assertEqual(result.stdout, "")
        self.assertFalse(any(c["command"] == "bash -s" for c in self.ssh_calls()), "no plan bundle was sent")

    def test_unsynchronized_clock_warns(self):
        result = self.plan_with(0, ntp="no")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: lab1 reports that its clock is not NTP-synchronized.", result.stderr)

    def test_unreadable_clock_is_skipped_with_a_warning(self):
        os.environ["MOCK_CLOCK_RAW"] = "date: invalid option"
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: Could not read the clock on lab1; the clock check was skipped.", result.stderr)


class RequiredCommandTests(Lab):
    """Commands the engine needs are checked before it uses them (4.39)."""

    def test_plan_names_the_missing_commands(self):
        os.environ["MOCK_REMOTE_HIDE"] = "jq flock"
        result = self.dssa("plan", "lab1", "testtool")
        self.assertEqual(result.returncode, 3)
        self.assertIn("ERROR: Missing on this server: jq. Install it, then try again. Nothing was changed.",
                      result.stderr)
        self.assertNotIn("jq: command not found", result.stderr)

    def test_apply_refuses_before_any_change(self):
        _, plan, path = self.plan("testtool")
        before = self.read_mock("packages.json")
        os.environ["MOCK_REMOTE_HIDE"] = "jq setsid"
        result = self.dssa("apply", "lab1", str(path), plan_digest(Path(path).read_bytes()))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Missing on this server: jq, setsid. Install them", result.stderr)
        self.assertIn("DSSA_EXIT 2", result.stdout)
        self.assertEqual(self.read_mock("packages.json"), before)
        self.assertFalse((self.root / "var/lib/dssa/transactions" / plan["plan_id"]).exists())
        self.assertFalse((self.root / "var/lib/dssa/runs" / plan["plan_id"]).exists())

    def test_vendor_repository_needs_curl_and_gpg(self):
        preflight = ROOT / "backend/linux/engine/preflight.sh"
        vendor = definition("vendortool")
        vendor["variants"][0]["steps"].insert(0, {"type": "apt_vendor_repository"})
        steps = [{"component": "vendortool", "action": "INSTALL", "variant_id": "apt-vendortool"},
                 {"component": "testtool", "action": "SKIP", "variant_id": "apt-testtool"}]
        bundle = [{"id": "vendortool", "content": json.dumps(vendor)},
                  {"id": "testtool", "content": json.dumps(definition("testtool"))}]
        spec = importlib.util.spec_from_file_location("mock_ssh", MOCK_SSH)
        mock_ssh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mock_ssh)
        hidden = mock_ssh.hide_commands(os.environ["PATH"], ["curl", "gpg"], str(self.remote))

        def check(steps):
            return subprocess.run(
                ["bash", "-c", 'source "$1"; dssa_require_commands apply "$2" "$3"', "_",
                 str(preflight), json.dumps({"steps": steps}), json.dumps(bundle)],
                capture_output=True, text=True, env={**os.environ, "PATH": hidden}, check=False)

        refused = check(steps)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Missing on this server: curl, gpg", refused.stderr)
        # Not needed when no INSTALL adds a vendor repository.
        self.assertEqual(check(steps[1:]).returncode, 0)


class StagingFolderTests(Lab):
    """Root refuses a symlinked state folder before it writes the bundle (4.39)."""

    def test_symlinked_state_folder_is_refused_before_staging(self):
        _, plan, path = self.plan("testtool")
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.root / "var/lib").mkdir(parents=True)
        (self.root / "var/lib/dssa").symlink_to(elsewhere)
        result = self.dssa("apply", "lab1", str(path), plan_digest(Path(path).read_bytes()))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("var/lib/dssa is a symlink; nothing was run.", result.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [], "nothing was written through the link")


if __name__ == "__main__":
    unittest.main()
