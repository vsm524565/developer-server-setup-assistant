
"""Read-only DSSA application assessment CLI."""

import argparse
import contextlib
import re
import subprocess
import sys
from contextlib import contextmanager
import os

from bridge.adapters.linux_backend import LinuxBackend
from bridge.applications.readiness.daemon_coordinator import (
    assess_daemon_aware_readiness,
)
from bridge.applications.readiness.readiness_explanation import (
    explain_docker_readiness,
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

            readiness = assess_daemon_aware_readiness(
                backend,
                args.target,
                args.application,
                max_age_seconds=120,
            )

            explanation = explain_docker_readiness(readiness)

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
