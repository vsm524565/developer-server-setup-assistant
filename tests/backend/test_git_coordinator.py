
"""Regression tests for Git setup coordinator failure reporting."""

import json
import shlex
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COORDINATOR = ROOT / "backend/linux/operations/setup.sh"


class TestGitCoordinator(unittest.TestCase):
    """Validate structured setup failures without contacting a server."""

    def run_failure(self, plan, completed, failed, exit_code):
        """Invoke the production failure emitter with synthetic results."""
        script = f"""
set -euo pipefail
source {shlex.quote(str(COORDINATOR))}

emit_setup_failure \
    {shlex.quote(json.dumps(plan))} \
    {shlex.quote(json.dumps(completed))} \
    {shlex.quote(failed)} \
    {exit_code}
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_git_verification_failure(self):
        """A Git failure must never become a successful setup result."""
        plan = {
            "steps": [
                {"component": "git", "action": "SKIP"}
            ]
        }

        result = self.run_failure(plan, [], "git", 1)

        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(
            result["results"][0]["execution_state"],
            "failed",
        )
        self.assertEqual(
            result["results"][0]["verification"],
            "unknown",
        )
        self.assertEqual(
            result["errors"][0]["code"],
            "COMPONENT_FAILED",
        )
        self.assertEqual(result["errors"][0]["exit_code"], 1)

    def test_installation_failure_preserves_exit_code(self):
        """Retain installer failure evidence for recovery decisions."""
        plan = {
            "steps": [
                {"component": "git", "action": "INSTALL"}
            ]
        }

        result = self.run_failure(plan, [], "git", 17)

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["results"][0]["exit_code"], 17
        )
        self.assertEqual(
            result["errors"][0]["exit_code"], 17
        )
        self.assertIn(
            "partial changes",
            result["errors"][0]["message"],
        )

    def test_completed_results_preserved(self):
        """Preserve completed operations and mark later steps unattempted."""
        plan = {
            "steps": [
                {"component": "nginx", "action": "INSTALL"},
                {"component": "git", "action": "INSTALL"},
            ]
        }

        completed = [{
            "component": "nginx",
            "package_action": "installed",
            "verification": "passed",
            "service_action": "started",
        }]

        result = self.run_failure(
            plan, completed, "git", 9
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["results"][0], completed[0]
        )
        self.assertEqual(
            result["results"][1]["component"], "git"
        )
        self.assertEqual(
            result["results"][1]["execution_state"], "failed"
        )

    def test_unattempted_component_identified(self):
        """Components following a failure must not appear completed."""
        plan = {
            "steps": [
                {"component": "git", "action": "INSTALL"},
                {"component": "nginx", "action": "INSTALL"},
            ]
        }

        result = self.run_failure(
            plan, [], "git", 1
        )

        self.assertEqual(
            result["results"][0]["execution_state"],
            "failed",
        )
        self.assertEqual(
            result["results"][1]["execution_state"],
            "not_attempted",
        )
        self.assertEqual(
            result["results"][1]["verification"],
            "not_run",
        )


if __name__ == "__main__":
    unittest.main()
