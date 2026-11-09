
"""Unit tests for read-only DSSA workflow coordination."""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from bridge.workflows.coordinator import assess_application_workflow


def valid_request():
    """Build an inventory-managed Docker assessment request."""
    return {
        "schema_version": "1.0",
        "workflow": "readiness_assessment",
        "target": {"kind": "inventory", "id": "ubuntu24"},
        "application_id": "docker",
        "max_age_seconds": 120,
    }


def valid_readiness():
    """Build a synthetic readiness decision for boundary tests."""
    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": "REVIEW_REQUIRED",
        "runtime_status": "CLI_ABSENT",
        "daemon_preflight_status": "DAEMON_UNIT_NOT_FOUND",
        "daemon_responsive": None,
        "reasons": ["DOCKER_DAEMON_UNIT_NOT_FOUND"],
        "evidence_request_ids": {
            f"operation.{i}": f"request-{i}"
            for i in range(9)
        },
        "planning_only": True,
        "execution_allowed": False,
    }


def valid_explanation(readiness):
    """Build a matching synthetic explanation for boundary tests."""
    return {
        "schema_version": "1.0",
        "check": "docker_readiness_explanation",
        "target_id": readiness["target_id"],
        "application_id": readiness["application_id"],
        "status": readiness["status"],
        "runtime_status": readiness["runtime_status"],
        "daemon_preflight_status": readiness["daemon_preflight_status"],
        "daemon_responsive": None,
        "source_reasons": list(readiness["reasons"]),
        "evidence_request_ids": deepcopy(
            readiness["evidence_request_ids"]
        ),
        "findings": [],
        "planning_only": True,
        "execution_allowed": False,
    }


