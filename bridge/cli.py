"""DSSA CLI for the first integrated Linux installation workflow."""

import argparse
import subprocess

from bridge.adapters.linux_backend import LinuxBackend


def main():
    """Discover, install through Bash, and verify Nginx."""
    parser = argparse.ArgumentParser(description="DSSA Linux setup")
    parser.add_argument("--target", required=True)
    parser.add_argument("--component", required=True, choices=["nginx"])
    args = parser.parse_args()

    backend = LinuxBackend()

    try:
        print(f"\nDiscovering web servers on {args.target}...", flush=True)
        servers = backend.discover_web_servers(args.target)
        nginx = servers["nginx"]

        print("\n=== PROPOSED ACTION ===")
        print(f"Target: {args.target}")
        print("Component: nginx")

        if nginx["available"]:
            print("Action: validate configuration and ensure service is active")
        else:
            print("Action: install Nginx and start its service")

        print(
            "The backend will check platform and installation constraints "
            "and request confirmation.",
            flush=True,
        )

        backend.install_nginx(args.target)

        print("\nVerifying through backend discovery...", flush=True)
        verified = backend.discover_web_servers(args.target)["nginx"]

        if not (
            verified["available"]
            and verified["detection_status"] == "detected"
            and verified["service_state"] == "active"
        ):
            raise RuntimeError(
                f"Post-operation verification failed: {verified}"
            )

        print("\n=== RESULT ===")
        print("Status: VERIFIED")
        print(f"Path: {verified['path']}")
        print(f"Version: {verified['version']}")
        print(f"Service: {verified['service_name']}")
        print(f"State: {verified['service_state']}")
        return 0

    except subprocess.TimeoutExpired:
        print("\nERROR: Discovery timed out.")
        return 1
    except (RuntimeError, OSError, KeyError, TypeError) as error:
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
