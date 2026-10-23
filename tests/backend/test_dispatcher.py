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

    def swap_filesystem_request(self, target_path="/swap.img"):
        """Build an approved read-only filesystem discovery request."""
        request = self.request.copy()
        request["request_id"] = "req-swap-fs-test-001"
        request["operation"] = "discovery.swap_filesystem"
        request["parameters"] = {"target_path": target_path}
        return request

    def run_with_mock_ssh(self, request, mock_response):
        """Run an isolated dispatcher with a controlled SSH response."""
        sandbox = Path(self.tempdir.name) / "mock-dispatcher"
        sandbox.mkdir(exist_ok=True)

        mock_ssh = sandbox / "mock-ssh.sh"
        mock_ssh.write_text(
            "#!/usr/bin/env bash\n"
            "cat \"$DSSA_TEST_RESPONSE_FILE\"\n"
        )
        mock_ssh.chmod(0o700)

        response_file = sandbox / "response.json"
        response_file.write_text(
            mock_response if isinstance(mock_response, str)
            else json.dumps(mock_response)
        )

        dispatcher_copy = sandbox / "dispatcher.sh"
        original = DISPATCHER.read_text()

        original_assignment = (
            'SSH_SCRIPT="$BASE_DIR/backend/linux/connection/ssh.sh"'
        )

        if original.count(original_assignment) != 1:
            raise AssertionError("Unexpected dispatcher SSH configuration")

        modified = original.replace(
            original_assignment,
            f'SSH_SCRIPT="{mock_ssh}"',
            1,
        )

        dispatcher_copy.write_text(modified)
        dispatcher_copy.chmod(0o700)

        # The dispatcher derives BASE_DIR from its own location.
        # Preserve the repository root for audit and inventory scripts.
        base_assignment = (
            'BASE_DIR="$(cd -- "$(dirname -- '
            '"${BASH_SOURCE[0]}")/../../.." && pwd)"'
        )

        if modified.count(base_assignment) != 1:
            raise AssertionError("Unexpected dispatcher BASE_DIR configuration")

        modified = modified.replace(
            base_assignment,
            f'BASE_DIR="{ROOT}"',
            1,
        )
        dispatcher_copy.write_text(modified)

        env = self.env.copy()
        env["DSSA_TEST_RESPONSE_FILE"] = str(response_file)

        return subprocess.run(
            [str(dispatcher_copy)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

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
    def test_swap_filesystem_discovery_request(self):
        request = self.swap_filesystem_request()

        result = self.run_dispatcher(request)
        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["operation"], "discovery.swap_filesystem")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["errors"], [])

        data = response["data"]

        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["target_path"], "/swap.img")
        self.assertTrue(data["filesystem"]["mountpoint"].startswith("/"))
        self.assertGreaterEqual(data["filesystem"]["available_bytes"], 0)

        events = self.audit_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "discovery.requested")
        self.assertEqual(events[1]["event"], "discovery.completed")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["exit_code"], 0)

    def test_swap_filesystem_rejects_missing_path(self):
        request = self.swap_filesystem_request()
        request["parameters"] = {}

        result = self.run_dispatcher(request)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["errors"][0]["code"],
            "INVALID_REQUEST",
        )
        self.assertEqual(
            self.audit_events()[0]["event"],
            "request.rejected",
        )

    def test_swap_filesystem_rejects_relative_path(self):
        request = self.swap_filesystem_request("swap.img")

        result = self.run_dispatcher(request)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["errors"][0]["code"],
            "INVALID_REQUEST",
        )

    def test_swap_filesystem_rejects_unexpected_parameters(self):
        request = self.swap_filesystem_request()
        request["parameters"]["install"] = "nginx"

        result = self.run_dispatcher(request)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["errors"][0]["code"],
            "INVALID_REQUEST",
        )

    def test_swap_filesystem_rejects_noncanonical_path(self):
        request = self.swap_filesystem_request(
            "/tmp/../etc/test-swap"
        )

        result = self.run_dispatcher(request)

        self.assertEqual(result.returncode, 4)

        response = json.loads(result.stdout)
        self.assertEqual(response["status"], "error")
        self.assertEqual(
            response["errors"][0]["code"],
            "DISCOVERY_FAILED",
        )

        events = self.audit_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["status"], "error")

    def test_swap_filesystem_audit_failure(self):
        request = self.swap_filesystem_request()

        invalid_dir = Path(self.tempdir.name) / "not-a-directory"
        invalid_dir.write_text("test")

        env = self.env.copy()
        env["DSSA_LOG_DIR"] = str(invalid_dir)

        result = self.run_dispatcher(request, env=env)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(
            json.loads(result.stdout)["errors"][0]["code"],
            "AUDIT_FAILURE",
        )
    def valid_swap_filesystem_response(self):
        return {
            "schema_version": "1.0",
            "target_path": "/swap.img",
            "filesystem": {
                "mountpoint": "/",
                "source": "/dev/sda1",
                "type": "ext4",
                "available_bytes": 1000000000,
            },
        }

    def test_swap_filesystem_mock_valid_response(self):
        request = self.swap_filesystem_request()
        response = self.valid_swap_filesystem_response()

        result = self.run_with_mock_ssh(request, response)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "success")

    def test_swap_filesystem_mock_malformed_json(self):
        request = self.swap_filesystem_request()

        result = self.run_with_mock_ssh(
            request,
            '{"schema_version":',
        )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(
            json.loads(result.stdout)["errors"][0]["code"],
            "DISCOVERY_FAILED",
        )

    def test_swap_filesystem_mock_mismatched_path(self):
        request = self.swap_filesystem_request()
        response = self.valid_swap_filesystem_response()
        response["target_path"] = "/different.img"

        result = self.run_with_mock_ssh(request, response)

        self.assertEqual(result.returncode, 4)
        self.assertEqual(
            json.loads(result.stdout)["status"],
            "error",
        )

    def test_swap_filesystem_mock_invalid_capacity(self):
        request = self.swap_filesystem_request()
        response = self.valid_swap_filesystem_response()
        response["filesystem"]["available_bytes"] = -1

        result = self.run_with_mock_ssh(request, response)

        self.assertEqual(result.returncode, 4)

    def test_swap_filesystem_mock_extra_field(self):
        request = self.swap_filesystem_request()
        response = self.valid_swap_filesystem_response()
        response["filesystem"]["unexpected"] = True

        result = self.run_with_mock_ssh(request, response)

        self.assertEqual(result.returncode, 4)
if __name__ == "__main__":
    unittest.main()
