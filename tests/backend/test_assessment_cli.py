
"""Regression tests for the read-only DSSA assessment CLI."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch
import subprocess
import sys

from bridge.assessment_cli import run_assessment_cli


def sample_explanation(status="REVIEW_REQUIRED"):
    """Build a minimal report fixture for mocked CLI tests."""
    return {
        "target_id": "ubuntu24",
        "application_id": "docker",
        "status": status,
        "planning_only": True,
        "execution_allowed": False,
    }


class TestAssessmentCLI(unittest.TestCase):
    """Verify CLI routing, output isolation and failure handling."""

    def execute(self, arguments, readiness=None, explanation=None):
        """Execute a mocked assessment with captured output."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        if readiness is None:
            readiness = {"status": "REVIEW_REQUIRED"}

        if explanation is None:
            explanation = sample_explanation()

        with (
            patch(
                "bridge.assessment_cli.LinuxBackend"
            ) as backend_class,
            patch(
                "bridge.assessment_cli.assess_daemon_aware_readiness",
                return_value=readiness,
            ) as coordinator,
            patch(
                "bridge.assessment_cli.explain_docker_readiness",
                return_value=explanation,
            ) as explainer,
            patch(
                "bridge.assessment_cli.render_docker_report",
                return_value="REPORT OUTPUT\n",
            ) as renderer,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = run_assessment_cli(arguments)

            backend_class.return_value.setup_components.assert_not_called()

        return {
            "exit_code": result,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "backend": backend_class,
            "coordinator": coordinator,
            "explainer": explainer,
            "renderer": renderer,
        }

    def test_successful_text_report(self):
        result = self.execute([
            "docker", "--target", "ubuntu24",
        ])

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "REPORT OUTPUT\n")
        self.assertEqual(result["stderr"], "")

        result["coordinator"].assert_called_once_with(
            result["backend"].return_value,
            "ubuntu24",
            "docker",
            max_age_seconds=120,
        )

        result["renderer"].assert_called_once_with(
            sample_explanation(),
            output_format="text",
        )

    def test_json_format_selection(self):
        result = self.execute([
            "docker",
            "--target", "ubuntu24",
            "--format", "json",
        ])

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "REPORT OUTPUT\n")

        self.assertEqual(
            result["renderer"].call_args.kwargs["output_format"],
            "json",
        )

    def test_blocked_report_is_not_cli_failure(self):
        result = self.execute(
            ["docker", "--target", "ubuntu24"],
            explanation=sample_explanation("BLOCKED"),
        )

        self.assertEqual(result["exit_code"], 0)

    def test_invalid_arguments_rejected_before_backend(self):
        scenarios = (
            ["docker"],
            ["nginx", "--target", "ubuntu24"],
            ["docker", "--target", "../ubuntu24"],
            ["docker", "--target", "ubuntu24", "--format", "yaml"],
            ["docker", "--target", "ubuntu24", "--install"],
        )

        for args in scenarios:
            with self.subTest(arguments=args):
                with patch(
                    "bridge.assessment_cli.LinuxBackend"
                ) as backend:
                    with (
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        with self.assertRaises(SystemExit) as error:
                            run_assessment_cli(args)

                    self.assertEqual(error.exception.code, 2)
                    backend.assert_not_called()

    def test_backend_stdout_diagnostics_are_suppressed(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def discover(*args, **kwargs):
            print("COLLECTOR DIAGNOSTIC")
            return {"status": "REVIEW_REQUIRED"}

        with (
            patch("bridge.assessment_cli.LinuxBackend") as backend,
            patch(
                "bridge.assessment_cli.assess_daemon_aware_readiness",
                side_effect=discover,
            ),
            patch(
                "bridge.assessment_cli.explain_docker_readiness",
                return_value=sample_explanation(),
            ),
            patch(
                "bridge.assessment_cli.render_docker_report",
                return_value=json.dumps({"status": "REVIEW_REQUIRED"}) + "\n",
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = run_assessment_cli([
                "docker", "--target", "ubuntu24",
                "--format", "json",
            ])

            backend.return_value.setup_components.assert_not_called()

        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["status"],
            "REVIEW_REQUIRED",
        )
        self.assertNotIn("COLLECTOR DIAGNOSTIC", stdout.getvalue())
        self.assertNotIn("COLLECTOR DIAGNOSTIC", stderr.getvalue())

    def test_discovery_failure_is_sanitized(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        secret = "SENSITIVE_TEST_CREDENTIAL"

        with (
            patch("bridge.assessment_cli.LinuxBackend") as backend,
            patch(
                "bridge.assessment_cli.assess_daemon_aware_readiness",
                side_effect=RuntimeError(secret),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = run_assessment_cli([
                "docker", "--target", "ubuntu24",
            ])

            backend.return_value.setup_components.assert_not_called()

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret, stderr.getvalue())
        self.assertIn(
            "Docker readiness assessment failed",
            stderr.getvalue(),
        )

    def test_keyboard_interrupt(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("bridge.assessment_cli.LinuxBackend"),
            patch(
                "bridge.assessment_cli.assess_daemon_aware_readiness",
                side_effect=KeyboardInterrupt,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = run_assessment_cli([
                "docker", "--target", "ubuntu24",
            ])

        self.assertEqual(result, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("interrupted", stderr.getvalue().lower())

    def test_no_setup_function_called_for_text_or_json(self):
        for output_format in ("text", "json"):
            with self.subTest(output_format=output_format):
                result = self.execute([
                    "docker",
                    "--target", "ubuntu24",
                    "--format", output_format,
                ])

                result["backend"].return_value.setup_components.assert_not_called()

    def test_target_validation(self):
        for target in ("ubuntu22", "debian13", "alma10", "rocky10"):
            with self.subTest(target=target):
                result = self.execute([
                    "docker", "--target", target,
                ])

                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(
                    result["coordinator"].call_args.args[1],
                    target,
                )

    def test_native_stdout_isolation_in_subprocess(self):
        """Verify native and Python stdout cannot corrupt JSON reports."""

        script = r'''
import subprocess
import sys
from unittest.mock import patch

from bridge.assessment_cli import run_assessment_cli


def noisy_discovery(*args, **kwargs):
    subprocess.run(
        [
            sys.executable,
            "-c",
            "print('NATIVE_STDOUT_MARKER')",
        ],
        check=True,
    )
    print("PYTHON_STDOUT_MARKER")

    if sys.argv[1] == "failure":
        raise RuntimeError("SENSITIVE_TEST_CREDENTIAL")

    return {"status": "REVIEW_REQUIRED"}


with (
    patch("bridge.assessment_cli.LinuxBackend") as backend,
    patch(
        "bridge.assessment_cli.assess_daemon_aware_readiness",
        side_effect=noisy_discovery,
    ),
    patch(
        "bridge.assessment_cli.explain_docker_readiness",
        return_value={"status": "REVIEW_REQUIRED"},
    ),
    patch(
        "bridge.assessment_cli.render_docker_report",
        return_value='{"status":"REVIEW_REQUIRED"}\n',
    ),
):
    exit_code = run_assessment_cli([
        "docker",
        "--target", "ubuntu24",
        "--format", "json",
    ])

    backend.return_value.setup_components.assert_not_called()

raise SystemExit(exit_code)
'''

        for scenario in ("success", "failure"):
            with self.subTest(scenario=scenario):
                result = subprocess.run(
                    [sys.executable, "-c", script, scenario],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                self.assertNotIn(
                    "NATIVE_STDOUT_MARKER", result.stdout
                )
                self.assertNotIn(
                    "PYTHON_STDOUT_MARKER", result.stdout
                )
                self.assertNotIn(
                    "NATIVE_STDOUT_MARKER", result.stderr
                )
                self.assertNotIn(
                    "PYTHON_STDOUT_MARKER", result.stderr
                )

                if scenario == "success":
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(
                        json.loads(result.stdout),
                        {"status": "REVIEW_REQUIRED"},
                    )
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(
                        "assessment failed",
                        result.stderr.lower(),
                    )
                    self.assertNotIn(
                        "SENSITIVE_TEST_CREDENTIAL",
                        result.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
