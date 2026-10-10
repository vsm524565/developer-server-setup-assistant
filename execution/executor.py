
"""
Controlled remote command execution through an existing SSH connection.

Only registered operations may be executed. Each operation has a
configured privilege level and execution deadline.
"""

import select
import socket
import time

from execution.operations import OPERATIONS


class RemoteExecutor:
    """Execute registered remote operations through Paramiko."""

    def __init__(self, ssh_client, sudo_password):
        self.ssh_client = ssh_client
        self.sudo_password = sudo_password

    def execute(self, operation_name):
        """Execute a registered operation with a wall-clock deadline."""
        if operation_name not in OPERATIONS:
            raise ValueError(
                f"Operation is not permitted: {operation_name}"
            )

        operation = OPERATIONS[operation_name]
        command = operation["command"]
        requires_sudo = operation["sudo"]
        timeout = operation["timeout"]

        if requires_sudo:
            command = "sudo -S -p '' -- " + command

        started = time.monotonic()
        deadline = started + timeout

        channel = None
        output = bytearray()
        error = bytearray()

        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(
                command,
                timeout=timeout,
            )

            channel = stdout.channel

            if requires_sudo:
                stdin.write(self.sudo_password + "\n")
                stdin.flush()

            while True:
                if channel.recv_ready():
                    output.extend(channel.recv(65536))

                if channel.recv_stderr_ready():
                    error.extend(channel.recv_stderr(65536))

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break

                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    channel.close()

                    return {
                        "operation": operation_name,
                        "success": False,
                        "exit_code": None,
                        "stdout": output.decode(
                            "utf-8", errors="replace"
                        ).strip(),
                        "stderr": "Execution deadline exceeded.",
                        "duration": round(
                            time.monotonic() - started, 3
                        ),
                        "timed_out": True,
                    }

                select.select(
                    [channel],
                    [],
                    [],
                    min(remaining, 0.1),
                )

            exit_code = channel.recv_exit_status()

            return {
                "operation": operation_name,
                "success": exit_code == 0,
                "exit_code": exit_code,
                "stdout": output.decode(
                    "utf-8", errors="replace"
                ).strip(),
                "stderr": error.decode(
                    "utf-8", errors="replace"
                ).strip(),
                "duration": round(
                    time.monotonic() - started, 3
                ),
                "timed_out": False,
            }

        except (socket.timeout, TimeoutError):
            if channel is not None:
                channel.close()

            return {
                "operation": operation_name,
                "success": False,
                "exit_code": None,
                "stdout": output.decode(
                    "utf-8", errors="replace"
                ).strip(),
                "stderr": "Remote command timed out.",
                "duration": round(
                    time.monotonic() - started, 3
                ),
                "timed_out": True,
            }


def display_execution_result(result):
    """Display an operation result without exposing credentials."""
    print(f"\nOperation : {result['operation']}")
    print(f"Success   : {result['success']}")
    print(f"Exit code : {result['exit_code']}")
    print(f"Duration  : {result['duration']}s")

    if result["stdout"]:
        print(f"Output    : {result['stdout']}")

    if result["stderr"]:
        print(f"Error     : {result['stderr']}")
