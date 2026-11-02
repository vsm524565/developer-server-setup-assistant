
"""Read-only tests for Bash package database discovery."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = (
    ROOT / "backend/linux/modules/software/application_state.sh"
)


class TestApplicationPackageQuery(unittest.TestCase):

    def run_query(self, manager, package, command, script):
        """Run a package query with a simulated package database."""

        with tempfile.TemporaryDirectory() as directory:
            fake_command = Path(directory) / command
            fake_command.write_text(
                "#!/usr/bin/env bash\n" + script,
                encoding="utf-8",
            )
            fake_command.chmod(0o755)

            environment = os.environ.copy()
            environment["PATH"] = (
                directory + os.pathsep + environment.get("PATH", "")
            )

            # Mocked executables are chosen via PATH. No real
            # package installation or modification occurs.
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'set -euo pipefail\n'
                    'source "$1"\n'
                    'application_package_state "$2" "$3"',
                    "bash",
                    str(COLLECTOR),
                    manager,
                    package,
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )

            return result.stdout.strip()

    def test_apt_installed(self):
        result = self.run_query(
            "apt",
            "git",
            "dpkg-query",
            "printf 'git\\tii \\nnginx\\trc \\n'\n",
        )
        self.assertEqual(result, "installed")

    def test_apt_absent(self):
        result = self.run_query(
            "apt",
            "nginx",
            "dpkg-query",
            "printf 'git\\tii \\nnginx\\trc \\n'\n",
        )
        self.assertEqual(result, "absent")

    def test_apt_unknown_package(self):
        result = self.run_query(
            "apt",
            "nonexistent-package",
            "dpkg-query",
            "printf 'git\\tii \\n'\n",
        )
        self.assertEqual(result, "absent")

    def test_apt_database_failure(self):
        result = self.run_query(
            "apt",
            "git",
            "dpkg-query",
            "exit 1\n",
        )
        self.assertEqual(result, "unknown")

    def test_dnf_installed(self):
        result = self.run_query(
            "dnf",
            "git",
            "rpm",
            "printf 'git\\nnginx\\n'\n",
        )
        self.assertEqual(result, "installed")

    def test_dnf_absent(self):
        result = self.run_query(
            "dnf",
            "docker-ce",
            "rpm",
            "printf 'git\\nnginx\\n'\n",
        )
        self.assertEqual(result, "absent")

    def test_dnf_database_failure(self):
        result = self.run_query(
            "dnf",
            "git",
            "rpm",
            "exit 1\n",
        )
        self.assertEqual(result, "unknown")

    def test_invalid_package_identifier(self):
        result = self.run_query(
            "apt",
            "git;echo unsafe",
            "dpkg-query",
            "printf 'git\\tii \\n'\n",
        )
        self.assertEqual(result, "unknown")


if __name__ == "__main__":
    unittest.main()
