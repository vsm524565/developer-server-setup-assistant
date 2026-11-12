
"""Tests for the read-only Docker Compose explanation contract."""

import json
import unittest
from copy import deepcopy

from bridge.applications.readiness.compose_explanation import (
    explain_compose_readiness,
)
from bridge.applications.readiness.compose_report import (
    render_compose_report,
    validate_compose_explanation,
)


def sample_readiness(
    status="REVIEW_REQUIRED",
    runtime="PLUGIN_ABSENT",
):
    """Build independent synthetic eight-source Compose readiness."""

    runtime_reason = {
        "PLUGIN_AVAILABLE": "COMPOSE_PLUGIN_VERIFIED",
        "PLUGIN_ABSENT": "COMPOSE_PLUGIN_ABSENT",
        "UNVERIFIED": "COMPOSE_RUNTIME_UNVERIFIED",
    }[runtime]

    identities = {
        f"discovery.test_source_{index}": f"request-{index}"
        for index in range(1, 8)
    }
    identities["discovery.application_runtime"] = "request-8"

    return {
        "target_id": "ubuntu24",
        "application_id": "docker_compose",
        "status": status,
        "runtime_status": runtime,
        "reasons": [runtime_reason],
        "evidence_request_ids": identities,
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }


class TestComposeReport(unittest.TestCase):
    """Verify Compose reporting and evidence safety."""

    def test_valid_explanation(self):
        report = explain_compose_readiness(sample_readiness())

        self.assertEqual(
            report["check"],
            "docker_compose_readiness_explanation",
        )
        self.assertEqual(len(report["evidence_request_ids"]), 8)
        self.assertFalse(report["execution_allowed"])
        validate_compose_explanation(report)

    def test_all_runtime_classifications(self):
        for runtime in (
            "PLUGIN_AVAILABLE",
            "PLUGIN_ABSENT",
            "UNVERIFIED",
        ):
            with self.subTest(runtime=runtime):
                report = explain_compose_readiness(
                    sample_readiness(runtime=runtime)
                )
                validate_compose_explanation(report)
                self.assertEqual(report["runtime_status"], runtime)

    def test_all_readiness_classifications(self):
        for status in (
            "READY_INSTALLED",
            "INSTALL_CANDIDATE_DRAFT",
            "REVIEW_REQUIRED",
            "BLOCKED",
        ):
            with self.subTest(status=status):
                report = explain_compose_readiness(
                    sample_readiness(status=status)
                )
                validate_compose_explanation(report)

    def test_blocker_has_readiness_finding(self):
        report = explain_compose_readiness(
            sample_readiness(status="BLOCKED")
        )
        self.assertEqual(
            [item["category"] for item in report["findings"]],
            ["runtime", "health", "readiness"],
        )

    def test_rejects_unsafe_readiness(self):
        readiness = sample_readiness()
        readiness["execution_allowed"] = True

        with self.assertRaises(ValueError):
            explain_compose_readiness(readiness)

    def test_rejects_duplicate_evidence_id(self):
        readiness = sample_readiness()
        readiness["evidence_request_ids"][
            "discovery.application_runtime"
        ] = "request-1"

        with self.assertRaises(ValueError):
            explain_compose_readiness(readiness)

    def test_rejects_daemon_preflight_evidence(self):
        readiness = sample_readiness()
        del readiness["evidence_request_ids"]["discovery.test_source_7"]
        readiness["evidence_request_ids"][
            "discovery.docker_daemon_preflight"
        ] = "request-7"

        with self.assertRaises(ValueError):
            explain_compose_readiness(readiness)

    def test_rejects_missing_runtime_reason(self):
        readiness = sample_readiness()
        readiness["reasons"] = []

        with self.assertRaises(ValueError):
            explain_compose_readiness(readiness)

    def test_explanation_does_not_mutate_input(self):
        readiness = sample_readiness()
        original = deepcopy(readiness)

        report = explain_compose_readiness(readiness)
        report["source_reasons"].append("LOCAL_MUTATION")
        report["evidence_request_ids"].clear()

        self.assertEqual(readiness, original)

    def test_text_and_json_rendering(self):
        report = explain_compose_readiness(sample_readiness())

        text = render_compose_report(report, "text")
        encoded = render_compose_report(report, "json")

        self.assertIn("DOCKER COMPOSE READINESS", text)
        self.assertIn("Execution: DISABLED", text)
        self.assertIn("Distinct identities: 8", text)
        self.assertEqual(json.loads(encoded), report)

    def test_rendering_is_deterministic(self):
        report = explain_compose_readiness(sample_readiness())
        original = deepcopy(report)

        for fmt in ("text", "json"):
            self.assertEqual(
                render_compose_report(report, fmt),
                render_compose_report(report, fmt),
            )

        self.assertEqual(report, original)

    def test_rejects_invalid_report_findings(self):
        report = explain_compose_readiness(sample_readiness())
        report["findings"][0]["code"] = "FALSE_VERIFICATION"

        with self.assertRaises(ValueError):
            validate_compose_explanation(report)

    def test_rejects_execution_enabled_report(self):
        report = explain_compose_readiness(sample_readiness())
        report["execution_allowed"] = True

        with self.assertRaises(ValueError):
            render_compose_report(report, "json")

    def test_rejects_unsupported_format(self):
        report = explain_compose_readiness(sample_readiness())

        with self.assertRaises(ValueError):
            render_compose_report(report, "xml")


if __name__ == "__main__":
    unittest.main()
