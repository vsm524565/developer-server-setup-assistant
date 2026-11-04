
"""Regression tests for passive Docker daemon readiness reconciliation."""

import unittest
from copy import deepcopy

from bridge.applications.readiness.daemon_reconciliation import (
    reconcile_daemon_readiness,
)


def readiness(status="REVIEW_REQUIRED"):
    """Create an eight-source, runtime-aware Docker readiness result."""
    request_ids = {
        f"discovery.source_{index}": f"request-{index}"
        for index in range(7)
    }
    request_ids["discovery.application_runtime"] = "request-7"

    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": status,
        "runtime_status": "CLI_ABSENT",
        "reasons": [],
        "evidence_request_ids": request_ids,
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }


def daemon(
    status="DAEMON_UNIT_NOT_FOUND",
    load_state="not-found",
    service_state="inactive",
):
    """Create classified passive daemon evidence."""
    source_statuses = {
        "DAEMON_UNIT_NOT_FOUND": "SERVICE_NOT_FOUND",
        "DAEMON_UNIT_MASKED": "SERVICE_MASKED",
        "DAEMON_INACTIVE": "SERVICE_INACTIVE",
        "DAEMON_FAILED": "SERVICE_FAILED",
        "DAEMON_UNVERIFIED": "UNVERIFIED",
        "PASSIVE_CANDIDATE": "PASSIVE_CANDIDATE",
    }

    source_reasons = {
        "DAEMON_UNIT_NOT_FOUND": "DOCKER_SERVICE_UNIT_NOT_FOUND",
        "DAEMON_UNIT_MASKED": "DOCKER_SERVICE_UNIT_MASKED",
        "DAEMON_INACTIVE": "DOCKER_SERVICE_INACTIVE",
        "DAEMON_FAILED": "DOCKER_SERVICE_FAILED",
        "DAEMON_UNVERIFIED": "INSUFFICIENT_PASSIVE_EVIDENCE",
        "PASSIVE_CANDIDATE": "DAEMON_PROCESS_AND_SOCKET_OBSERVED",
    }

    candidate = status == "PASSIVE_CANDIDATE"

    return {
        "check": "docker_daemon_preflight_classification",
        "target_id": "ubuntu24",
        "evidence_request_id": "request-8",
        "status": status,
        "source_status": source_statuses[status],
        "service_load_state": load_state,
        "service_state": service_state,
        "process_verified": candidate,
        "socket_present": candidate,
        "socket_listener_state": (
            "present" if candidate else "absent"
        ),
        "reasons": [source_reasons[status]],
        "daemon_responsive": None,
        "probe_attempted": False,
        "planning_only": True,
        "execution_allowed": False,
    }


