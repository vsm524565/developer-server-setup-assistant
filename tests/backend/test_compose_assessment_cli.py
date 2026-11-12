
"""Regression tests for Docker Compose public assessment CLI."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from bridge.assessment_cli import build_parser, run_assessment_cli
from bridge.applications.readiness.compose_explanation import (
    explain_compose_readiness,
)


def compose_workflow():
    """Create a synthetic but valid Compose workflow result."""
    identities = {
        f"discovery.source_{index}": f"request-{index}"
        for index in range(1, 8)
    }
    identities["discovery.application_runtime"] = "request-8"

    readiness = {
        "target_id": "ubuntu24",
        "application_id": "docker_compose",
        "status": "REVIEW_REQUIRED",
        "runtime_status": "PLUGIN_ABSENT",
        "reasons": ["COMPOSE_PLUGIN_ABSENT"],
        "evidence_request_ids": identities,
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }

    explanation = explain_compose_readiness(readiness)

    return {
        "schema_version": "1.0",
        "check": "application_workflow_result",
        "workflow": "readiness_assessment",
        "target": {"kind": "inventory", "id": "ubuntu24"},
        "application_id": "docker_compose",
        "status": "REVIEW_REQUIRED",
        "result": explanation,
        "planning_only": True,
        "execution_allowed": False,
    }


class TestComposeAssessmentCLI(unittest.TestCase):
    """Verify Compose CLI routing without remote discovery."""

    def execute_cli(self, fmt="json", workflow=None):
        """Capture the command with a synthetic workflow result."""
        if workflow is None:
            workflow = compose_workflow()

        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("bridge.assessment_cli.LinuxBackend"),
            patch(
                "bridge.assessment_cli.assess_application_workflow",
                return_value=workflow,
            ) as assess,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_assessment_cli([
                "docker_compose",
                "--target", "ubuntu24",
                "--format", fmt,
            ])

        return code, stdout.getvalue(), stderr.getvalue(), assess

    def test_parser_accepts_compose(self):
        args = build_parser().parse_args([
            "docker_compose", "--target", "ubuntu24"
        ])
        self.assertEqual(args.application, "docker_compose")

    def test_compose_json_report(self):
        code, output, errors, assess = self.execute_cli()

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")

        report = json.loads(output)

        self.assertEqual(
            report["check"],
            "docker_compose_readiness_explanation",
        )
        self.assertEqual(report["application_id"], "docker_compose")
        self.assertEqual(len(report["evidence_request_ids"]), 8)
        self.assertIs(report["planning_only"], True)
        self.assertIs(report["execution_allowed"], False)
        self.assertIsNone(report["daemon_responsive"])

        assess.assert_called_once()

    def test_compose_text_report(self):
        code, output, errors, _ = self.execute_cli("text")

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("DOCKER COMPOSE READINESS REPORT", output)
        self.assertIn("Distinct identities: 8", output)
        self.assertIn("Execution: DISABLED", output)
        self.assertIn("Compose project execution: UNKNOWN", output)

    def test_compose_identity_mismatch_rejected(self):
        workflow = compose_workflow()
        workflow["result"]["target_id"] = "rocky10"

        code, output, errors, _ = self.execute_cli(
            workflow=workflow
        )

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("assessment failed", errors)

    def test_compose_execution_authorization_rejected(self):
        workflow = compose_workflow()
        workflow["result"]["execution_allowed"] = True

        code, output, errors, _ = self.execute_cli(
            workflow=workflow
        )

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("assessment failed", errors)

    def test_backend_failure_sanitized(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("bridge.assessment_cli.LinuxBackend"),
            patch(
                "bridge.assessment_cli.assess_application_workflow",
                side_effect=RuntimeError("SECRET_BACKEND_DETAIL"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_assessment_cli([
                "docker_compose",
                "--target", "ubuntu24",
                "--format", "json",
            ])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("assessment failed", stderr.getvalue())
        self.assertNotIn("SECRET_BACKEND_DETAIL", stderr.getvalue())

    def test_public_help_lists_both_applications(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "bridge.cli",
                "assess", "--help",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker_compose", result.stdout)
        self.assertIn("docker", result.stdout)


if __name__ == "__main__":
    unittest.main()
