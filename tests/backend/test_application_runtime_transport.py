
"""Verify the SSH and dispatcher runtime JSON contracts."""

import copy
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SOURCES = {
    "ssh": (
        ROOT / "backend/linux/connection/ssh.sh",
        r'discovery\.application_runtime\)\s+'
        r'if ! jq -e --arg requested "\$application_id" '
        r"'(?P<filter>.*?)' <<< \"\$output\"",
        "requested",
    ),
    "dispatcher": (
        ROOT / "backend/linux/bin/dispatcher.sh",
        r"discovery\.application_runtime\)\s+"
        r"validation_filter='(?P<filter>.*?)'\s*;;",
        "requested_application",
    ),
}


def compose_evidence():
    """Create a valid read-only Compose absence response."""

    return {
        "schema_version": "1.0",
        "application_id": "docker_compose",
        "cli": {
            "path": "/usr/bin/docker",
            "status": "unknown",
            "version_output": None,
        },
        "service": {"state": "not_applicable"},
        "socket": {"state": "not_applicable"},
        "plugin": {"state": "absent"},
        "daemon_responsive": None,
        "read_only": True,
        "execution_allowed": False,
    }


def extract_validator(source):
    """Read the actual runtime jq validator from the selected script."""

    path, pattern, variable = SOURCES[source]
    content = path.read_text()
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        raise AssertionError(
            f"Runtime validator could not be located in {path}"
        )

    return match.group("filter"), variable


def validate(source, data):
    """Execute the production jq validation expression."""

    jq_filter, variable = extract_validator(source)

    result = subprocess.run(
        [
            "jq",
            "-e",
            "--arg",
            variable,
            "docker_compose",
            jq_filter,
        ],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=5,
    )

    if result.returncode not in (0, 1, 4):
        raise AssertionError(
            f"jq validator failed unexpectedly: {result.stderr}"
        )

    return result.returncode == 0


class TestApplicationRuntimeTransport(unittest.TestCase):
    """Enforce matching runtime contracts at both transport layers."""

    def test_runtime_contracts(self):
        for source in SOURCES:

            with self.subTest(source=source, scenario="valid_absent"):
                self.assertTrue(
                    validate(source, compose_evidence())
                )

            with self.subTest(source=source, scenario="valid_unknown"):
                data = compose_evidence()
                data["plugin"]["state"] = "unknown"
                self.assertTrue(validate(source, data))

            with self.subTest(source=source, scenario="valid_available"):
                data = compose_evidence()
                data["plugin"]["state"] = "available"
                data["cli"]["status"] = "available"
                data["cli"]["version_output"] = (
                    "Docker Compose version v2.40.0"
                )
                self.assertTrue(validate(source, data))

            with self.subTest(source=source, scenario="wrong_application"):
                data = compose_evidence()
                data["application_id"] = "docker"
                self.assertFalse(validate(source, data))

            with self.subTest(source=source, scenario="execution_enabled"):
                data = compose_evidence()
                data["execution_allowed"] = True
                self.assertFalse(validate(source, data))

            with self.subTest(source=source, scenario="absent_without_cli"):
                data = compose_evidence()
                data["cli"]["path"] = None
                data["cli"]["status"] = "absent"
                self.assertFalse(validate(source, data))

            with self.subTest(source=source, scenario="contradictory_status"):
                data = compose_evidence()
                data["cli"]["status"] = "available"
                data["cli"]["version_output"] = (
                    "Docker Compose version v2.40.0"
                )
                self.assertFalse(validate(source, data))

            with self.subTest(source=source, scenario="unexpected_field"):
                data = compose_evidence()
                data["unexpected"] = True
                self.assertFalse(validate(source, data))


if __name__ == "__main__":
    unittest.main()
