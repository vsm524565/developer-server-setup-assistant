
"""Regression tests for the read-only DSSA assessment CLI."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

from bridge.assessment_cli import run_assessment_cli


def sample_explanation(status="REVIEW_REQUIRED", target="ubuntu24"):
    """Build a minimal explanation for mocked CLI boundary tests."""
    return {
        "target_id": target,
        "application_id": "docker",
        "status": status,
        "planning_only": True,
        "execution_allowed": False,
    }


def sample_workflow(status="REVIEW_REQUIRED", target="ubuntu24"):
    """Build a valid workflow envelope for CLI contract tests."""
    return {
        "schema_version": "1.0",
        "check": "application_workflow_result",
        "workflow": "readiness_assessment",
        "target": {
            "kind": "inventory",
            "id": target,
        },
        "application_id": "docker",
        "status": status,
        "result": sample_explanation(status, target),
        "planning_only": True,
        "execution_allowed": False,
    }


class TestAssessmentCLI(unittest.TestCase):
    """Verify CLI routing, stdout isolation and safety boundaries."""

    def execute(self, arguments, workflow=None):
        """Run the CLI using a mocked shared workflow coordinator."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        target = arguments[arguments.index("--target") + 1]

        if workflow is None:
            workflow = sample_workflow(target=target)

        with (
            patch(
                "bridge.assessment_cli.LinuxBackend"
            ) as backend_class,
            patch(
                "bridge.assessment_cli.assess_application_workflow",
                return_value=workflow,
            ) as coordinator,
            patch(
                "bridge.assessment_cli.render_docker_report",
                return_value="REPORT OUTPUT\n",
            ) as renderer,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = run_assessment_cli(arguments)
            backend_class.return_value.setup_components.assert_not_called()

        return {
            "exit_code": exit_code,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "backend": backend_class,
            "coordinator": coordinator,
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
            {
                "schema_version": "1.0",
                "workflow": "readiness_assessment",
                "target": {
                    "kind": "inventory",
                    "id": "ubuntu24",
                },
                "application_id": "docker",
                "max_age_seconds": 120,
            },
        )

        result["renderer"].assert_called_once_with(
            sample_explanation(),
            output_format="text",
        )

    def test_json_format_selection(self):
        result = self.execute([
            "docker", "--target", "ubuntu24",
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
            workflow=sample_workflow(status="BLOCKED"),
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

        for arguments in scenarios:
            with self.subTest(arguments=arguments):
                with (
                    patch(
                        "bridge.assessment_cli.LinuxBackend"
                    ) as backend,
                    patch(
                        "bridge.assessment_cli.assess_application_workflow"
                    ) as coordinator,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as error:
                        run_assessment_cli(arguments)

                self.assertEqual(error.exception.code, 2)
                backend.assert_not_called()
                coordinator.assert_not_called()

    def test_backend_stdout_diagnostics_are_suppressed(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def noisy_workflow(*args, **kwargs):
            print("COLLECTOR DIAGNOSTIC")
            return sample_workflow()

        with (
            patch("bridge.assessment_cli.LinuxBackend") as backend,
            patch(
                "bridge.assessment_cli.assess_application_workflow",
                side_effect=noisy_workflow,
            ),
            patch(
                "bridge.assessment_cli.render_docker_report",
                return_value='{"status":"REVIEW_REQUIRED"}\n',
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = run_assessment_cli([
                "docker", "--target", "ubuntu24",
                "--format", "json",
            ])

            backend.return_value.setup_components.assert_not_called()

        self.assertEqual(exit_code, 0)
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
                "bridge.assessment_cli.assess_application_workflow",
                side_effect=RuntimeError(secret),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = run_assessment_cli([
                "docker", "--target", "ubuntu24",
            ])

            backend.return_value.setup_components.assert_not_called()

        self.assertEqual(exit_code, 1)
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
                "bridge.assessment_cli.assess_application_workflow",
                side_effect=KeyboardInterrupt,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = run_assessment_cli([
                "docker", "--target", "ubuntu24",
            ])

        self.assertEqual(exit_code, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("interrupted", stderr.getvalue().lower())

    def test_no_setup_function_called_for_text_or_json(self):
        for output_format in ("text", "json"):
            with self.subTest(output_format=output_format):
                result = self.execute([
                    "docker", "--target", "ubuntu24",
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

                request = result["coordinator"].call_args.args[1]
                self.assertEqual(request["target"]["id"], target)

    def test_invalid_workflow_envelope_rejected(self):
        modifications = (
            ("schema_version", "2.0"),
            ("check", "installation_result"),
            ("workflow", "installation"),
            ("target", {"kind": "inventory", "id": "rocky10"}),
            ("application_id", "nginx"),
            ("planning_only", False),
            ("execution_allowed", True),
            ("result", None),
        )

        for field, value in modifications:
            with self.subTest(field=field):
                workflow = sample_workflow()
                workflow[field] = value

                result = self.execute(
                    ["docker", "--target", "ubuntu24"],
                    workflow=workflow,
                )

                self.assertEqual(result["exit_code"], 1)
                self.assertEqual(result["stdout"], "")
                self.assertIn(
                    "assessment failed",
                    result["stderr"].lower(),
                )
                result["renderer"].assert_not_called()

    def test_mismatched_explanation_rejected(self):
        modifications = (
            ("target_id", "rocky10"),
            ("application_id", "nginx"),
            ("status", "BLOCKED"),
            ("planning_only", False),
            ("execution_allowed", True),
        )

        for field, value in modifications:
            with self.subTest(field=field):
                workflow = deepcopy(sample_workflow())
                workflow["result"][field] = value

                result = self.execute(
                    ["docker", "--target", "ubuntu24"],
                    workflow=workflow,
                )

                self.assertEqual(result["exit_code"], 1)
                self.assertEqual(result["stdout"], "")
                result["renderer"].assert_not_called()

    def test_native_stdout_isolation_in_subprocess(self):
        """Verify inherited FD 1 and Python stdout are isolated."""
        script = r'''
            import subprocess
            import sys
            from unittest.mock import patch

            from bridge.assessment_cli import run_assessment_cli


            def noisy_workflow(*args, **kwargs):
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

                return {
                    "schema_version": "1.0",
                    "check": "application_workflow_result",
                    "workflow": "readiness_assessment",
                    "target": {"kind": "inventory", "id": "ubuntu24"},
                    "application_id": "docker",
                    "status": "REVIEW_REQUIRED",
                    "result": {
                        "target_id": "ubuntu24",
                        "application_id": "docker",
                        "status": "REVIEW_REQUIRED",
                        "planning_only": True,
                        "execution_allowed": False,
                    },
                    "planning_only": True,
                    "execution_allowed": False,
                }


            with (
                patch("bridge.assessment_cli.LinuxBackend") as backend,
                patch(
                    "bridge.assessment_cli.assess_application_workflow",
                    side_effect=noisy_workflow,
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

                for marker in (
                    "NATIVE_STDOUT_MARKER",
                    "PYTHON_STDOUT_MARKER",
                ):
                    self.assertNotIn(marker, result.stdout)
                    self.assertNotIn(marker, result.stderr)

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
