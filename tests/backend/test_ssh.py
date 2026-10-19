import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SSH_SCRIPT = ROOT / "backend/linux/connection/ssh.sh"


class SSHTransportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        ssh_dir = self.home / ".ssh"
        ssh_dir.mkdir(parents=True)

        (ssh_dir / "id_ed25519").write_text("test-key")
        (ssh_dir / "known_hosts").write_text("test-host-key")

        self.inventory = self.base / "inventory.json"
        self.inventory.write_text(json.dumps({
            "schema_version": "1.0",
            "servers": [{
                "target_id": "lab-ubuntu-01",
                "platform": "linux",
                "host": "192.168.56.109",
                "ssh_port": 22,
                "ssh_user": "vivek",
                "enabled": True
            }]
        }))
        self.inventory.chmod(0o600)

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["DSSA_INVENTORY_FILE"] = str(self.inventory)

        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        self.capture = self.base / "ssh-arguments.txt"

        mock_ssh = self.bin_dir / "ssh"
        mock_ssh.write_text(
            """#!/usr/bin/env bash
        printf '%s\\n' "$@" > "$SSH_TEST_CAPTURE"

        if [[ -n "${SSH_TEST_RESPONSE_FILE:-}" ]]; then
            cat "$SSH_TEST_RESPONSE_FILE"
            exit 0
        fi

        printf '%s\\n' '{
            "hostname": "ubuntutest",
            "os_id": "ubuntu",
            "os_version": "24.04",
            "architecture": "x86_64",
            "cpu_logical_cores": 3,
            "cpu_model": "Intel Core i7",
            "memory_total_bytes": 4105826304,
            "memory_available_bytes": 3617198080,
            "disk_total_bytes": 25180848128,
            "disk_used_bytes": 6938574848,
            "disk_available_bytes": 16937263104,
            "uptime_seconds": 109534,
            "virtualization": "oracle"
        }'
        """
        )
        mock_ssh.chmod(0o700)

        self.env["PATH"] = f"{self.bin_dir}:{self.env['PATH']}"
        self.env["SSH_TEST_CAPTURE"] = str(self.capture)

    def run_ssh(self, target, operation):
        return subprocess.run(
            [str(SSH_SCRIPT), target, operation],
            text=True,
            capture_output=True,
            env=self.env,
            timeout=10,
        )
    def set_mock_response(self, response):
        response_file = self.base / "mock-response.json"

        if isinstance(response, str):
            response_file.write_text(response)
        else:
            response_file.write_text(json.dumps(response))

        self.env["SSH_TEST_RESPONSE_FILE"] = str(response_file)

    def test_approved_discovery(self):
        result = self.run_ssh("lab-ubuntu-01", "discovery.system")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["hostname"], "ubuntutest")
        self.assertEqual(data["os_id"], "ubuntu")
        self.assertEqual(data["os_version"], "24.04")
        self.assertEqual(data["architecture"], "x86_64")
        self.assertEqual(data["cpu_logical_cores"], 3)
        self.assertEqual(data["cpu_model"], "Intel Core i7")
        self.assertEqual(data["memory_total_bytes"], 4105826304)
        self.assertGreaterEqual(data["memory_available_bytes"], 0)
        self.assertGreater(data["disk_total_bytes"], 0)
        self.assertGreaterEqual(data["disk_available_bytes"], 0)
        self.assertEqual(data["virtualization"], "oracle")
        self.assertGreaterEqual(data["uptime_seconds"], 0)

        args = self.capture.read_text().splitlines()
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("PasswordAuthentication=no", args)
        self.assertIn("192.168.56.109", args)
        self.assertIn("bash -s", args)

    def test_unknown_target(self):
        result = self.run_ssh("unknown-server", "discovery.system")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_unsupported_operation(self):
        result = self.run_ssh("lab-ubuntu-01", "install.package")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.capture.exists())

    def test_invalid_target_identifier(self):
        result = self.run_ssh("../server", "discovery.system")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_disabled_target(self):
        data = json.loads(self.inventory.read_text())
        data["servers"][0]["enabled"] = False
        self.inventory.write_text(json.dumps(data))

        result = self.run_ssh("lab-ubuntu-01", "discovery.system")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_package_manager_discovery(self):
        self.set_mock_response({
            "available_package_tools": ["apt-get", "dpkg"],
            "package_manager_detected": True
        })

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.package_manager"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        data = json.loads(result.stdout)

        self.assertEqual(
            data["available_package_tools"],
            ["apt-get", "dpkg"]
        )
        self.assertTrue(data["package_manager_detected"])

        args = self.capture.read_text().splitlines()
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("bash -s", args)


    def test_package_manager_rejects_invalid_json(self):
        self.set_mock_response('{"invalid":')

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.package_manager"
        )

        self.assertEqual(result.returncode, 5)
        self.assertIn(
            "Invalid remote package manager discovery response",
            result.stderr
        )


    def test_package_manager_rejects_invalid_schema(self):
        self.set_mock_response({
            "available_package_tools": ["apt-get", "dpkg"],
            "package_manager_detected": False
        })

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.package_manager"
        )

        self.assertEqual(result.returncode, 5)

    def test_runtime_discovery(self):
        self.set_mock_response({
            "runtimes": {
                "python3": {
                    "available": True,
                    "path": "/usr/bin/python3",
                    "version": "Python 3.12.3"
                },
                "php": {
                    "available": False,
                    "path": None,
                    "version": None
                },
                "node": {
                    "available": False,
                    "path": None,
                    "version": None
                },
                "java": {
                    "available": False,
                    "path": None,
                    "version": None
                }
            }
        })

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.runtimes"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        data = json.loads(result.stdout)
        runtimes = data["runtimes"]

        self.assertEqual(
            set(runtimes),
            {"python3", "php", "node", "java"}
        )
        self.assertTrue(runtimes["python3"]["available"])
        self.assertEqual(
            runtimes["python3"]["path"],
            "/usr/bin/python3"
        )
        self.assertFalse(runtimes["php"]["available"])

        args = self.capture.read_text().splitlines()
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("bash -s", args)

    def test_runtime_rejects_invalid_json(self):
        self.set_mock_response('{"invalid":')

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.runtimes"
        )

        self.assertEqual(result.returncode, 5)

    def test_runtime_rejects_invalid_schema(self):
        self.set_mock_response({
            "runtimes": {
                "python3": {
                    "available": "yes",
                    "path": "/usr/bin/python3",
                    "version": "Python 3.12.3"
                }
            }
        })

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.runtimes"
        )

        self.assertEqual(result.returncode, 5)

    def valid_web_server_response(self):
        """Return a valid web server discovery response for testing."""
        def absent():
            return {
                "available": False,
                "path": None,
                "version": None,
                "service_name": None,
                "service_state": "unknown",
                "detection_status": "absent",
            }

        return {
            "web_servers": {
                "nginx": {
                    "available": True,
                    "path": "/usr/sbin/nginx",
                    "version": "nginx version: nginx/1.24.0",
                    "service_name": "nginx.service",
                    "service_state": "active",
                    "detection_status": "detected",
                },
                "apache": absent(),
                "openlitespeed": absent(),
                "litespeed": absent(),
            }
        }

    def test_web_server_discovery(self):
        """Valid web server discovery must pass SSH validation."""
        self.set_mock_response(self.valid_web_server_response())

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.web_servers",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        data = json.loads(result.stdout)
        servers = data["web_servers"]

        self.assertEqual(
            set(servers),
            {"nginx", "apache", "openlitespeed", "litespeed"},
        )
        self.assertTrue(servers["nginx"]["available"])
        self.assertEqual(servers["nginx"]["service_state"], "active")
        self.assertFalse(servers["apache"]["available"])

        args = self.capture.read_text().splitlines()
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("bash -s", args)

    def test_web_server_rejects_invalid_json(self):
        """Malformed remote JSON must be rejected."""
        self.set_mock_response('{"web_servers":')

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.web_servers",
        )

        self.assertEqual(result.returncode, 5)

    def test_web_server_rejects_missing_field(self):
        """Missing required discovery fields must be rejected."""
        response = self.valid_web_server_response()
        del response["web_servers"]["nginx"]["service_state"]

        self.set_mock_response(response)

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.web_servers",
        )

        self.assertEqual(result.returncode, 5)

    def test_web_server_rejects_inconsistent_detection(self):
        """Unavailable software cannot have detected status."""
        response = self.valid_web_server_response()

        response["web_servers"]["nginx"]["available"] = False
        response["web_servers"]["nginx"]["path"] = None
        response["web_servers"]["nginx"]["version"] = None

        # Deliberately leave detection_status as "detected".
        self.set_mock_response(response)

        result = self.run_ssh(
            "lab-ubuntu-01",
            "discovery.web_servers",
        )

        self.assertEqual(result.returncode, 5)

if __name__ == "__main__":
    unittest.main()
