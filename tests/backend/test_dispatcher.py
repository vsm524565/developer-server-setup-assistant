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


    def test_web_server_discovery_request(self):
        """Web-server discovery must pass through the dispatcher."""
        self.request["operation"] = "discovery.web_servers"
        self.request["request_id"] = "req-web-servers-test-001"

        result = self.run_dispatcher(self.request)
        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)
        self.assertEqual(response["status"], "success")
        self.assertEqual(
            response["request_id"], self.request["request_id"]
        )
        self.assertEqual(
            response["operation"], "discovery.web_servers"
        )
        self.assertEqual(response["errors"], [])

        servers = response["data"]["web_servers"]
        self.assertEqual(
            set(servers),
            {"nginx", "apache", "openlitespeed", "litespeed"},
        )

        for server in servers.values():
            self.assertIsInstance(server["available"], bool)
            self.assertIn(
                server["detection_status"],
                {"detected", "absent", "unidentified"},
            )
            if server["available"]:
                self.assertEqual(server["detection_status"], "detected")
                self.assertIsInstance(server["path"], str)
                self.assertTrue(server["path"])
            else:
                self.assertNotEqual(
                    server["detection_status"], "detected"
                )
                self.assertIsNone(server["path"])

        events = self.audit_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "discovery.requested")
        self.assertEqual(events[1]["event"], "discovery.completed")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["exit_code"], 0)

    def test_web_server_rejects_unexpected_parameters(self):
        """Discovery requests must reject installation parameters."""
        self.request["operation"] = "discovery.web_servers"
        self.request["parameters"] = {"install": "nginx"}

        result = self.run_dispatcher(self.request)
        self.assertEqual(result.returncode, 2)

        response = json.loads(result.stdout)
        self.assertEqual(
            response["errors"][0]["code"], "INVALID_REQUEST"
        )
        self.assertEqual(
            self.audit_events()[0]["event"], "request.rejected"
        )

    def test_valid_discovery_request(self):
        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["request_id"], "req-001")
        self.assertEqual(response["operation"], "discovery.system")
        self.assertEqual(response["errors"], [])

        data = response["data"]

        self.assertEqual(data["hostname"], "ubuntutest")
        self.assertEqual(data["os_id"], "ubuntu")
        self.assertEqual(data["os_version"], "24.04")
        self.assertEqual(data["architecture"], "x86_64")

        events = self.audit_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "discovery.requested")
        self.assertEqual(events[1]["event"], "discovery.completed")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["exit_code"], 0)

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
    def test_package_manager_discovery_request(self):
        self.request["operation"] = "discovery.package_manager"
        self.request["request_id"] = "req-package-test-001"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["status"], "success")
        self.assertEqual(
            response["operation"],
            "discovery.package_manager"
        )
        self.assertEqual(response["errors"], [])

        data = response["data"]

        self.assertIsInstance(data["available_package_tools"], list)
        self.assertIsInstance(data["package_manager_detected"], bool)
        self.assertEqual(
            data["package_manager_detected"],
            len(data["available_package_tools"]) > 0
        )

        events = self.audit_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "discovery.requested")
        self.assertEqual(events[1]["event"], "discovery.completed")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["exit_code"], 0)


    def test_package_manager_rejects_unexpected_parameters(self):
        self.request["operation"] = "discovery.package_manager"
        self.request["parameters"] = {
            "install": "nginx"
        }

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)

        response = json.loads(result.stdout)
        self.assertEqual(
            response["errors"][0]["code"],
            "INVALID_REQUEST"
        )

        events = self.audit_events()
        self.assertEqual(events[0]["event"], "request.rejected")

    def test_runtime_discovery_request(self):
        self.request["operation"] = "discovery.runtimes"
        self.request["request_id"] = "req-runtimes-test-001"

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["operation"], "discovery.runtimes")
        self.assertEqual(response["errors"], [])

        runtimes = response["data"]["runtimes"]

        self.assertEqual(
            set(runtimes),
            {"python3", "php", "node", "java"}
        )

        for runtime in runtimes.values():
            self.assertIsInstance(runtime["available"], bool)

            if runtime["available"]:
                self.assertIsInstance(runtime["path"], str)
                self.assertTrue(runtime["path"])
            else:
                self.assertIsNone(runtime["path"])
                self.assertIsNone(runtime["version"])

            if runtime["version"] is not None:
                self.assertIsInstance(runtime["version"], str)

        events = self.audit_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "discovery.requested")
        self.assertEqual(events[1]["event"], "discovery.completed")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["exit_code"], 0)

    def test_runtime_discovery_rejects_unexpected_parameters(self):
        self.request["operation"] = "discovery.runtimes"
        self.request["parameters"] = {
            "install": "nodejs"
        }

        result = self.run_dispatcher(self.request)

        self.assertEqual(result.returncode, 2)

        response = json.loads(result.stdout)

        self.assertEqual(
            response["errors"][0]["code"],
            "INVALID_REQUEST"
        )

        events = self.audit_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "request.rejected")

if __name__ == "__main__":
    unittest.main()
