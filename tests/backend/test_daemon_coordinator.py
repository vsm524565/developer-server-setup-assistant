
"""Regression tests for nine-source passive Docker readiness coordination."""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from bridge.applications.readiness.daemon_coordinator import (
    assess_daemon_aware_readiness,
)


def runtime_readiness(status="REVIEW_REQUIRED"):
    """Create an eight-source Docker readiness result."""
    ids = {
        f"discovery.source_{i}": f"request-{i}"
        for i in range(7)
    }
    ids["discovery.application_runtime"] = "request-7"

    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": status,
        "runtime_status": "CLI_ABSENT",
        "reasons": [],
        "evidence_request_ids": ids,
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }


def daemon_result():
    """Create a valid classified missing-unit observation."""
    return {
        "schema_version": "1.0",
        "check": "docker_daemon_preflight_classification",
        "target_id": "ubuntu24",
        "evidence_request_id": "request-8",
        "status": "DAEMON_UNIT_NOT_FOUND",
        "source_status": "SERVICE_NOT_FOUND",
        "service_load_state": "not-found",
        "service_state": "inactive",
        "process_verified": False,
        "socket_present": False,
        "socket_listener_state": "absent",
        "daemon_responsive": None,
        "probe_attempted": False,
        "reasons": ["DOCKER_SERVICE_UNIT_NOT_FOUND"],
        "planning_only": True,
        "execution_allowed": False,
    }


class TestDaemonCoordinator(unittest.TestCase):
    """Check nine-source orchestration and failure propagation."""

    def setUp(self):
        self.backend = Mock()
        self.backend.discover_for_planning.return_value = {
            "synthetic": "daemon-planning-result",
        }

    def assess(self, readiness=None, daemon=None):
        """Run the coordinator with mocked upstream classifiers."""
        if readiness is None:
            readiness = runtime_readiness()
        if daemon is None:
            daemon = daemon_result()

        with (
            patch(
                "bridge.applications.readiness.daemon_coordinator."
                "assess_runtime_aware_readiness",
                return_value=deepcopy(readiness),
            ) as runtime_mock,
            patch(
                "bridge.applications.readiness.daemon_coordinator."
                "classify_docker_daemon_preflight_evidence",
                return_value=deepcopy(daemon),
            ) as daemon_mock,
        ):
            result = assess_daemon_aware_readiness(
                self.backend,
                "ubuntu24",
                "docker",
                max_age_seconds=30,
            )

        return result, runtime_mock, daemon_mock

    def test_valid_nine_source_reconciliation(self):
        result, runtime_mock, daemon_mock = self.assess()

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(
            result["daemon_preflight_status"],
            "DAEMON_UNIT_NOT_FOUND",
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

        runtime_mock.assert_called_once_with(
            self.backend,
            "ubuntu24",
            "docker",
            max_age_seconds=30,
        )

        self.backend.discover_for_planning.assert_called_once_with(
            "discovery.docker_daemon_preflight",
            "ubuntu24",
            parameters={},
            max_age_seconds=30,
        )

        daemon_mock.assert_called_once_with(
            self.backend.discover_for_planning.return_value,
            expected_target_id="ubuntu24",
            max_age_seconds=30,
        )

    def test_existing_blocker_preserved(self):
        result, _, _ = self.assess(
            readiness=runtime_readiness("BLOCKED")
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_ready_installed_downgraded(self):
        result, _, _ = self.assess(
            readiness=runtime_readiness("READY_INSTALLED")
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_reject_invalid_inputs_before_discovery(self):
        cases = (
            ("", "docker", 30),
            ("ubuntu24", "docker_compose", 30),
            ("ubuntu24", "nginx", 30),
            ("ubuntu24", "docker", 0),
            ("ubuntu24", "docker", 301),
            ("ubuntu24", "docker", True),
        )

        for target, application, age in cases:
            with self.subTest(
                target=target, application=application, age=age
            ):
                backend = Mock()

                with self.assertRaises(ValueError):
                    assess_daemon_aware_readiness(
                        backend,
                        target,
                        application,
                        max_age_seconds=age,
                    )

                backend.discover_for_planning.assert_not_called()

    def test_reject_invalid_base_readiness(self):
        cases = {
            "wrong_target": ("target_id", "rocky10"),
            "wrong_application": ("application_id", "docker_compose"),
            "execution_enabled": ("execution_allowed", True),
            "planning_disabled": ("planning_only", False),
            "fabricated_health": ("daemon_responsive", True),
        }

        for name, (key, value) in cases.items():
            with self.subTest(scenario=name):
                data = runtime_readiness()
                data[key] = value

                with self.assertRaises(RuntimeError):
                    self.assess(readiness=data)

    def test_reject_invalid_base_evidence_ids(self):
        cases = []

        duplicate = runtime_readiness()
        duplicate["evidence_request_ids"][
            "discovery.source_6"
        ] = "request-0"
        cases.append(("duplicate", duplicate))

        missing = runtime_readiness()
        del missing["evidence_request_ids"][
            "discovery.application_runtime"
        ]
        cases.append(("missing", missing))

        extra = runtime_readiness()
        extra["evidence_request_ids"][
            "discovery.docker_daemon_preflight"
        ] = "request-8"
        cases.append(("preexisting_daemon", extra))

        for name, data in cases:
            with self.subTest(scenario=name):
                with self.assertRaises(RuntimeError):
                    self.assess(readiness=data)

    def test_reject_duplicate_daemon_request_id(self):
        data = daemon_result()
        data["evidence_request_id"] = "request-7"

        with self.assertRaises(ValueError):
            self.assess(daemon=data)

    def test_reject_cross_target_daemon_evidence(self):
        data = daemon_result()
        data["target_id"] = "rocky10"

        with self.assertRaises(ValueError):
            self.assess(daemon=data)

    def test_reject_fabricated_daemon_health(self):
        data = daemon_result()
        data["daemon_responsive"] = True

        with self.assertRaises(ValueError):
            self.assess(daemon=data)

    def test_reject_active_probe(self):
        data = daemon_result()
        data["probe_attempted"] = True

        with self.assertRaises(ValueError):
            self.assess(daemon=data)

    def test_runtime_failure_stops_daemon_discovery(self):
        with patch(
            "bridge.applications.readiness.daemon_coordinator."
            "assess_runtime_aware_readiness",
            side_effect=ValueError("Runtime evidence rejected"),
        ):
            with self.assertRaisesRegex(ValueError, "Runtime"):
                assess_daemon_aware_readiness(
                    self.backend, "ubuntu24", "docker"
                )

        self.backend.discover_for_planning.assert_not_called()

    def test_daemon_evidence_failure_propagates(self):
        with (
            patch(
                "bridge.applications.readiness.daemon_coordinator."
                "assess_runtime_aware_readiness",
                return_value=runtime_readiness(),
            ),
            patch(
                "bridge.applications.readiness.daemon_coordinator."
                "classify_docker_daemon_preflight_evidence",
                side_effect=ValueError("Stale daemon evidence"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Stale"):
                assess_daemon_aware_readiness(
                    self.backend, "ubuntu24", "docker"
                )

    def test_invalid_base_prevents_daemon_discovery(self):
        data = runtime_readiness()
        data["execution_allowed"] = True

        with patch(
            "bridge.applications.readiness.daemon_coordinator."
            "assess_runtime_aware_readiness",
            return_value=data,
        ):
            with self.assertRaises(RuntimeError):
                assess_daemon_aware_readiness(
                    self.backend, "ubuntu24", "docker"
                )

        self.backend.discover_for_planning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
