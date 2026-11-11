
"""Offline CLI for discovering supported DSSA workflow capabilities."""

import argparse
import json
import sys

from bridge.workflows.discovery import (
    discover_workflow_capabilities,
    validate_capability_report,
)


def build_parser():
    """Define the offline capability discovery command."""
    parser = argparse.ArgumentParser(
        prog="python -m bridge.cli capabilities",
        description="List implemented DSSA read-only workflow capabilities",
    )

    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="Report format (default: text)",
    )

    return parser



def render_capabilities(report, output_format="text"):
    """Render validated capabilities with a strict output contract."""

    # Reject unsupported formats before examining report contents.
    if output_format not in ("text", "json"):
        raise ValueError("Unsupported capability report format.")

    # Complete contract validation before producing any output.
    validated = validate_capability_report(report)

    if output_format == "json":
        return json.dumps(
            validated,
            indent=2,
            sort_keys=True,
        ) + "\n"

    lines = [
        "=== DSSA WORKFLOW CAPABILITIES ===",
        f"Schema version: {validated['schema_version']}",
        f"Registered capabilities: {len(validated['capabilities'])}",
        "",
    ]

    if not validated["capabilities"]:
        lines.append("No workflow capabilities are available.")
    else:
        for index, capability in enumerate(
            validated["capabilities"], start=1
        ):
            lines.extend([
                f"[{index}] {capability['application_id']}",
                f"    Workflow: {capability['workflow']}",
                f"    Target: {capability['target_kind']}",
                "    Planning only: YES",
                "    Execution: DISABLED",
                "",
            ])

    return "\n".join(lines).rstrip("\n") + "\n"


def run_capability_cli(argv=None):
    """Discover and print capabilities without creating a backend."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        report = discover_workflow_capabilities()
        rendered = render_capabilities(
            report,
            output_format=args.output_format,
        )

        sys.stdout.write(rendered)
        return 0

    except KeyboardInterrupt:
        print(
            "ERROR: Capability discovery interrupted.",
            file=sys.stderr,
        )
        return 130

    except (RuntimeError, OSError, KeyError, TypeError, ValueError):
        # Keep internal registry and application details out of CLI errors.
        print(
            "ERROR: Workflow capability discovery failed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(run_capability_cli())
