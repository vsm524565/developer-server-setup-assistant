
"""Regression tests for the existing Bash Git installer.

All package-management operations are mocked. No installation or
remote server access occurs during these tests.
"""

import json
import shlex
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "backend/linux/modules/install/git.sh"


class TestGitInstaller(unittest.TestCase):
    """Exercise Git installation outcomes with mocked Bash functions."""

    def run_installer(self, functions, manager="apt"):
        """Execute the sourced installer with isolated shell mocks."""
        script = f"""
set -euo pipefail
source {shlex.quote(str(INSTALLER))}
{functions}
install_git {shlex.quote(manager)}
"""
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def test_existing_git_verified_without_reinstallation(self):
        """Installed Git must be verified without touching packages."""
        result = self.run_installer("""
package_is_installed() { return 0; }
verify_git() { return 0; }
refresh_package_metadata() {
    echo UNEXPECTED_REFRESH >&2
    return 99
}
install_package() {
    echo UNEXPECTED_INSTALL >&2
    return 99
}
""")

        self.assertEqual(result.returncode, 0, result.stderr)

        report = json.loads(result.stdout)
        self.assertEqual(report["component"], "git")
        self.assertEqual(report["package_action"], "skipped")
        self.assertEqual(report["verification"], "passed")
        self.assertNotIn("UNEXPECTED", result.stderr)

    def test_broken_existing_git_is_rejected(self):
        """Never mark an unusable existing Git installation successful."""
        result = self.run_installer("""
package_is_installed() { return 0; }
verify_git() { return 1; }
refresh_package_metadata() {
    echo UNEXPECTED_REFRESH >&2
    return 99
}
install_package() {
    echo UNEXPECTED_INSTALL >&2
    return 99
}
""")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("failed verification", result.stderr)
        self.assertNotIn("UNEXPECTED", result.stderr)

    def test_missing_git_installs_and_verifies(self):
        """A missing Git package follows the intended install sequence."""
        result = self.run_installer("""
installed=0

package_is_installed() {
    [[ "$installed" == 1 ]]
}

command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "git" ]]; then
        return 1
    fi
    builtin command "$@"
}

refresh_package_metadata() {
    [[ "$1" == "apt" ]]
}

install_package() {
    [[ "$1" == "apt" && "$2" == "git" ]] || return 1
    installed=1
}

verify_git() {
    [[ "$installed" == 1 ]]
}
""")

        self.assertEqual(result.returncode, 0, result.stderr)

        report = json.loads(result.stdout)
        self.assertEqual(report["package_action"], "installed")
        self.assertEqual(report["verification"], "passed")

    def test_metadata_failure_prevents_installation(self):
        """Do not install when repository metadata cannot be refreshed."""
        result = self.run_installer("""
package_is_installed() { return 1; }

command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "git" ]]; then
        return 1
    fi
    builtin command "$@"
}

refresh_package_metadata() { return 1; }
install_package() {
    echo UNEXPECTED_INSTALL >&2
    return 99
}
""")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("metadata refresh failed", result.stderr)
        self.assertNotIn("UNEXPECTED_INSTALL", result.stderr)

    def test_package_installation_failure_is_reported(self):
        """Installation failure must not be represented as success."""
        result = self.run_installer("""
package_is_installed() { return 1; }

command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "git" ]]; then
        return 1
    fi
    builtin command "$@"
}

refresh_package_metadata() { return 0; }
install_package() { return 1; }
verify_git() {
    echo UNEXPECTED_VERIFY >&2
    return 99
}
""")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("Git installation failed", result.stderr)
        self.assertNotIn("UNEXPECTED_VERIFY", result.stderr)

    def test_unmanaged_git_is_blocked(self):
        """An unmanaged executable requires review, not replacement."""
        result = self.run_installer("""
package_is_installed() { return 1; }
refresh_package_metadata() {
    echo UNEXPECTED_REFRESH >&2
    return 99
}
install_package() {
    echo UNEXPECTED_INSTALL >&2
    return 99
}
""")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("Unmanaged Git executable", result.stderr)
        self.assertNotIn("UNEXPECTED", result.stderr)


if __name__ == "__main__":
    unittest.main()
