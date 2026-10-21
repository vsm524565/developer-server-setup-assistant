"""Verify structured backend failures reach the Python bridge."""

import io
import json
import unittest
from unittest.mock import patch

from bridge.adapters.linux_backend import LinuxBackend


class FakeProcess:
    def __init__(self, response, exit_code):
        frame = "DSSA_RESPONSE: " + json.dumps(response) + "\n"
        self.stdout = io.BytesIO(frame.encode())
        self.exit_code = exit_code

    def wait(self):
        return self.exit_code


class BridgeFailureTests(unittest.TestCase):
    def test_structured_failure_preserves_partial_results(self):
        response = {
            "schema_version": "1.0",
            "status": "error",
            "results": [
                {
                    "component": "nginx",
                    "package_action": "skipped",
                    "verification": "not_run",
                    "service_action": "unchanged",
                },
                {
                    "component": "git",
                    "execution_state": "failed",
                    "package_action": "unknown",
                    "verification": "unknown",
                    "service_action": "unknown",
                    "exit_code": 17,
                },
            ],
            "errors": [
                {
                    "code": "COMPONENT_FAILED",
                    "component": "git",
                    "exit_code": 17,
                    "message": "Component execution failed.",
                }
            ],
        }

        fake_process = FakeProcess(response, 17)

        with patch(
            "bridge.adapters.linux_backend.subprocess.Popen",
            return_value=fake_process,
        ), patch("bridge.adapters.linux_backend.sys.stdout") as output:
            output.buffer = io.BytesIO()

            result = LinuxBackend().setup_components(
                "ubuntu24", ["nginx", "git"]
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["backend_exit_code"], 17)
        self.assertEqual(result["results"][0]["package_action"], "skipped")
        self.assertEqual(result["results"][1]["execution_state"], "failed")
        self.assertEqual(result["errors"][0]["code"], "COMPONENT_FAILED")


if __name__ == "__main__":
    unittest.main()
