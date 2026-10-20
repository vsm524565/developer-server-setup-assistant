"""DSSA CLI for the first integrated Linux installation workflow."""

import argparse
import subprocess

from bridge.adapters.linux_backend import LinuxBackend


def main():
    """Discover, install through Bash, and verify Nginx."""
    parser = argparse.ArgumentParser(description="DSSA Linux setup")
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--component",
        required=True,
        nargs="+",
        help="Components to process, separated by spaces",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Generate a read-only component plan",
    )
    args = parser.parse_args()

    backend = LinuxBackend()

    try:
        print(f"\nTarget: {args.target}")
        print(f"Selected components: {', '.join(args.component)}")
        print(
            "The Bash backend will validate selections, discover the "
            "environment and process the requested components.",
            flush=True,
        )

        response = backend.setup_components(
            args.target,
            args.component,
            plan_only=args.plan,
        )

        if args.plan:
            print("\n=== PLAN SUMMARY ===")
            print(f"Eligible: {response['eligible']}")

            for step in response["steps"]:
                print(f"{step['component']}: {step['action']}")

            print("No installation was attempted.")
        else:
            print("\n=== SETUP SUMMARY ===")

            for result in response["results"]:
                print(
                    f"{result['component']}: "
                    f"{result['package_action']} | "
                    f"verification={result['verification']}"
                )

        return 0

    except subprocess.TimeoutExpired:
        print("\nERROR: Discovery timed out.")
        return 1
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"\nERROR: {error}")
        return 1
    except KeyboardInterrupt:
        print(
            "\nInterrupted. If installation had started, "
            "remote completion is not confirmed."
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
