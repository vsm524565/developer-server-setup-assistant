
"""Safe pre-installation regression tests for Docker Engine."""

import json
import shlex
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "backend/linux/modules/install/docker.sh"


class DockerInstallerTests(unittest.TestCase):
    """Exercise actual installer guards with package operations disabled."""

    def run_installer(
        self,
        manager="apt",
        os_id="ubuntu",
        os_version="24.04",
        architecture="x86_64",
        installed=(),
        healthy=True,
    ):
        """Run an isolated installer fixture without privileged changes."""

        source = INSTALLER.read_text()

        # Substitute the privilege/platform evidence only in this test copy.
        # The real installer file is not modified.
        root_check = '[[ "$EUID" -eq 0 ]]'
        os_check = "source /etc/os-release"

        self.assertEqual(source.count(root_check), 1)
        self.assertEqual(source.count(os_check), 1)

        source = source.replace(root_check, "true", 1)
        source = source.replace(
            os_check,
            (
                f"ID={shlex.quote(os_id)}\n"
                f"VERSION_ID={shlex.quote(os_version)}"
            ),
            1,
        )

        package_cases = "\n".join(
            f"        {shlex.quote(pkg)}) return 0 ;;"
            for pkg in installed
        )

        script = f"""
set -e

# All tests prohibit actual APT operations.
apt-get() {{
    echo "UNEXPECTED_APT_INVOCATION" >&2
    return 97
}}

package_is_installed() {{
    case "$2" in
{package_cases}
        *) return 1 ;;
    esac
}}

verify_docker() {{
    [[ "$1" == "apt" && "{'yes' if healthy else 'no'}" == "yes" ]]
}}

uname() {{
    if [[ "$1" == "-m" ]]; then
        printf '%s\\n' {shlex.quote(architecture)}
    else
        command uname "$@"
    fi
}}

dpkg() {{
    if [[ "$1" == "--print-architecture" ]]; then
        if [[ {shlex.quote(architecture)} == "x86_64" ]]; then
            echo amd64
        else
            echo arm64
        fi
    else
        command dpkg "$@"
    fi
}}

{source}

install_docker {shlex.quote(manager)}
"""

        result = subprocess.run(
            ["bash", "-c", script],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertNotIn(
            "UNEXPECTED_APT_INVOCATION",
            result.stderr,
            "Installer unexpectedly reached a package-changing operation.",
        )

        return result

    def test_unsupported_manager_rejected(self):
        result = self.run_installer(manager="dnf")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APT only", result.stderr)

    def test_unsupported_os_rejected(self):
        result = self.run_installer(os_id="debian", os_version="13")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Only Ubuntu 24.04", result.stderr)

    def test_unsupported_architecture_rejected(self):
        result = self.run_installer(architecture="aarch64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Only x86_64", result.stderr)

    def test_distribution_conflict_rejected(self):
        result = self.run_installer(installed=("docker.io",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Conflicting package", result.stderr)

    def test_moby_conflict_rejected(self):
        result = self.run_installer(installed=("moby-engine",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Conflicting package", result.stderr)

    def test_partial_vendor_installation_rejected(self):
        result = self.run_installer(installed=("docker-ce-cli",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Partial Docker installation", result.stderr)

    def test_existing_healthy_docker_skipped(self):
        result = self.run_installer(
            installed=(
                "docker-ce",
                "docker-ce-cli",
                "containerd.io",
                "docker-buildx-plugin",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)
        self.assertEqual(response["component"], "docker")
        self.assertEqual(response["package_action"], "skipped")
        self.assertEqual(response["verification"], "passed")
        self.assertEqual(response["service_action"], "unchanged")

    def test_existing_unhealthy_docker_rejected(self):
        result = self.run_installer(
            installed=("docker-ce",),
            healthy=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unhealthy", result.stderr)


if __name__ == "__main__":
    unittest.main()
