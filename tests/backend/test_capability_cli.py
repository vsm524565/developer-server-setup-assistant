
"""Permanent tests for the offline DSSA capability CLI."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

from bridge.capability_cli import (
    render_capabilities,
    run_capability_cli,
)


def valid_report():
    """Provide the expected Docker-only capability report."""
    return {
        "schema_version": "1.0",
        "check": "workflow_capabilities",
        "capabilities": [{
            "application_id": "docker",
            "workflow": "readiness_assessment",
            "target_kind": "inventory",
            "planning_only": True,
            "execution_allowed": False,
        }],
    }


class TestCapabilityCLI(unittest.TestCase):
    """Verify offline CLI behavior, reporting and safety."""

    def execute_cli(self, arguments, report=None):
        """Capture CLI output while replacing the discovery boundary."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        if report is None:
            report = valid_report()

        with (
            patch(
                "bridge.capability_cli.discover_workflow_capabilities",
                return_value=deepcopy(report),
            ) as discover,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_capability_cli(arguments)

        return code, stdout.getvalue(), stderr.getvalue(), discover

    def test_default_text_format(self):
        code, output, errors, discover = self.execute_cli([])

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("DSSA WORKFLOW CAPABILITIES", output)
        self.assertIn("docker", output)
        self.assertIn("readiness_assessment", output)
        self.assertIn("Execution: DISABLED", output)
        discover.assert_called_once_with()

    def test_json_format(self):
        code, output, errors, _ = self.execute_cli(
            ["--format", "json"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(json.loads(output), valid_report())

    def test_text_format_explicit(self):
        code, output, errors, _ = self.execute_cli(
            ["--format", "text"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("Planning only: YES", output)

    def test_render_does_not_modify_input(self):
        report = valid_report()
        original = deepcopy(report)

        render_capabilities(report, "text")
        render_capabilities(report, "json")

        self.assertEqual(report, original)

    def test_unsafe_report_rejected(self):
        report = valid_report()
        report["capabilities"][0]["execution_allowed"] = True

        with self.assertRaises(ValueError):
            render_capabilities(report, "json")

    def test_invalid_report_format_rejected(self):
        with self.assertRaises(ValueError):
            render_capabilities(valid_report(), "xml")

    def test_empty_capabilities_rendered(self):
        report = valid_report()
        report["capabilities"] = []

        text = render_capabilities(report, "text")
        data = json.loads(render_capabilities(report, "json"))

        self.assertIn("No workflow capabilities", text)
        self.assertEqual(data["capabilities"], [])

    def test_discovery_failure_sanitized(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch(
                "bridge.capability_cli.discover_workflow_capabilities",
                side_effect=RuntimeError(
                    "Sensitive backend diagnostic"
                ),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_capability_cli(["--format", "json"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("discovery failed", stderr.getvalue())
        self.assertNotIn("Sensitive", stderr.getvalue())

    def test_interrupt_handling(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch(
                "bridge.capability_cli.discover_workflow_capabilities",
                side_effect=KeyboardInterrupt,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_capability_cli([])

        self.assertEqual(code, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("interrupted", stderr.getvalue())

    def test_invalid_arguments_rejected_before_discovery(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "bridge.cli",
                "capabilities", "--format", "xml",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)

    def test_public_cli_json_entrypoint(self):
        """Validate the actual CLI entrypoint without SSH configuration."""
        result = subprocess.run(
            [
                sys.executable, "-m", "bridge.cli",
                "capabilities", "--format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), valid_report())

    def test_public_cli_text_entrypoint(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "bridge.cli",
                "capabilities", "--format", "text",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker", result.stdout)
        self.assertIn("Execution: DISABLED", result.stdout)

    def test_invalid_format_rejected_before_report_validation(self):
        """Reject unsupported formats without inspecting report contents."""
        with patch(
            "bridge.capability_cli.validate_capability_report"
        ) as validator:
            with self.assertRaisesRegex(
                ValueError, "Unsupported capability report format"
            ):
                render_capabilities(
                    {"invalid": "report"},
                    output_format="xml",
                )

        validator.assert_not_called()

    def test_malformed_discovery_report_sanitized(self):
        """Do not emit JSON when discovery returns an invalid contract."""
        report = valid_report()
        report["unexpected_field"] = "internal-data"

        code, output, errors, discover = self.execute_cli(
            ["--format", "json"],
            report=report,
        )

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn(
            "ERROR: Workflow capability discovery failed.",
            errors,
        )
        self.assertNotIn("internal-data", errors)
        discover.assert_called_once_with()

    def test_unsafe_discovery_report_sanitized(self):
        """Reject execution-enabled capabilities before printing."""
        report = valid_report()
        report["capabilities"][0]["execution_allowed"] = True

        code, output, errors, _ = self.execute_cli(
            ["--format", "json"],
            report=report,
        )

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("discovery failed", errors)
        self.assertNotIn("Unsafe workflow capability", errors)

    def test_unsorted_discovery_report_sanitized(self):
        """Prevent unsorted multi-capability reports from being published."""
        report = valid_report()
        report["capabilities"].append({
            "application_id": "apache",
            "workflow": "readiness_assessment",
            "target_kind": "inventory",
            "planning_only": True,
            "execution_allowed": False,
        })

        with patch(
            "bridge.workflows.discovery.resolve_workflow_handler",
            return_value="test_readonly_handler",
        ):
            code, output, errors, _ = self.execute_cli(
                ["--format", "json"],
                report=report,
            )

        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("discovery failed", errors)
        self.assertNotIn("not sorted", errors)

    def test_rendering_failure_does_not_publish_output(self):
        """Keep expected rendering errors on the sanitized error path."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch(
                "bridge.capability_cli.discover_workflow_capabilities",
                return_value=valid_report(),
            ),
            patch(
                "bridge.capability_cli.render_capabilities",
                side_effect=ValueError("Sensitive rendering detail"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_capability_cli(["--format", "json"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("discovery failed", stderr.getvalue())
        self.assertNotIn(
            "Sensitive rendering detail",
            stderr.getvalue(),
        )

    def test_repeated_rendering_is_deterministic(self):
        """Produce identical text and JSON from an unchanged contract."""
        report = valid_report()

        for output_format in ("text", "json"):
            first = render_capabilities(report, output_format)
            second = render_capabilities(report, output_format)

            self.assertEqual(first, second)
            self.assertTrue(first.endswith("\n"))

        self.assertEqual(report, valid_report())


if __name__ == "__main__":
    unittest.main()
