
"""Tests for DSSA Docker readiness report rendering."""

import json
import unittest
from copy import deepcopy

from bridge.applications.readiness.daemon_reconciliation import (
    PREFLIGHT_REASONS,
)
from bridge.applications.readiness.readiness_explanation import (
    DAEMON_FINDINGS,
    RUNTIME_FINDINGS,
    explain_docker_readiness,
)
from bridge.applications.readiness.report_renderer import (
    render_docker_report,
)


def valid_explanation(
    status="REVIEW_REQUIRED",
    runtime="CLI_ABSENT",
    daemon="DAEMON_UNIT_NOT_FOUND",
):
    """Create an explanation through the established readiness layer."""

    readiness = {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": status,
        "runtime_status": runtime,
        "daemon_preflight_status": daemon,
        "reasons": [PREFLIGHT_REASONS[daemon]],
        "evidence_request_ids": {
            f"discovery.source_{i}": f"request-{i}"
            for i in range(7)
        } | {
            "discovery.application_runtime": "request-7",
            "discovery.docker_daemon_preflight": "request-8",
        },
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }

    return explain_docker_readiness(readiness)


class TestReadinessReportRenderer(unittest.TestCase):
    """Verify deterministic output, structure and reporting safety."""

    def test_text_report(self):
        output = render_docker_report(valid_explanation(), "text")

        self.assertIn("DSSA DOCKER READINESS REPORT", output)
        self.assertIn("Target       : ubuntu24", output)
        self.assertIn("Readiness    : REVIEW_REQUIRED", output)
        self.assertIn("API health   : NOT VERIFIED", output)
        self.assertIn("Execution: DISABLED", output)

    def test_json_round_trip(self):
        explanation = valid_explanation()

        output = render_docker_report(explanation, "json")

        self.assertEqual(json.loads(output), explanation)

    def test_all_runtime_and_daemon_classifications(self):
        for runtime in RUNTIME_FINDINGS:
            for daemon in DAEMON_FINDINGS:
                with self.subTest(runtime=runtime, daemon=daemon):
                    explanation = valid_explanation(
                        runtime=runtime,
                        daemon=daemon,
                    )

                    text = render_docker_report(explanation)
                    result = json.loads(
                        render_docker_report(explanation, "json")
                    )

                    self.assertIn(runtime, text)
                    self.assertIn(daemon, text)
                    self.assertEqual(len(result["findings"]), 3)

    def test_blocked_readiness(self):
        explanation = valid_explanation(status="BLOCKED")

        output = render_docker_report(explanation)

        self.assertIn("BLOCKED", output)
        self.assertIn("EXISTING_READINESS_BLOCKER", output)

    def test_input_unchanged(self):
        explanation = valid_explanation()
        original = deepcopy(explanation)

        render_docker_report(explanation, "text")
        render_docker_report(explanation, "json")

        self.assertEqual(explanation, original)

    def test_deterministic_output(self):
        explanation = valid_explanation()

        self.assertEqual(
            render_docker_report(explanation),
            render_docker_report(deepcopy(explanation)),
        )

    def test_reject_invalid_formats(self):
        for output_format in ("html", "yaml", "", None):
            with self.subTest(output_format=output_format):
                with self.assertRaises(ValueError):
                    render_docker_report(
                        valid_explanation(),
                        output_format,
                    )

    def test_reject_invalid_policy(self):
        for field, value in (
            ("execution_allowed", True),
            ("planning_only", False),
            ("daemon_responsive", True),
        ):
            with self.subTest(field=field):
                explanation = valid_explanation()
                explanation[field] = value

                with self.assertRaises(ValueError):
                    render_docker_report(explanation)

    def test_reject_duplicate_evidence(self):
        explanation = valid_explanation()
        explanation["evidence_request_ids"][
            "discovery.docker_daemon_preflight"
        ] = "request-7"

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_missing_evidence(self):
        explanation = valid_explanation()
        del explanation["evidence_request_ids"][
            "discovery.application_runtime"
        ]

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_finding_execution_permission(self):
        explanation = valid_explanation()
        explanation["findings"][0]["execution_allowed"] = True

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_invalid_finding(self):
        explanation = valid_explanation()
        explanation["findings"][0]["title"] = None

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_unexpected_fields(self):
        explanation = valid_explanation()
        explanation["installation_authorized"] = True

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_non_dictionary_input(self):
        for value in (None, [], "docker", 123):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    render_docker_report(value)

    def test_reject_inconsistent_runtime_finding(self):
        explanation = valid_explanation()
        explanation["findings"][0]["code"] = "DOCKER_CLI_AVAILABLE"

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_inconsistent_daemon_finding(self):
        explanation = valid_explanation()
        explanation["findings"][1]["code"] = "DOCKER_DAEMON_FAILED"

        with self.assertRaises(ValueError):
            render_docker_report(explanation)

    def test_reject_missing_source_reason(self):
        explanation = valid_explanation()
        explanation["source_reasons"] = ["UNRELATED_REASON"]

        with self.assertRaises(ValueError):
            render_docker_report(explanation)


if __name__ == "__main__":
    unittest.main()
