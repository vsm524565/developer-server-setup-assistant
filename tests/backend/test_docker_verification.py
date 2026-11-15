
"""Regression tests for Docker Engine verification."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "backend/linux/verification/docker.sh"


class DockerVerificationTests(unittest.TestCase):
    """Verify Docker package and daemon health decisions without Docker."""

    def run_verification(
        self,
        manager="apt",
        missing_package="",
        service_active=True,
        daemon_available=True,
    ):
        """Run the real verifier with mocked package and runtime commands."""

        script = r'''
source "$1"

MISSING_PACKAGE="$2"
SERVICE_ACTIVE="$3"
DAEMON_AVAILABLE="$4"
MANAGER="$5"

package_is_installed() {
    [[ "$2" != "$MISSING_PACKAGE" ]]
}

systemctl() {
    if [[ "$1" == "is-active" && "$2" == "--quiet" &&
          "$3" == "docker.service" ]]; then
        [[ "$SERVICE_ACTIVE" == "yes" ]]
    else
        return 1
    fi
}

docker() {
    [[ "$DAEMON_AVAILABLE" == "yes" ]] || return 1

    case "$1" in
        version|info)
            printf '%s\n' "28.0.0"
            ;;
        *)
            return 1
            ;;
    esac
}

timeout() {
    shift
    "$@"
}

verify_docker "$MANAGER"
'''

        result = subprocess.run(
            [
                "bash", "-c", script, "docker-test",
                str(VERIFIER),
                missing_package,
                "yes" if service_active else "no",
                "yes" if daemon_available else "no",
                manager,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        return result

    def test_healthy_docker_passes(self):
        """Complete packages, active service and responsive daemon pass."""
        result = self.run_verification()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_required_package_fails(self):
        """Incomplete package installations must fail verification."""
        result = self.run_verification(
            missing_package="containerd.io",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_inactive_service_fails(self):
        """An inactive Docker service must not appear healthy."""
        result = self.run_verification(service_active=False)
        self.assertNotEqual(result.returncode, 0)

    def test_unresponsive_daemon_fails(self):
        """Docker CLI availability alone is insufficient."""
        result = self.run_verification(daemon_available=False)
        self.assertNotEqual(result.returncode, 0)

    def test_unsupported_package_manager_fails(self):
        """The verifier must enforce its supported package manager."""
        result = self.run_verification(manager="dnf")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
