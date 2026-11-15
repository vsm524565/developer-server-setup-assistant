"""Single-use approval records bound to exact plan bytes (Milestone 4.32)."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from bridge.components import approval as approval_module
from bridge.components.approval import (
    APPROVAL_WINDOW,
    ApprovalError,
    _parse_time,
    approve_plan,
    check_approval,
    consume_approval,
    known_host_fingerprints,
    plan_digest,
    preflight_plan,
)


_SPEC = importlib.util.spec_from_file_location(
    "dssa_plan_test_helpers",
    Path(__file__).with_name("test_multi_component_plan.py"),
)
helpers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helpers)

HOST_KEY = helpers.HOST_KEY
OTHER_KEY = "SHA256:" + "B" * 43
MINUTE = timedelta(minutes=1)


class ApprovalTestCase(unittest.TestCase):
    """Shared fixture: definitions, a plan, private state and audit dirs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)

        self.definitions = base / "definitions"
        self.definitions.mkdir()
        helpers.write_definitions(self.definitions, [
            helpers.make_definition("alpha"),
            helpers.make_definition("beta", [("alpha", True)]),
            helpers.make_definition("delta"),
        ])

        self.root = base / "state/approvals"
        self.audit_dir = base / "audit"
        patcher = mock.patch.dict(os.environ, {"DSSA_LOG_DIR": str(self.audit_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

        # alpha installed (SKIP), beta INSTALL, delta held (unmanaged executable).
        self.plan = helpers.plan(
            self.definitions, ["beta", "delta"],
            installed=("alpha",), executables=("delta",),
        )
        self.plan_bytes = (json.dumps(self.plan, indent=2) + "\n").encode()
        self.created = _parse_time(self.plan["created_at"])
        self.keys = {HOST_KEY}

    def approve(self, **overrides):
        options = dict(
            plan_bytes=self.plan_bytes, current_host_keys=self.keys,
            confirmation="APPROVE", operator="tester",
            now=self.created + MINUTE, root=self.root,
            definitions_dir=self.definitions,
        )
        options.update(overrides)
        return approve_plan(**options)

    def check(self, **overrides):
        options = dict(
            plan_id=self.plan["plan_id"], current_host_keys=self.keys,
            now=self.created + 2 * MINUTE, root=self.root,
            definitions_dir=self.definitions,
        )
        options.update(overrides)
        return check_approval(**options)

    def record_dir(self):
        return self.root / self.plan["plan_id"]

    def audit_events(self):
        log = self.audit_dir / "security.jsonl"
        if not log.exists():
            return []
        return [json.loads(line)["event"] for line in log.read_text().splitlines()]

    def rewrite(self, name, change):
        """Edit a record file while keeping its private permissions."""

        path = self.record_dir() / name
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)


class PreflightTests(ApprovalTestCase):

    def test_valid_plan_passes_and_needs_approval(self):
        result = preflight_plan(self.plan_bytes, self.keys, self.created + MINUTE,
                                self.definitions)
        self.assertTrue(result["passed"], result["issues"])
        self.assertTrue(result["requires_approval"])
        self.assertEqual(self.plan["runnable"], ["alpha", "beta"])

    def test_preflight_refusals(self):
        def changed_definition():
            helpers.write_definitions(self.definitions, [
                helpers.make_definition("alpha", support="draft"),
            ])

        cases = {
            "stale plan": dict(now=self.created + APPROVAL_WINDOW + MINUTE),
            "plan from the future": dict(now=self.created - 10 * MINUTE),
            "host key changed": dict(keys={OTHER_KEY}),
            "invalid plan bytes": dict(plan_bytes=b'{"plan": '),
            "tampered plan": dict(plan_bytes=self.plan_bytes.replace(
                b'"execution_allowed": false', b'"execution_allowed": true', 1)),
        }
        for name, case in cases.items():
            with self.subTest(case=name):
                result = preflight_plan(
                    case.get("plan_bytes", self.plan_bytes),
                    case.get("keys", self.keys),
                    case.get("now", self.created + MINUTE),
                    self.definitions,
                )
                self.assertFalse(result["passed"])
                self.assertTrue(result["issues"])

        changed_definition()
        result = preflight_plan(self.plan_bytes, self.keys, self.created + MINUTE,
                                self.definitions)
        self.assertIn("alpha", " ".join(result["issues"]))

    def test_nothing_runnable_is_refused(self):
        held = helpers.plan(self.definitions, ["delta"], executables=("delta",))
        result = preflight_plan(json.dumps(held).encode(), self.keys,
                                _parse_time(held["created_at"]) + MINUTE,
                                self.definitions)
        self.assertFalse(result["passed"])
        self.assertIn("Nothing in the plan is runnable.", result["issues"])

    def test_verification_only_plan_needs_no_approval(self):
        skip_only = helpers.plan(self.definitions, ["alpha"], installed=("alpha",))
        plan_bytes = json.dumps(skip_only).encode()
        now = _parse_time(skip_only["created_at"]) + MINUTE
        result = preflight_plan(plan_bytes, self.keys, now, self.definitions)
        self.assertTrue(result["passed"])
        self.assertTrue(result["verification_only"])
        with self.assertRaisesRegex(ApprovalError, "no approval is needed"):
            self.approve(plan_bytes=plan_bytes, now=now)
        self.assertFalse(self.root.exists() and any(self.root.iterdir()))


class ApproveTests(ApprovalTestCase):

    def test_record_is_written_privately_and_audited(self):
        record = self.approve()

        self.assertEqual(record["plan_sha256"], plan_digest(self.plan_bytes))
        self.assertEqual(record["approved_components"], ["alpha", "beta"])
        self.assertEqual(record["approved_by"], "tester")
        self.assertEqual(
            _parse_time(record["expires_at"]), self.created + APPROVAL_WINDOW,
        )
        self.assertIs(record["single_use"], True)

        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.record_dir().stat().st_mode & 0o777, 0o700)
        for name in ("plan.json", "approval.json"):
            self.assertEqual((self.record_dir() / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.record_dir() / "plan.json").read_bytes(), self.plan_bytes)
        self.assertEqual(self.audit_events(), ["approval.granted"])

    def test_confirmation_must_be_exactly_approve(self):
        for text in ("approve", "APPROVE ", "yes", ""):
            with self.subTest(text=text):
                with self.assertRaises(ApprovalError):
                    self.approve(confirmation=text)
        self.assertFalse(self.record_dir().exists())
        self.assertEqual(self.audit_events(), [])

    def test_plan_cannot_be_approved_twice(self):
        self.approve()
        with self.assertRaisesRegex(ApprovalError, "already has an approval"):
            self.approve()

    def test_failed_audit_leaves_no_usable_record(self):
        failing = Path(self.tmp.name) / "fail-audit.sh"
        failing.write_text("#!/usr/bin/env bash\nexit 1\n")
        failing.chmod(0o700)
        with mock.patch.object(approval_module, "AUDIT_SCRIPT", failing):
            with self.assertRaisesRegex(ApprovalError, "Audit"):
                self.approve()
        self.assertFalse(self.record_dir().exists())
        self.assertFalse(self.check()["ready"])


class CheckApprovalTests(ApprovalTestCase):

    def setUp(self):
        super().setUp()
        self.approve()

    def test_fresh_approval_is_ready(self):
        result = self.check()
        self.assertTrue(result["ready"], result["issues"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["plan"]["plan_id"], self.plan["plan_id"])

    def assert_blocked(self, gate, **overrides):
        result = self.check(**overrides)
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["checks"][gate], result)
        self.assertIsNone(result["plan"])
        self.assertTrue(result["issues"])

    def test_tampered_plan_byte_breaks_binding(self):
        path = self.record_dir() / "plan.json"
        path.write_bytes(path.read_bytes().replace(b'"tester"', b'"x"') + b" ")
        os.chmod(path, 0o600)
        self.assert_blocked("binding")

    def test_record_for_another_target_breaks_binding(self):
        self.rewrite("approval.json", lambda r: r.update(target_id="ubuntu22"))
        self.assert_blocked("binding")

    def test_expired_approval_is_blocked(self):
        self.assert_blocked("freshness", now=self.created + APPROVAL_WINDOW)

    def test_extended_record_expiry_does_not_extend_window(self):
        self.rewrite("approval.json", lambda r: r.update(expires_at="2099-01-01T00:00:00Z"))
        self.assert_blocked("freshness", now=self.created + APPROVAL_WINDOW + MINUTE)

    def test_scope_must_equal_runnable(self):
        self.rewrite("approval.json",
                     lambda r: r.update(approved_components=["alpha", "beta", "delta"]))
        self.assert_blocked("scope")

    def test_definition_change_after_approval_is_blocked(self):
        helpers.write_definitions(self.definitions, [
            helpers.make_definition("beta", [("alpha", True)], support="draft"),
        ])
        self.assert_blocked("definitions")

    def test_host_key_change_is_blocked(self):
        self.assert_blocked("identity", current_host_keys={OTHER_KEY})

    def test_unsafe_files_are_rejected(self):
        plan_file = self.record_dir() / "plan.json"
        os.chmod(plan_file, 0o644)
        self.assert_blocked("record")
        os.chmod(plan_file, 0o600)

        approval_file = self.record_dir() / "approval.json"
        real = approval_file.with_name("real.json")
        approval_file.rename(real)
        approval_file.symlink_to(real)
        self.assert_blocked("record")

    def test_malformed_record_is_rejected(self):
        self.rewrite("approval.json", lambda r: r.update(single_use=False))
        self.assert_blocked("record")
        self.rewrite("approval.json", lambda r: r.update(single_use=True, extra=1))
        self.assert_blocked("record")

    def test_missing_and_invalid_plan_ids(self):
        self.assertFalse(self.check(plan_id="../../etc")["ready"])
        missing = self.check(plan_id="00000000-0000-4000-8000-000000000000")
        self.assertFalse(missing["ready"])
        self.assertIn("No approval exists for this plan.", missing["issues"])


class ConsumeTests(ApprovalTestCase):

    def setUp(self):
        super().setUp()
        self.approve()

    def consume(self, **overrides):
        options = dict(
            plan_id=self.plan["plan_id"], current_host_keys=self.keys,
            now=self.created + 2 * MINUTE, root=self.root,
            definitions_dir=self.definitions,
        )
        options.update(overrides)
        return consume_approval(**options)

    def test_approval_is_consumed_exactly_once(self):
        plan = self.consume()
        self.assertEqual(plan["runnable"], ["alpha", "beta"])
        marker = self.record_dir() / "consumed"
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

        with self.assertRaisesRegex(ApprovalError, "not valid"):
            self.consume()
        self.assertFalse(self.check()["checks"]["unused"])
        self.assertEqual(self.audit_events(), ["approval.granted", "approval.consumed"])

    def test_invalid_approval_is_not_consumed(self):
        with self.assertRaises(ApprovalError):
            self.consume(current_host_keys={OTHER_KEY})
        self.assertFalse((self.record_dir() / "consumed").exists())

    def test_concurrent_claim_loses_to_existing_marker(self):
        # Simulate another apply creating the marker after our check passed.
        real_check = approval_module.check_approval

        def check_then_race(*args, **kwargs):
            result = real_check(*args, **kwargs)
            (self.record_dir() / "consumed").write_text("other\n")
            return result

        with mock.patch.object(approval_module, "check_approval", check_then_race):
            with self.assertRaisesRegex(ApprovalError, "already been used"):
                self.consume()


class KnownHostsTests(unittest.TestCase):

    def test_fingerprints_from_plain_and_hashed_known_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "host_key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test",
                 "-f", str(key)],
                check=True, stdin=subprocess.DEVNULL, timeout=30,
            )
            expected = subprocess.run(
                ["ssh-keygen", "-l", "-f", str(key) + ".pub"],
                check=True, capture_output=True, text=True, timeout=15,
            ).stdout.split()[1]

            known = Path(tmp) / "known_hosts"
            public = (Path(str(key) + ".pub")).read_text().split()
            known.write_text(f"192.168.56.200 {public[0]} {public[1]}\n")

            self.assertEqual(known_host_fingerprints("192.168.56.200", known), {expected})
            self.assertEqual(known_host_fingerprints("192.168.56.201", known), set())

            subprocess.run(["ssh-keygen", "-H", "-f", str(known)],
                           check=True, capture_output=True, timeout=15,
                           stdin=subprocess.DEVNULL)
            self.assertEqual(known_host_fingerprints("192.168.56.200", known), {expected})

    def test_missing_known_hosts_gives_no_keys(self):
        self.assertEqual(known_host_fingerprints("host", "/nonexistent/known_hosts"), set())


if __name__ == "__main__":
    unittest.main()
