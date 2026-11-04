
"""Tests for Docker runtime-aware readiness reconciliation."""

from copy import deepcopy
import unittest

from bridge.applications.readiness.runtime_reconciliation import (
    reconcile_runtime_readiness,
)


def readiness(status="REVIEW_REQUIRED"):
    """Create a planning-only Compose readiness decision."""

    return {
        "target_id": "ubuntu24",
        "application_id": "docker_compose",
        "status": status,
        "reasons": [],
        "evidence_request_ids": {
            "discovery.system": "request-plan-1",
        },
        "planning_only": True,
        "execution_allowed": False,
    }


def runtime(status="PLUGIN_ABSENT"):
    """Create a classified Compose runtime observation."""

    return {
        "target_id": "ubuntu24",
        "application_id": "docker_compose",
        "status": status,
        "reasons": ["COMPOSE_COMMAND_UNAVAILABLE"],
        "service_state": "not_applicable",
        "socket_state": "not_applicable",
        "plugin_state": "absent",
        "daemon_responsive": None,
        "evidence_request_id": "request-runtime-2",
        "planning_only": True,
        "execution_allowed": False,
    }


class TestRuntimeReconciliation(unittest.TestCase):

    def test_compose_absent_preserves_review(self):
        result = reconcile_runtime_readiness(
            readiness(),
            runtime(),
        )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["runtime_status"], "PLUGIN_ABSENT")
        self.assertIn("COMPOSE_PLUGIN_ABSENT", result["reasons"])
        self.assertFalse(result["execution_allowed"])

    def test_compose_absent_downgrades_ready(self):
        result = reconcile_runtime_readiness(
            readiness("READY_INSTALLED"),
            runtime(),
        )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIn(
            "RUNTIME_READINESS_CONTRADICTION",
            result["reasons"],
        )

    def test_existing_blocker_is_preserved(self):
        result = reconcile_runtime_readiness(
            readiness("BLOCKED"),
            runtime(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_duplicate_evidence_identity_rejected(self):
        observation = runtime()
        observation["evidence_request_id"] = "request-plan-1"

        with self.assertRaises(ValueError):
            reconcile_runtime_readiness(
                readiness(),
                observation,
            )

    def test_inputs_remain_unchanged(self):
        base = readiness()
        observation = runtime()

        original_base = deepcopy(base)
        original_runtime = deepcopy(observation)

        reconcile_runtime_readiness(base, observation)

        self.assertEqual(base, original_base)
        self.assertEqual(observation, original_runtime)


if __name__ == "__main__":
    unittest.main()
