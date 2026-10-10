
"""
Regression tests for the controlled SSH executor.

Tests normal execution, sudo, timeout, bounded output,
nonzero exit codes and operation allowlist enforcement.
"""

from getpass import getpass
from pathlib import Path

import paramiko

from execution.executor import (
    RemoteExecutor,
    display_execution_result,
)


def main():
    """Run remote executor regression tests."""

    host = input("Server IP: ").strip()
    username = input("SSH username: ").strip()
    password = getpass("Password: ")

    client = paramiko.SSHClient()
    client.load_system_host_keys()

    known_hosts = Path.home() / ".ssh" / "known_hosts"

    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))

    client.set_missing_host_key_policy(
        paramiko.RejectPolicy()
    )

    try:
        client.connect(
            hostname=host,
            username=username,
            password=password,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )

        print("\nSSH connection established.")

        executor = RemoteExecutor(
            ssh_client=client,
            sudo_password=password,
        )

        operations = (
            "check_hostname",
            "check_identity",
            "check_root_identity",
            "check_nginx_service",
            "test_timeout",
            "test_large_output",
            "test_nonzero_exit",
        )

        for operation_name in operations:
            result = executor.execute(operation_name)

            if operation_name == "test_large_output":
                print("\n=== LARGE OUTPUT TEST ===")
                print("State:", result["execution_state"])
                print(
                    "Captured bytes:",
                    len(result["stdout"].encode("utf-8")),
                )
                print(
                    "Truncated:",
                    result["output_truncated"],
                )

            else:
                display_execution_result(result)

        print("\n=== OPERATION ALLOWLIST TEST ===")

        try:
            executor.execute("install_nginx")

            print(
                "FAILED: Unregistered operation was accepted."
            )

        except ValueError as error:
            print(f"PASSED: {error}")

    finally:
        client.close()
        print("\nSSH connection closed.")


if __name__ == "__main__":
    main()
