import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = ROOT / "backend/linux/bin/dispatcher.sh"
EXAMPLE = ROOT / "contracts/requests/discovery-system.example.json"


class DispatcherTests(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.env = os.environ.copy()
        self.env["DSSA_LOG_DIR"] = self.tempdir.name

        self.request = json.loads(EXAMPLE.read_text())

    def run_dispatcher(self, request, env=None):
        payload = (
            request if isinstance(request, str)
            else json.dumps(request)
        )

        return subprocess.run(
            [str(DISPATCHER)],
            input=payload,
            text=True,
            capture_output=True,
            env=env or self.env,
            timeout=10,
        )

    def audit_events(self):
        path = Path(self.tempdir.name) / "security.jsonl"
        if not path.exists():
            return []

        return [
            json.loads(line)
            for line in path.read_text().splitlines()
        ]

    def test_valid_discovery_request(self):
        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 0)

        response = json.loads(result.stdout)
        self.assertEqual(response["status"], "not_implemented")
        self.assertEqual(response["request_id"], "req-001")

        events = self.audit_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "discovery.requested")

    def test_unsupported_operation(self):
        self.request["operation"] = "system.execute"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            self.audit_events()[0]["event"],
            "request.rejected",
        )

    def test_unknown_target(self):
        self.request["target_id"] = "unknown-server"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)

    def test_malformed_json(self):
        result = self.run_dispatcher('{"invalid":')

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            self.audit_events()[0]["event"],
            "request.rejected",
        )

    def test_unsupported_schema_version(self):
        self.request["schema_version"] = "2.0"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)

    def test_unexpected_field(self):
        self.request["password"] = "SECRET_TEST_MARKER"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)

        log_text = (
            Path(self.tempdir.name) / "security.jsonl"
        ).read_text()

        self.assertNotIn("SECRET_TEST_MARKER", log_text)

    def test_audit_write_failure(self):
        # A regular file cannot serve as a log directory.
        invalid_dir = Path(self.tempdir.name) / "not-a-directory"
        invalid_dir.write_text("test")

        env = self.env.copy()
        env["DSSA_LOG_DIR"] = str(invalid_dir)

        result = self.run_dispatcher(self.request, env=env)

        self.assertEqual(result.returncode, 3)

        response = json.loads(result.stdout)
        self.assertEqual(
            response["errors"][0]["code"],
            "AUDIT_FAILURE",
        )


if __name__ == "__main__":
    unittest.main()
