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
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$@\" > \"$SSH_TEST_CAPTURE\"\n"
            "printf '%s\\n' "
            "'{\"hostname\":\"ubuntutest\","
            "\"os_id\":\"ubuntu\","
            "\"os_version\":\"24.04\","
            "\"architecture\":\"x86_64\","
            "\"cpu_logical_cores\":3,"
            "\"cpu_model\":\"Intel Core i7\","
            "\"memory_total_bytes\":4105826304,"
            "\"memory_available_bytes\":3617198080,"
            "\"disk_total_bytes\":25180848128,"
            "\"disk_used_bytes\":6938574848,"
            "\"disk_available_bytes\":16937263104,"
            "\"uptime_seconds\":109534,"
            "\"virtualization\":\"oracle\"}'\n"
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


if __name__ == "__main__":
    unittest.main()
