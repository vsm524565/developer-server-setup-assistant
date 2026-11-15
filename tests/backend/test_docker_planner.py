
"""Docker planner compatibility and package-state regressions."""

import json
import shlex
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT / "backend/linux/planning/components.sh"


class DockerPlannerTests(unittest.TestCase):
    """Exercise the real Docker planner with controlled package evidence."""

    def run_plan(self, manager, os_id, version, installed=()):
        """Run planning without changing OS files or packages."""

        # The fixture replaces only the os-release read in this test copy.
        source = PLANNER.read_text()
        self.assertIn("source /etc/os-release", source)

        fixture = (
            f"ID={shlex.quote(os_id)}\n"
            f"VERSION_ID={shlex.quote(version)}\n"
        )

        source = source.replace(
            "source /etc/os-release",
            fixture,
        )

        packages = " ".join(shlex.quote(pkg) for pkg in installed)

        script = f"""
set -e
package_is_installed() {{
    case " {packages} " in
        *" $2 "*) return 0 ;;
        *) return 1 ;;
    esac
}}

uname() {{
    if [[ "$1" == "-m" ]]; then
        echo x86_64
    else
        command uname "$@"
    fi
}}

dpkg() {{
    if [[ "$1" == "--print-architecture" ]]; then
        echo amd64
    else
        command dpkg "$@"
    fi
}}

{source}

plan_docker_component {shlex.quote(manager)}
"""

        result = subprocess.run(
            ["bash", "-c", script],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_debian_is_not_eligible(self):
        result = self.run_plan("apt", "debian", "13")
        self.assertEqual(result["action"], "REVIEW")

    def test_ubuntu22_is_not_eligible(self):
        result = self.run_plan("apt", "ubuntu", "22.04")
        self.assertEqual(result["action"], "REVIEW")

    def test_ubuntu26_is_not_eligible(self):
        result = self.run_plan("apt", "ubuntu", "26.04")
        self.assertEqual(result["action"], "REVIEW")

    def test_dnf_is_not_eligible(self):
        result = self.run_plan("dnf", "almalinux", "10")
        self.assertEqual(result["action"], "REVIEW")

    def test_partial_docker_installation_requires_review(self):
        result = self.run_plan(
            "apt",
            "ubuntu",
            "24.04",
            installed=("docker-ce",),
        )
        self.assertEqual(result["action"], "REVIEW")
        self.assertIn(
            "Partial Docker vendor installation",
            result["reasons"][0],
        )

    def test_distribution_docker_conflict_requires_review(self):
        result = self.run_plan(
            "apt",
            "ubuntu",
            "24.04",
            installed=("docker.io",),
        )
        self.assertEqual(result["action"], "REVIEW")
        self.assertIn(
            "Conflicting package",
            result["reasons"][0],
        )

    def test_complete_vendor_package_set_can_be_skipped(self):
        result = self.run_plan(
            "apt",
            "ubuntu",
            "24.04",
            installed=(
                "docker-ce",
                "docker-ce-cli",
                "containerd.io",
                "docker-buildx-plugin",
            ),
        )
        self.assertEqual(result["action"], "SKIP")
        self.assertIn(
            "runtime verification required",
            result["reasons"][0],
        )


if __name__ == "__main__":
    unittest.main()
