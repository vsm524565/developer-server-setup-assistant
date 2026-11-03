
"""Regression tests for application readiness evidence coordination."""

import unittest
from unittest.mock import Mock, patch

from bridge.applications.readiness.coordinator import (
    _validated_application_evidence,
    assess_application_readiness,
)


def make_evidence(operation, request_id):
    """Create a synthetic accepted discovery envelope."""
    return {
        "gate": {
            "status": "ACCEPTED",
            "planning_allowed": True,
            "execution_allowed": False,
        },
        "evidence": {
            "operation": operation,
            "target_id": "ubuntu24",
            "parameters": {"application_id": "git"},
            "request_id": request_id,
            "execution_allowed": False,
        },
        "execution_allowed": False,
    }


def make_plan():
    """Create a synthetic non-executable draft plan."""
    return {
        "target_id": "ubuntu24",
        "application_id": "git",
        "status": "REVIEW_REQUIRED",
        "recipe_id": "git-apt-os-repository",
        "dependencies": [],
        "reasons": ["RECIPE_POLICY_DRAFT"],
        "evidence_request_ids": {
            "discovery.system": "plan-system",
            "discovery.package_manager": "plan-manager",
        },
        "planning_only": True,
        "execution_allowed": False,
    }


class TestReadinessCoordinator(unittest.TestCase):

    def test_accepted_live_evidence(self):
        operation = "discovery.application_state"
        backend = Mock()
        backend.discover_for_planning.return_value = (
            make_evidence(operation, "state-123")
        )

        with patch(
            "bridge.applications.readiness.coordinator.evaluate_evidence_gate",
            return_value={"status": "ACCEPTED"},
        ):
            result = _validated_application_evidence(
                backend, operation, "ubuntu24", "git", 30
            )

        self.assertEqual(
            result["evidence"]["request_id"], "state-123"
        )

    def test_wrong_target_is_rejected(self):
        operation = "discovery.application_state"
        backend = Mock()
        response = make_evidence(operation, "state-123")
        response["evidence"]["target_id"] = "alma10"
        backend.discover_for_planning.return_value = response

        with self.assertRaises(RuntimeError):
            _validated_application_evidence(
                backend, operation, "ubuntu24", "git", 30
            )

    def test_wrong_application_is_rejected(self):
        operation = "discovery.application_inventory"
        backend = Mock()
        response = make_evidence(operation, "inventory-123")
        response["evidence"]["parameters"] = {
            "application_id": "nginx"
        }
        backend.discover_for_planning.return_value = response

        with self.assertRaises(RuntimeError):
            _validated_application_evidence(
                backend, operation, "ubuntu24", "git", 30
            )

    def test_independent_gate_rejection(self):
        operation = "discovery.application_state"
        backend = Mock()
        backend.discover_for_planning.return_value = (
            make_evidence(operation, "state-123")
        )

        with patch(
            "bridge.applications.readiness.coordinator.evaluate_evidence_gate",
            return_value={"status": "REJECTED"},
        ):
            with self.assertRaises(RuntimeError):
                _validated_application_evidence(
                    backend, operation, "ubuntu24", "git", 30
                )

    def test_invalid_freshness_window(self):
        with self.assertRaises(ValueError):
            assess_application_readiness(
                Mock(), "ubuntu24", "git", max_age_seconds=0
            )

    @patch(
        "bridge.applications.readiness.coordinator."
        "normalize_application_inventory_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "classify_application_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "_validated_application_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "build_draft_installation_plan"
    )
    def test_duplicate_request_ids_rejected(
        self, plan_mock, evidence_mock, state_mock, inventory_mock
    ):
        plan_mock.return_value = make_plan()

        evidence_mock.side_effect = [
            make_evidence("discovery.application_state", "same-id"),
            make_evidence("discovery.application_inventory", "same-id"),
        ]

        state_mock.return_value = {
            "evidence_request_id": "same-id",
        }
        inventory_mock.return_value = {
            "evidence_request_id": "same-id",
        }

        with self.assertRaises(RuntimeError):
            assess_application_readiness(
                Mock(), "ubuntu24", "git"
            )

    @patch(
        "bridge.applications.readiness.coordinator."
        "normalize_application_inventory_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "classify_application_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "_validated_application_evidence"
    )
    @patch(
        "bridge.applications.readiness.coordinator."
        "build_draft_installation_plan"
    )
    def test_successful_readiness_coordination(
        self, plan_mock, evidence_mock, state_mock, inventory_mock
    ):
        plan_mock.return_value = make_plan()

        evidence_mock.side_effect = [
            make_evidence("discovery.application_state", "state-id"),
            make_evidence("discovery.application_inventory", "inventory-id"),
        ]

        state_mock.return_value = {
            "target_id": "ubuntu24",
            "application_id": "git",
            "state": "INSTALLED_HEALTHY",
            "reasons": [],
            "evidence_request_id": "state-id",
            "planning_only": True,
            "execution_allowed": False,
        }

        inventory_mock.return_value = {
            "target_id": "ubuntu24",
            "application_id": "git",
            "inventory_status": "IDENTIFIED",
            "reasons": [],
            "evidence_request_id": "inventory-id",
            "planning_only": True,
            "execution_allowed": False,
        }

        result = assess_application_readiness(
            Mock(), "ubuntu24", "git"
        )

        self.assertEqual(result["status"], "READY_INSTALLED")
        self.assertEqual(len(result["evidence_request_ids"]), 4)
        self.assertTrue(result["planning_only"])
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
