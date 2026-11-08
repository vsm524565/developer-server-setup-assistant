
"""Read-only DSSA application assessment CLI."""

import argparse
import contextlib
import re
import subprocess
import sys
from contextlib import contextmanager
import os

from bridge.adapters.linux_backend import LinuxBackend
from bridge.workflows.coordinator import (
    assess_application_workflow,
)
from bridge.applications.readiness.report_renderer import (
    render_docker_report,
)


TARGET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def validate_target(value):
    """Accept a safe inventory identifier rather than SSH connection data."""
    if not TARGET_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "Target must be a valid inventory ID using letters, "
            "numbers, underscores or hyphens."
        )

    return value


def build_parser():
    """Define the read-only assessment command interface."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.cli assess",
        description="DSSA read-only application readiness assessment",
    )

    parser.add_argument(
        "application",
        choices=("docker",),
        help="Application to assess",
    )

    parser.add_argument(
        "--target",
        required=True,
        type=validate_target,
        help="Existing DSSA inventory target ID",
    )

    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="Report output format (default: text)",
    )

    return parser


@contextmanager
def isolated_discovery_stdout():
    """Suppress Python and inherited FD 1 output during discovery."""

    saved_stdout_fd = os.dup(1)

    try:
        sys.stdout.flush()

        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)

            with contextlib.redirect_stdout(sink):
                yield

            sink.flush()

    finally:
        # Restore original stdout even if discovery raises an exception.
        os.dup2(saved_stdout_fd, 1)
        os.close(saved_stdout_fd)

def run_assessment_cli(argv=None):
    """Execute an approved assessment and print its validated report."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # Keep collector diagnostics off stdout, including in JSON mode.
        with isolated_discovery_stdout():
            backend = LinuxBackend()

            # Build a read-only inventory-managed workflow request.
            request = {
                "schema_version": "1.0",
                "workflow": "readiness_assessment",
                "target": {
                    "kind": "inventory",
                    "id": args.target,
                },
                "application_id": args.application,
                "max_age_seconds": 120,
            }

            # Reuse the shared coordinator and its evidence validation.
            workflow = assess_application_workflow(
                backend,
                request,
            )

            # Fail closed if the workflow boundary is violated.
            if (
                not isinstance(workflow, dict)
                or workflow.get("schema_version") != "1.0"
                or workflow.get("check") != "application_workflow_result"
                or workflow.get("workflow") != "readiness_assessment"
                or workflow.get("target") != request["target"]
                or workflow.get("application_id") != args.application
                or workflow.get("planning_only") is not True
                or workflow.get("execution_allowed") is not False
                or not isinstance(workflow.get("result"), dict)
            ):
                raise RuntimeError("Invalid assessment workflow result.")

            explanation = workflow["result"]

            if (
                explanation.get("target_id") != args.target
                or explanation.get("application_id") != args.application
                or explanation.get("status") != workflow.get("status")
                or explanation.get("planning_only") is not True
                or explanation.get("execution_allowed") is not False
            ):
                raise RuntimeError("Assessment workflow identity mismatch.")


            report = render_docker_report(
                explanation,
                output_format=args.output_format,
            )

        sys.stdout.write(report)
        return 0

    except KeyboardInterrupt:
        print(
            "ERROR: Assessment interrupted.",
            file=sys.stderr,
        )
        return 130

    except (
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ):
        # Do not expose SSH credentials or sensitive backend exception data.
        print(
            "ERROR: Docker readiness assessment failed. "
            "Review the backend diagnostics.",
            file=sys.stderr,
        )
        return 1
