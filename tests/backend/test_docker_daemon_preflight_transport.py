
"""Validate the actual SSH and dispatcher Docker preflight contracts."""

import json
import re
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SOURCES = {
    "ssh": (
        ROOT / "backend/linux/connection/ssh.sh",
        r'discovery\.docker_daemon_preflight\)\s+'
        r"if ! jq -e '(?P<filter>.*?)' <<< \"\$output\"",
    ),
    "dispatcher": (
        ROOT / "backend/linux/bin/dispatcher.sh",
        r"discovery\.docker_daemon_preflight\)\s+"
        r"validation_filter='(?P<filter>.*?)'\s*;;",
    ),
}


def valid_candidate():
    """Return valid passive observations for a loaded Docker unit."""
    return {
        "schema_version": "1.0",
        "check": "docker_daemon_preflight",
        "service": {
            "load_state": "loaded",
            "state": "active",
            "main_pid": 2937,
            "process_verified": True,
        },
        "socket": {
            "unit_state": "active",
            "path": "/run/docker.sock",
            "present": True,
            "permissions": "660",
            "owner": "root",
            "group": "docker",
            "listener_state": "present",
        },
        "status": "PASSIVE_CANDIDATE",
        "reasons": ["DAEMON_PROCESS_AND_SOCKET_OBSERVED"],
        "probe_attempted": False,
        "daemon_responsive": None,
        "read_only": True,
        "execution_allowed": False,
    }


def with_state(load_state, active_state, status, reason):
    """Build a passive service-unit classification fixture."""
    data = valid_candidate()

    data["service"] = {
        "load_state": load_state,
        "state": active_state,
        "main_pid": 0,
        "process_verified": False,
    }

    data["socket"].update({
        "unit_state": "inactive",
        "present": False,
        "permissions": None,
        "owner": None,
        "group": None,
        "listener_state": "absent",
    })

    data["status"] = status
    data["reasons"] = [reason]

    return data


def extract_filter(source):
    """Extract the real production jq validator."""
    path, pattern = SOURCES[source]
    match = re.search(pattern, path.read_text(), re.DOTALL)

    if match is None:
        raise AssertionError(
            f"Docker preflight validator not found in {path}"
        )

    return match.group("filter")


def validate(source, data):
    """Evaluate the selected production validator against JSON."""
    result = subprocess.run(
        ["jq", "-e", extract_filter(source)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=5,
    )

    if result.returncode not in (0, 1, 4):
        raise AssertionError(
            f"jq validation error in {source}: {result.stderr}"
        )

    return result.returncode == 0


class DockerPreflightTransportTests(unittest.TestCase):
    """Ensure SSH and dispatcher independently enforce the contract."""

    def test_valid_service_states(self):
        scenarios = {
            "candidate": valid_candidate(),
            "not_found": with_state(
                "not-found", "inactive",
                "SERVICE_NOT_FOUND",
                "DOCKER_SERVICE_UNIT_NOT_FOUND",
            ),
            "masked": with_state(
                "masked", "inactive",
                "SERVICE_MASKED",
                "DOCKER_SERVICE_UNIT_MASKED",
            ),
            "inactive": with_state(
                "loaded", "inactive",
                "SERVICE_INACTIVE",
                "DOCKER_SERVICE_INACTIVE",
            ),
            "failed": with_state(
                "loaded", "failed",
                "SERVICE_FAILED",
                "DOCKER_SERVICE_FAILED",
            ),
            "unverified": with_state(
                "error", "inactive",
                "UNVERIFIED",
                "INSUFFICIENT_PASSIVE_EVIDENCE",
            ),
        }

        for source in SOURCES:
            for name, data in scenarios.items():
                with self.subTest(source=source, scenario=name):
                    self.assertTrue(
                        validate(source, data),
                        f"{source} rejected valid {name}",
                    )

    def test_reject_invalid_contracts(self):
        base = with_state(
            "not-found", "inactive",
            "SERVICE_NOT_FOUND",
            "DOCKER_SERVICE_UNIT_NOT_FOUND",
        )

        cases = {}

        sample = deepcopy(base)
        sample["service"]["load_state"] = "loaded"
        cases["not_found_but_loaded"] = sample

        sample = with_state(
            "masked", "inactive",
            "SERVICE_MASKED",
            "DOCKER_SERVICE_UNIT_MASKED",
        )
        sample["service"]["load_state"] = "not-found"
        cases["masked_but_missing"] = sample

        sample = valid_candidate()
        sample["service"]["load_state"] = "not-found"
        cases["fabricated_candidate"] = sample

        sample = deepcopy(base)
        sample["service"]["load_state"] = "unexpected"
        cases["invalid_load_state"] = sample

        sample = deepcopy(base)
        del sample["service"]["load_state"]
        cases["missing_load_state"] = sample

        sample = deepcopy(base)
        sample["status"] = "SERVICE_INACTIVE"
        sample["reasons"] = ["DOCKER_SERVICE_INACTIVE"]
        cases["wrong_status"] = sample

        sample = deepcopy(base)
        sample["probe_attempted"] = True
        cases["active_probe"] = sample

        sample = deepcopy(base)
        sample["daemon_responsive"] = True
        cases["fabricated_health"] = sample

        sample = deepcopy(base)
        sample["execution_allowed"] = True
        cases["execution_enabled"] = sample

        sample = deepcopy(base)
        sample["read_only"] = False
        cases["read_only_disabled"] = sample

        sample = deepcopy(base)
        sample["socket"]["listener_state"] = "present"
        cases["missing_socket_with_listener"] = sample

        sample = deepcopy(base)
        sample["unexpected"] = True
        cases["unexpected_field"] = sample

        for source in SOURCES:
            for name, data in cases.items():
                with self.subTest(source=source, scenario=name):
                    self.assertFalse(
                        validate(source, data),
                        f"{source} accepted invalid {name}",
                    )


if __name__ == "__main__":
    unittest.main()