class TestWorkflowCoordinator(unittest.TestCase):
    """Verify orchestration, identity binding and execution denial."""

    def setUp(self):
        self.backend = Mock()
        self.request = valid_request()
        self.readiness = valid_readiness()
        self.explanation = valid_explanation(self.readiness)

        coordinator_patch = patch(
            "bridge.workflows.coordinator.assess_daemon_aware_readiness"
        )
        explanation_patch = patch(
            "bridge.workflows.coordinator.explain_docker_readiness"
        )
        validator_patch = patch(
            "bridge.workflows.coordinator.validate_explanation"
        )

        self.assess = coordinator_patch.start()
        self.explain = explanation_patch.start()
        self.validate = validator_patch.start()

        self.addCleanup(coordinator_patch.stop)
        self.addCleanup(explanation_patch.stop)
        self.addCleanup(validator_patch.stop)

        self.assess.return_value = self.readiness
        self.explain.return_value = self.explanation

    def test_successful_workflow(self):
        result = assess_application_workflow(
            self.backend, self.request
        )

        self.assertEqual(
            result["check"], "application_workflow_result"
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["target"]["id"], "ubuntu24")
        self.assertTrue(result["planning_only"])
        self.assertFalse(result["execution_allowed"])

        self.assess.assert_called_once_with(
            self.backend,
            "ubuntu24",
            "docker",
            max_age_seconds=120,
        )
        self.explain.assert_called_once_with(self.readiness)
        self.validate.assert_called_once_with(self.explanation)

    def test_invalid_request_rejected_before_discovery(self):
        for change in (
            {"application_id": "nginx"},
            {"workflow": "installation"},
            {"max_age_seconds": True},
            {"password": "DO_NOT_ACCEPT"},
        ):
            with self.subTest(change=change):
                request = valid_request()
                request.update(change)

                with self.assertRaises(ValueError):
                    assess_application_workflow(
                        self.backend, request
                    )

        self.assess.assert_not_called()

    def test_invalid_readiness_type(self):
        for value in (None, [], "invalid", 42):
            with self.subTest(value=value):
                self.assess.return_value = value

                with self.assertRaises(RuntimeError):
                    assess_application_workflow(
                        self.backend, self.request
                    )

    def test_readiness_identity_mismatch(self):
        for field, value in (
            ("target_id", "rocky10"),
            ("application_id", "nginx"),
        ):
            with self.subTest(field=field):
                self.assess.return_value = deepcopy(
                    self.readiness
                )
                self.assess.return_value[field] = value

                with self.assertRaises(RuntimeError):
                    assess_application_workflow(
                        self.backend, self.request
                    )

    def test_readiness_execution_policy_rejected(self):
        for field, value in (
            ("execution_allowed", True),
            ("planning_only", False),
            ("daemon_responsive", True),
        ):
            with self.subTest(field=field):
                self.assess.return_value = deepcopy(
                    self.readiness
                )
                self.assess.return_value[field] = value

                with self.assertRaises(RuntimeError):
                    assess_application_workflow(
                        self.backend, self.request
                    )

    def test_explanation_identity_mismatch(self):
        for field, value in (
            ("target_id", "alma10"),
            ("application_id", "nginx"),
        ):
            with self.subTest(field=field):
                self.explain.return_value = deepcopy(
                    self.explanation
                )
                self.explain.return_value[field] = value

                with self.assertRaises(RuntimeError):
                    assess_application_workflow(
                        self.backend, self.request
                    )

    def test_explanation_contradictions_rejected(self):
        modifications = (
            ("status", "BLOCKED"),
            ("runtime_status", "UNVERIFIED"),
            ("daemon_preflight_status", "DAEMON_FAILED"),
            ("source_reasons", ["DIFFERENT_REASON"]),
            ("evidence_request_ids", {"changed": "request-1"}),
        )

        for field, value in modifications:
            with self.subTest(field=field):
                self.explain.return_value = deepcopy(
                    self.explanation
                )
                self.explain.return_value[field] = value

                with self.assertRaises(RuntimeError):
                    assess_application_workflow(
                        self.backend, self.request
                    )

    def test_explanation_validation_is_required(self):
        self.validate.side_effect = ValueError(
            "Invalid explanation evidence"
        )

        with self.assertRaises(ValueError):
            assess_application_workflow(
                self.backend, self.request
            )

        self.validate.assert_called_once()

    def test_backend_failure_is_not_converted_to_success(self):
        self.assess.side_effect = RuntimeError(
            "Backend discovery rejected"
        )

        with self.assertRaises(RuntimeError):
            assess_application_workflow(
                self.backend, self.request
            )

        self.explain.assert_not_called()
        self.validate.assert_not_called()

    def test_result_does_not_share_mutable_data(self):
        result = assess_application_workflow(
            self.backend, self.request
        )

        self.assertIsNot(
            result["target"], self.request["target"]
        )
        self.assertIsNot(
            result["result"], self.explanation
        )

        result["target"]["id"] = "modified"
        result["result"]["source_reasons"].append("MODIFIED")

        self.assertEqual(
            self.request["target"]["id"], "ubuntu24"
        )
        self.assertNotIn(
            "MODIFIED", self.explanation["source_reasons"]
        )

    def test_setup_components_is_never_called(self):
        assess_application_workflow(
            self.backend, self.request
        )

        self.backend.setup_components.assert_not_called()

    def test_unregistered_capability_rejected_before_discovery(self):
        """Reject workflows absent from the capability registry."""
        with patch(
            "bridge.workflows.coordinator.build_workflow_registry"
        ) as builder:
            builder.return_value.get.side_effect = ValueError(
                "Unsupported workflow capability."
            )

            with self.assertRaises(ValueError):
                assess_application_workflow(
                    self.backend,
                    self.request,
                )

            builder.return_value.get.assert_called_once_with(
                "docker",
                "readiness_assessment",
                "inventory",
            )

        self.assess.assert_not_called()
        self.explain.assert_not_called()
        self.validate.assert_not_called()

    def test_unsafe_capability_rejected_before_discovery(self):
        """Reject execution authority even from a registry response."""
        for field, value in (
            ("execution_allowed", True),
            ("planning_only", False),
            ("application_id", "nginx"),
            ("workflow", "installation"),
            ("target_kind", "ssh"),
        ):
            with self.subTest(field=field):
                capability = {
                    "application_id": "docker",
                    "workflow": "readiness_assessment",
                    "target_kind": "inventory",
                    "planning_only": True,
                    "execution_allowed": False,
                }
                capability[field] = value

                with patch(
                    "bridge.workflows.coordinator.build_workflow_registry"
                ) as builder:
                    builder.return_value.get.return_value = capability

                    with self.assertRaises(RuntimeError):
                        assess_application_workflow(
                            self.backend,
                            self.request,
                        )

        self.assess.assert_not_called()
        self.explain.assert_not_called()
        self.validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
