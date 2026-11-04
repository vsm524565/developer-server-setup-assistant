
"""Regression tests for passive Docker daemon classification."""

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from bridge.evidence.gate import evaluate_evidence_gate
from bridge.applications.runtime.docker_daemon_preflight import (
    classify_docker_daemon_preflight,
    classify_docker_daemon_preflight_evidence,
)

from bridge.applications.runtime.docker_daemon_preflight import (
    classify_docker_daemon_preflight,
)


def valid_candidate():
    """Build valid passive evidence without contacting Docker."""
    return {
        "schema_version": "1.0",
        "check": "docker_daemon_preflight",
        "service": {
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

def valid_planning_evidence():
    """Build fresh controller evidence for the passive preflight."""

    now = datetime.now(timezone.utc)
    operation = "discovery.docker_daemon_preflight"
    target = "ubuntu24"

    evidence = {
        "schema_version": "1.0",
        "request_id": "docker-preflight-test-001",
        "operation": operation,
        "target_id": target,
        "parameters": {},
        "collection": {
            "started_at_utc": (
                now - timedelta(seconds=3)
            ).isoformat(),
            "completed_at_utc": (
                now - timedelta(seconds=2)
            ).isoformat(),
            "duration_ms": 1000,
            "clock_source": "controller",
        },
        "data": valid_candidate(),
        "execution_allowed": False,
    }

    gate = evaluate_evidence_gate(
        evidence,
        expected_operation=operation,
        expected_target_id=target,
        max_age_seconds=30,
    )

    assert gate["status"] == "ACCEPTED"

    return {
        "evidence": evidence,
        "gate": gate,
        "execution_allowed": False,
    }


class DockerDaemonPreflightTests(unittest.TestCase):
    """Verify classification and the passive-only safety boundary."""

    def test_valid_passive_candidate(self):
        result = classify_docker_daemon_preflight(valid_candidate())

        self.assertEqual(result["status"], "PASSIVE_CANDIDATE")
        self.assertIsNone(result["daemon_responsive"])
        self.assertIs(result["probe_attempted"], False)
        self.assertIs(result["execution_allowed"], False)

    def test_inactive_service(self):
        data = valid_candidate()
        data["service"] = {
            "state": "inactive",
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
        data["status"] = "SERVICE_INACTIVE"
        data["reasons"] = ["DOCKER_SERVICE_INACTIVE"]

        result = classify_docker_daemon_preflight(data)
        self.assertEqual(result["status"], "DAEMON_INACTIVE")

    def test_failed_service(self):
        data = valid_candidate()
        data["service"]["state"] = "failed"
        data["status"] = "SERVICE_FAILED"
        data["reasons"] = ["DOCKER_SERVICE_FAILED"]

        result = classify_docker_daemon_preflight(data)
        self.assertEqual(result["status"], "DAEMON_FAILED")

    def test_unverified_service(self):
        data = valid_candidate()
        data["service"]["state"] = "unknown"
        data["status"] = "UNVERIFIED"
        data["reasons"] = ["INSUFFICIENT_PASSIVE_EVIDENCE"]

        result = classify_docker_daemon_preflight(data)
        self.assertEqual(result["status"], "DAEMON_UNVERIFIED")

    def test_reject_unsafe_policy(self):
        changes = (
            ("probe_attempted", True),
            ("daemon_responsive", True),
            ("execution_allowed", True),
            ("read_only", False),
        )

        for field, value in changes:
            with self.subTest(field=field):
                data = valid_candidate()
                data[field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight(data)

    def test_reject_invalid_identity(self):
        for field, value in (
            ("schema_version", "2.0"),
            ("check", "docker_daemon_health"),
            ("reasons", ["HEALTHY"]),
        ):
            with self.subTest(field=field):
                data = valid_candidate()
                data[field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight(data)

    def test_reject_invalid_service(self):
        changes = (
            ("main_pid", True),
            ("main_pid", -1),
            ("main_pid", 0),
            ("state", "healthy"),
        )

        for field, value in changes:
            with self.subTest(field=field):
                data = valid_candidate()
                data["service"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight(data)

    def test_reject_invalid_socket(self):
        changes = (
            ("path", "/tmp/docker.sock"),
            ("permissions", "999"),
            ("listener_state", "connected"),
            ("owner", ""),
        )

        for field, value in changes:
            with self.subTest(field=field):
                data = valid_candidate()
                data["socket"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight(data)

    def test_reject_contradictory_status(self):
        data = valid_candidate()
        data["socket"]["listener_state"] = "absent"

        with self.assertRaises(ValueError):
            classify_docker_daemon_preflight(data)

    def test_reject_missing_socket_with_listener(self):
        data = valid_candidate()
        data["socket"].update({
            "present": False,
            "permissions": None,
            "owner": None,
            "group": None,
        })

        with self.assertRaises(ValueError):
            classify_docker_daemon_preflight(data)

    def test_reject_missing_or_extra_fields(self):
        for change in ("missing", "extra"):
            with self.subTest(change=change):
                data = deepcopy(valid_candidate())

                if change == "missing":
                    del data["socket"]
                else:
                    data["unexpected"] = True

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight(data)


    def test_evidence_wrapper_accepts_fresh_evidence(self):
        planning = valid_planning_evidence()

        result = classify_docker_daemon_preflight_evidence(
            planning,
            expected_target_id="ubuntu24",
        )

        self.assertEqual(result["status"], "PASSIVE_CANDIDATE")
        self.assertEqual(result["target_id"], "ubuntu24")
        self.assertEqual(
            result["evidence_request_id"],
            "docker-preflight-test-001",
        )
        self.assertIsNone(result["daemon_responsive"])
        self.assertIs(result["probe_attempted"], False)
        self.assertIs(result["planning_only"], True)
        self.assertIs(result["execution_allowed"], False)

    def test_evidence_wrapper_rejects_stale_timestamps(self):
        planning = valid_planning_evidence()
        collection = planning["evidence"]["collection"]

        now = datetime.now(timezone.utc)
        collection["started_at_utc"] = (
            now - timedelta(seconds=62)
        ).isoformat()
        collection["completed_at_utc"] = (
            now - timedelta(seconds=61)
        ).isoformat()

        # Keep the original accepted planning gate to verify
        # that independent freshness validation cannot be bypassed.
        with self.assertRaises(ValueError):
            classify_docker_daemon_preflight_evidence(
                planning,
                expected_target_id="ubuntu24",
            )

    def test_evidence_wrapper_rejects_invalid_timing(self):
        changes = (
            ("clock_source", "remote"),
            ("duration_ms", 10000),
            ("completed_at_utc", "invalid-timestamp"),
        )

        for field, value in changes:
            with self.subTest(field=field):
                planning = valid_planning_evidence()
                planning["evidence"]["collection"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight_evidence(
                        planning,
                        expected_target_id="ubuntu24",
                    )

    def test_evidence_wrapper_rejects_identity_mismatch(self):
        changes = (
            ("target_id", "ubuntu22"),
            ("operation", "discovery.application_runtime"),
            ("parameters", {"probe": True}),
            ("request_id", ""),
        )

        for field, value in changes:
            with self.subTest(field=field):
                planning = valid_planning_evidence()
                planning["evidence"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight_evidence(
                        planning,
                        expected_target_id="ubuntu24",
                    )

    def test_evidence_wrapper_rejects_invalid_gate(self):
        changes = (
            ("status", "REJECTED"),
            ("planning_allowed", False),
            ("execution_allowed", True),
        )

        for field, value in changes:
            with self.subTest(field=field):
                planning = valid_planning_evidence()
                planning["gate"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight_evidence(
                        planning,
                        expected_target_id="ubuntu24",
                    )

    def test_evidence_wrapper_rejects_fabricated_health(self):
        changes = (
            ("probe_attempted", True),
            ("daemon_responsive", True),
            ("execution_allowed", True),
            ("read_only", False),
        )

        for field, value in changes:
            with self.subTest(field=field):
                planning = valid_planning_evidence()
                planning["evidence"]["data"][field] = value

                with self.assertRaises(ValueError):
                    classify_docker_daemon_preflight_evidence(
                        planning,
                        expected_target_id="ubuntu24",
                    )

if __name__ == "__main__":
    unittest.main()
