
"""Regression tests for structured Docker readiness explanations."""

import unittest
from copy import deepcopy
from itertools import product

from bridge.applications.readiness.daemon_reconciliation import (
    PREFLIGHT_REASONS,
)
from bridge.applications.readiness.readiness_explanation import (
    DAEMON_FINDINGS,
    RUNTIME_FINDINGS,
    explain_docker_readiness,
)


def readiness(
    status="REVIEW_REQUIRED",
    runtime="CLI_ABSENT",
    daemon="DAEMON_UNIT_NOT_FOUND",
):
    """Create a nine-source daemon-aware Docker readiness result."""
    ids = {
        f"discovery.source_{index}": f"request-{index}"
        for index in range(7)
    }
    ids["discovery.application_runtime"] = "request-7"
    ids["discovery.docker_daemon_preflight"] = "request-8"

    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": status,
        "runtime_status": runtime,
        "daemon_preflight_status": daemon,
        "reasons": [PREFLIGHT_REASONS[daemon]],
        "evidence_request_ids": ids,
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }


class TestReadinessExplanation(unittest.TestCase):
    """Verify output structure, source identity and safety."""

    def test_all_classification_combinations(self):
        for runtime, daemon in product(
            RUNTIME_FINDINGS, DAEMON_FINDINGS
        ):
            with self.subTest(runtime=runtime, daemon=daemon):
                explanation = explain_docker_readiness(
                    readiness(runtime=runtime, daemon=daemon)
                )

                self.assertEqual(
                    explanation["runtime_status"], runtime
                )
                self.assertEqual(
                    explanation["daemon_preflight_status"], daemon
                )
                self.assertEqual(len(explanation["findings"]), 3)
                self.assertEqual(
                    len(explanation["evidence_request_ids"]), 9
                )
                self.assertIsNone(
                    explanation["daemon_responsive"]
                )
                self.assertIs(
                    explanation["execution_allowed"], False
                )

    def test_findings_have_stable_structure(self):
        result = explain_docker_readiness(readiness())

        for finding in result["findings"]:
            self.assertEqual(
                set(finding),
                {
                    "category",
                    "code",
                    "title",
                    "description",
                    "investigation",
                    "execution_allowed",
                },
            )
            for key in (
                "category", "code", "title",
                "description", "investigation",
            ):
                self.assertIsInstance(finding[key], str)
                self.assertTrue(finding[key])
            self.assertIs(finding["execution_allowed"], False)

    def test_blocked_readiness_adds_finding(self):
        result = explain_docker_readiness(
            readiness(status="BLOCKED")
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["findings"]), 4)
        self.assertIn(
            "EXISTING_READINESS_BLOCKER",
            [item["code"] for item in result["findings"]],
        )

    def test_supported_readiness_statuses(self):
        for status in (
            "READY_INSTALLED",
            "INSTALL_CANDIDATE_DRAFT",
            "REVIEW_REQUIRED",
            "BLOCKED",
        ):
            with self.subTest(status=status):
                result = explain_docker_readiness(
                    readiness(status=status)
                )
                self.assertEqual(result["status"], status)
                self.assertFalse(result["execution_allowed"])

    def test_input_is_not_modified(self):
        data = readiness()
        original = deepcopy(data)

        result = explain_docker_readiness(data)

        self.assertEqual(data, original)
        self.assertIsNot(
            result["evidence_request_ids"],
            data["evidence_request_ids"],
        )
        self.assertIsNot(
            result["source_reasons"], data["reasons"]
        )

    def test_deterministic_output(self):
        data = readiness()

        first = explain_docker_readiness(data)
        second = explain_docker_readiness(deepcopy(data))

        self.assertEqual(first, second)

    def test_reject_invalid_classifications(self):
        cases = {
            "wrong_application": ("application_id", "docker_compose"),
            "invalid_readiness": ("status", "HEALTHY"),
            "invalid_runtime": ("runtime_status", "RUNNING"),
            "invalid_daemon": (
                "daemon_preflight_status", "DOCKER_HEALTHY"
            ),
            "wrong_target": ("target_id", ""),
        }

        for name, (field, value) in cases.items():
            with self.subTest(scenario=name):
                data = readiness()
                data[field] = value
                with self.assertRaises(ValueError):
                    explain_docker_readiness(data)

    def test_reject_unsafe_policy(self):
        cases = {
            "execution_enabled": ("execution_allowed", True),
            "planning_disabled": ("planning_only", False),
            "fabricated_health": ("daemon_responsive", True),
        }

        for name, (field, value) in cases.items():
            with self.subTest(scenario=name):
                data = readiness()
                data[field] = value
                with self.assertRaises(ValueError):
                    explain_docker_readiness(data)

    def test_reject_missing_daemon_reason(self):
        data = readiness()
        data["reasons"] = ["DOCKER_CLI_ABSENT"]

        with self.assertRaises(ValueError):
            explain_docker_readiness(data)

    def test_reject_invalid_reasons(self):
        for reasons in (None, "reason", [None], [42]):
            with self.subTest(reasons=reasons):
                data = readiness()
                data["reasons"] = reasons
                with self.assertRaises(ValueError):
                    explain_docker_readiness(data)

    def test_reject_duplicate_evidence_ids(self):
        data = readiness()
        data["evidence_request_ids"][
            "discovery.docker_daemon_preflight"
        ] = "request-7"

        with self.assertRaises(ValueError):
            explain_docker_readiness(data)

    def test_reject_missing_evidence_source(self):
        data = readiness()
        del data["evidence_request_ids"][
            "discovery.application_runtime"
        ]

        with self.assertRaises(ValueError):
            explain_docker_readiness(data)

    def test_reject_invalid_evidence_identity(self):
        data = readiness()
        data["evidence_request_ids"][
            "discovery.docker_daemon_preflight"
        ] = ""

        with self.assertRaises(ValueError):
            explain_docker_readiness(data)

    def test_reject_non_dictionary_input(self):
        for value in (None, [], "docker", 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    explain_docker_readiness(value)


if __name__ == "__main__":
    unittest.main()
