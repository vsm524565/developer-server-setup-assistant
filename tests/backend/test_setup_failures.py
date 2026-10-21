"""Regression tests for structured component failure responses."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "backend/linux/operations/setup.sh"


class SetupFailureTests(unittest.TestCase):

    def test_preserves_completed_result_and_reports_failed_component(self):
        plan = {
            "steps": [
                {"component": "nginx", "action": "SKIP"},
                {"component": "git", "action": "INSTALL"},
            ]
        }

        completed = [
            {
                "component": "nginx",
                "package_action": "skipped",
                "verification": "not_run",
                "service_action": "unchanged",
            }
        ]

        script = """
source "$1"
emit_setup_failure "$2" "$3" git 17
"""

        result = subprocess.run(
            [
                "bash",
                "-c",
                script,
                "bash",
                str(SETUP),
                json.dumps(plan),
                json.dumps(completed),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["schema_version"], "1.0")
        self.assertEqual(response["status"], "error")
        self.assertEqual(len(response["results"]), 2)

        nginx, git = response["results"]

        self.assertEqual(nginx, completed[0])

        self.assertEqual(git["component"], "git")
        self.assertEqual(git["execution_state"], "failed")
        self.assertEqual(git["package_action"], "unknown")
        self.assertEqual(git["verification"], "unknown")
        self.assertEqual(git["exit_code"], 17)

        self.assertEqual(response["errors"][0]["code"], "COMPONENT_FAILED")
        self.assertEqual(response["errors"][0]["component"], "git")
        self.assertEqual(response["errors"][0]["exit_code"], 17)


if __name__ == "__main__":
    unittest.main()
