
"""
Controlled SSH command execution with bounded output and deadlines.

Stage 3.5 supports registered diagnostic operations only.
Remote command cancellation is not guaranteed after a timeout.
"""

import select
import socket
import time

import paramiko

from execution.operations import OPERATIONS


MAX_OUTPUT_BYTES = 65536
READ_CHUNK_SIZE = 16384


class RemoteExecutor:
    """Execute allowlisted operations through an existing SSH client."""

    def __init__(self, ssh_client, sudo_password):
        self.ssh_client = ssh_client
        self.sudo_password = sudo_password

    @staticmethod
    def _append_bounded(buffer, chunk, limit):
        """Capture up to limit bytes and report whether data was discarded."""
        remaining = max(0, limit - len(buffer))

        if len(chunk) > remaining:
            buffer.extend(chunk[:remaining])
            return True

        buffer.extend(chunk)
        return False

    def execute(self, operation_name):
        """Execute a registered diagnostic operation."""
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
        output_truncated = False
        stderr_truncated = False

        def result(state, exit_code=None, message=None):
            """Build a consistent execution result."""
            stderr_text = error.decode(
                "utf-8", errors="replace"
            ).strip()

            if message:
                stderr_text = (
                    f"{stderr_text}\n{message}"
                    if stderr_text
                    else message
                )

            return {
                "operation": operation_name,
                "success": (
                    state == "COMPLETED" and exit_code == 0
                ),
                "exit_code": exit_code,
                "stdout": output.decode(
                    "utf-8", errors="replace"
                ).strip(),
                "stderr": stderr_text,
                "duration": round(
                    time.monotonic() - started, 3
                ),
                "timed_out": state == "TIMED_OUT",
                "output_truncated": (
                    output_truncated or stderr_truncated
                ),
                "execution_state": state,
            }

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
                    chunk = channel.recv(READ_CHUNK_SIZE)
                    output_truncated |= self._append_bounded(
                        output, chunk, MAX_OUTPUT_BYTES
                    )

                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(READ_CHUNK_SIZE)
                    stderr_truncated |= self._append_bounded(
                        error, chunk, MAX_OUTPUT_BYTES
                    )

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    exit_code = channel.recv_exit_status()
                    return result("COMPLETED", exit_code)

                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    channel.close()
                    return result(
                        "TIMED_OUT",
                        message=(
                            "Execution deadline exceeded. "
                            "Remote process termination is not confirmed."
                        ),
                    )

                select.select(
                    [channel], [], [], min(remaining, 0.1)
                )

        except (socket.timeout, TimeoutError):
            return result(
                "TIMED_OUT",
                message=(
                    "SSH operation timed out. "
                    "Remote process state is not confirmed."
                ),
            )

        except (
            paramiko.SSHException,
            EOFError,
            OSError,
        ) as exc:
            return result(
                "UNKNOWN",
                message=(
                    "SSH transport interrupted: "
                    f"{type(exc).__name__}. "
                    "Remote process state is unknown."
                ),
            )

        finally:
            if channel is not None:
                channel.close()


def display_execution_result(result):
    """Display execution results without revealing credentials."""

    print(f"\nOperation       : {result['operation']}")
    print(f"State           : {result['execution_state']}")
    print(f"Success         : {result['success']}")
    print(f"Exit code       : {result['exit_code']}")
    print(f"Duration        : {result['duration']}s")
    print(f"Timed out       : {result['timed_out']}")
    print(f"Output truncated: {result['output_truncated']}")

    if result["stdout"]:
        print(f"Output          : {result['stdout']}")

    if result["stderr"]:
        print(f"Error           : {result['stderr']}")
