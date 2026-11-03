"""Regression tests for runtime-aware readiness coordination."""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from bridge.applications.readiness.runtime_coordinator import (
    assess_runtime_aware_readiness,
)


def base_readiness():
    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": "REVIEW_REQUIRED",
        "reasons": [],
        "planning_only": True,
        "execution_allowed": False,
        "evidence_request_ids": {
            f"discovery.source_{i}": f"request-{i}"
            for i in range(7)
        },
    }


def runtime_result():
    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": "CLI_ABSENT",
        "reasons": ["DOCKER_CLI_NOT_FOUND"],
        "service_state": "inactive",
        "socket_state": "inactive",
        "plugin_state": "not_applicable",
        "daemon_responsive": None,
        "evidence_request_id": "request-7",
        "planning_only": True,
        "execution_allowed": False,
    }


class TestRuntimeReadinessCoordinator(unittest.TestCase):

    def setUp(self):
        self.backend = Mock()
        self.backend.discover_for_planning.return_value = {
            "evidence": "mock-runtime-evidence",
        }

    def assess(self, readiness=None, runtime=None):
        if readiness is None:
            readiness = base_readiness()
        if runtime is None:
            runtime = runtime_result()

        with (
            patch(
                "bridge.applications.readiness.runtime_coordinator."
                "assess_application_readiness",
                return_value=deepcopy(readiness),
            ),
            patch(
                "bridge.applications.readiness.runtime_coordinator."
                "classify_application_runtime_evidence",
                return_value=deepcopy(runtime),
            ),
        ):
            return assess_runtime_aware_readiness(
                self.backend, "ubuntu24", "docker"
            )

    def test_valid_reconciliation(self):
        result = self.assess()
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["runtime_status"], "CLI_ABSENT")
        self.assertEqual(len(result["evidence_request_ids"]), 8)
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(result["planning_only"])

    def test_invalid_application_rejected_before_discovery(self):
        with self.assertRaises(ValueError):
            assess_runtime_aware_readiness(
                self.backend, "ubuntu24", "nginx"
            )
        self.backend.discover_for_planning.assert_not_called()

    def test_invalid_freshness_rejected(self):
        with self.assertRaises(ValueError):
            assess_runtime_aware_readiness(
                self.backend,
                "ubuntu24",
                "docker",
                max_age_seconds=0,
            )

    def test_mismatched_base_target_rejected(self):
        data = base_readiness()
        data["target_id"] = "rocky10"
        with self.assertRaises(RuntimeError):
            self.assess(readiness=data)

    def test_duplicate_base_request_ids_rejected(self):
        data = base_readiness()
        data["evidence_request_ids"]["discovery.source_6"] = "request-0"
        with self.assertRaises(RuntimeError):
            self.assess(readiness=data)

    def test_duplicate_runtime_request_id_rejected(self):
        data = runtime_result()
        data["evidence_request_id"] = "request-0"
        with self.assertRaises(ValueError):
            self.assess(runtime=data)

    def test_runtime_failure_propagates(self):
        with (
            patch(
                "bridge.applications.readiness.runtime_coordinator."
                "assess_application_readiness",
                return_value=base_readiness(),
            ),
            patch(
                "bridge.applications.readiness.runtime_coordinator."
                "classify_application_runtime_evidence",
                side_effect=ValueError("Stale runtime evidence"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Stale"):
                assess_runtime_aware_readiness(
                    self.backend, "ubuntu24", "docker"
                )


if __name__ == "__main__":
    unittest.main()