class TestDaemonReconciliation(unittest.TestCase):
    """Verify status preservation, identities, and passive-only policy."""

    def test_six_daemon_classifications(self):
        scenarios = (
            ("DAEMON_UNIT_NOT_FOUND", "not-found", "inactive"),
            ("DAEMON_UNIT_MASKED", "masked", "inactive"),
            ("DAEMON_INACTIVE", "loaded", "inactive"),
            ("DAEMON_FAILED", "loaded", "failed"),
            ("DAEMON_UNVERIFIED", "unknown", "unknown"),
            ("PASSIVE_CANDIDATE", "loaded", "active"),
        )

        for status, load_state, service_state in scenarios:
            with self.subTest(status=status):
                observation = daemon(
                    status, load_state, service_state
                )
                result = reconcile_daemon_readiness(
                    readiness(), observation
                )

                self.assertEqual(
                    result["daemon_preflight_status"], status
                )
                self.assertEqual(
                    result["status"], "REVIEW_REQUIRED"
                )
                self.assertEqual(
                    len(result["evidence_request_ids"]), 9
                )
                self.assertEqual(
                    result["evidence_request_ids"][
                        "discovery.docker_daemon_preflight"
                    ],
                    "request-8",
                )
                self.assertIsNone(result["daemon_responsive"])
                self.assertIs(result["planning_only"], True)
                self.assertIs(result["execution_allowed"], False)

    def test_existing_blocker_preserved(self):
        result = reconcile_daemon_readiness(
            readiness("BLOCKED"), daemon()
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_ready_installed_downgraded(self):
        result = reconcile_daemon_readiness(
            readiness("READY_INSTALLED"), daemon()
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIn(
            "DAEMON_HEALTH_NOT_VERIFIED", result["reasons"]
        )

    def test_install_candidate_preserved_when_unit_missing(self):
        result = reconcile_daemon_readiness(
            readiness("INSTALL_CANDIDATE_DRAFT"), daemon()
        )
        self.assertEqual(
            result["status"], "INSTALL_CANDIDATE_DRAFT"
        )

    def test_passive_candidate_contradicts_install_candidate(self):
        result = reconcile_daemon_readiness(
            readiness("INSTALL_CANDIDATE_DRAFT"),
            daemon("PASSIVE_CANDIDATE", "loaded", "active"),
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIn(
            "DAEMON_INSTALLATION_STATE_CONTRADICTION",
            result["reasons"],
        )

    def test_inputs_unchanged(self):
        base = readiness()
        observation = daemon()

        original_base = deepcopy(base)
        original_observation = deepcopy(observation)

        reconcile_daemon_readiness(base, observation)

        self.assertEqual(base, original_base)
        self.assertEqual(observation, original_observation)

    def test_reject_invalid_evidence_identity(self):
        changes = {
            "duplicate_request_id": (
                "evidence_request_id", "request-7"
            ),
            "missing_request_id": (
                "evidence_request_id", ""
            ),
            "wrong_target": ("target_id", "rocky10"),
            "wrong_source_status": (
                "source_status", "SERVICE_MASKED"
            ),
            "wrong_check": ("check", "application_runtime"),
        }

        for name, (key, value) in changes.items():
            with self.subTest(scenario=name):
                observation = daemon()
                observation[key] = value
                with self.assertRaises(ValueError):
                    reconcile_daemon_readiness(
                        readiness(), observation
                    )

    def test_reject_unsafe_daemon_policy(self):
        changes = {
            "execution_enabled": ("execution_allowed", True),
            "planning_disabled": ("planning_only", False),
            "active_probe": ("probe_attempted", True),
            "fabricated_health": ("daemon_responsive", True),
        }

        for name, (key, value) in changes.items():
            with self.subTest(scenario=name):
                observation = daemon()
                observation[key] = value
                with self.assertRaises(ValueError):
                    reconcile_daemon_readiness(
                        readiness(), observation
                    )

    def test_reject_contradictory_service_state(self):
        observation = daemon()
        observation["service_load_state"] = "loaded"

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

    def test_reject_fabricated_passive_candidate(self):
        observation = daemon(
            "PASSIVE_CANDIDATE", "loaded", "active"
        )
        observation["process_verified"] = False

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

    def test_reject_unverified_known_classification(self):
        observation = daemon(
            "DAEMON_UNVERIFIED", "loaded", "inactive"
        )

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

    def test_reject_invalid_base_readiness(self):
        cases = {
            "wrong_application": ("application_id", "docker_compose"),
            "invalid_status": ("status", "UNKNOWN"),
            "execution_enabled": ("execution_allowed", True),
            "fabricated_health": ("daemon_responsive", True),
        }

        for name, (key, value) in cases.items():
            with self.subTest(scenario=name):
                base = readiness()
                base[key] = value
                with self.assertRaises(ValueError):
                    reconcile_daemon_readiness(
                        base, daemon()
                    )

    def test_reject_duplicate_base_evidence_ids(self):
        base = readiness()
        base["evidence_request_ids"][
            "discovery.source_6"
        ] = "request-0"

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(base, daemon())


    def test_cli_absent_with_passive_daemon_candidate(self):
        """CLI absence does not invalidate an observed daemon."""
        base = readiness()
        observation = daemon(
            "PASSIVE_CANDIDATE", "loaded", "active"
        )

        result = reconcile_daemon_readiness(base, observation)

        self.assertEqual(result["runtime_status"], "CLI_ABSENT")
        self.assertEqual(
            result["daemon_preflight_status"],
            "PASSIVE_CANDIDATE",
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIsNone(result["daemon_responsive"])
        self.assertFalse(result["execution_allowed"])

    def test_cli_available_with_missing_daemon_unit(self):
        """Installed CLI does not prove the daemon is running."""
        base = readiness()
        base["runtime_status"] = "CLI_AVAILABLE"

        result = reconcile_daemon_readiness(base, daemon())

        self.assertEqual(
            result["runtime_status"], "CLI_AVAILABLE"
        )
        self.assertEqual(
            result["daemon_preflight_status"],
            "DAEMON_UNIT_NOT_FOUND",
        )
        self.assertIsNone(result["daemon_responsive"])
        self.assertFalse(result["execution_allowed"])

    def test_reject_malformed_daemon_reasons(self):
        """Reject malformed classified evidence."""
        observation = daemon()
        observation["reasons"] = "DOCKER_SERVICE_UNIT_NOT_FOUND"

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

    def test_reject_invalid_socket_observation(self):
        """An absent socket cannot have an observed listener."""
        observation = daemon()
        observation["socket_present"] = False
        observation["socket_listener_state"] = "present"

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

    def test_reject_nonboolean_process_verification(self):
        """Process verification must be a boolean."""
        observation = daemon()
        observation["process_verified"] = "false"

        with self.assertRaises(ValueError):
            reconcile_daemon_readiness(
                readiness(), observation
            )

if __name__ == "__main__":
    unittest.main()
