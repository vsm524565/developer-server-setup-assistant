"""Invoke the Linux backend without handling SSH credentials."""

import json
import subprocess
import uuid
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


class LinuxBackend:
    """Coordinate discovery and the existing interactive lab installer."""

    def _request(self, operation, target_id):
        """Send a discovery request and validate the backend response."""
        request = {
            "schema_version": "1.0",
            "request_id": f"bridge-{uuid.uuid4().hex}",
            "operation": operation,
            "target_id": target_id,
            "parameters": {},
        }

        result = subprocess.run(
            [str(ROOT / "backend/linux/bin/dispatcher.sh")],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=60,
        )

        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError(
                "Backend returned an invalid JSON response."
            ) from error
        if not isinstance(response, dict):
            raise RuntimeError("Backend response must be a JSON object.")

        if result.returncode != 0 or response.get("status") != "success":
            raise RuntimeError(
                f"Discovery failed: {response.get('errors', [])}. "
                f"{result.stderr.strip()}"
            )

        if (
            response.get("schema_version") != "1.0"
            or response.get("request_id") != request["request_id"]
            or response.get("operation") != request["operation"]
        ):
            raise RuntimeError("Backend response does not match the request.")

        data = response.get("data")

        if not isinstance(data, dict):
            raise RuntimeError("Backend response data must be an object.")

        return data

    def discover_system(self, target_id):
        """Retrieve system information for compatibility decisions."""
        return self._request("discovery.system", target_id)

    def discover_web_servers(self, target_id):
        """Retrieve web-server discovery results."""
        data = self._request("discovery.web_servers", target_id)
        servers = data.get("web_servers")

        if not isinstance(servers, dict):
            raise RuntimeError("Backend web-server data must be an object.")

        return servers

    def setup_components(self, target_id, components, plan_only=False):
        """Pass component selections to the authoritative Bash backend."""
        if not isinstance(components, list) or not components:
            raise ValueError("Select at least one component.")

        if any(
            not isinstance(component, str) or not component.strip()
            for component in components
        ):
            raise ValueError("Component selections must be nonempty strings.")

        command = [
            str(ROOT / "backend/linux/bin/setup-components.sh")
        ]

        if plan_only:
            command.append("--plan")

        command.extend([target_id, *components])
        # Inherit terminal input; read output as bytes to preserve CR/LF.
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        frames = []
        line = b""
        oversized_line = False
        previous = b""
        prefix = b"DSSA_RESPONSE: "

        try:
            while True:
                character = process.stdout.read(1)

                if not character:
                    break

                # Preserve remote CR/LF and complete standalone newlines.
                if character == b"\n" and previous != b"\r":
                    sys.stdout.buffer.write(b"\r")

                sys.stdout.buffer.write(character)
                sys.stdout.buffer.flush()
                previous = character

                if character == b"\n":
                    complete_line = line.rstrip(b"\r")

                    if complete_line.startswith(prefix):
                        frames.append(complete_line[len(prefix):])

                    line = b""
                elif len(line) < 1048576:
                    line += character
                else:
                    oversized_line = True

            complete_line = line.rstrip(b"\r")
            if complete_line.startswith(prefix):
                frames.append(complete_line[len(prefix):])

            exit_code = process.wait()

        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            process.stdout.close()

        # Without a response, the backend exit is our only outcome evidence.
        if exit_code != 0 and len(frames) != 1:
            raise RuntimeError(
                f"Backend failed (exit {exit_code}) without a usable response. "
                "Remote changes may be partial."
            )

        if oversized_line or len(frames) != 1:
            raise RuntimeError(
                "Expected exactly one bounded backend response frame."
            )

        try:
            response = json.loads(frames[0])
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError(
                f"Backend response contains invalid JSON: {error}"
            ) from error

        if (
            not isinstance(response, dict)
            or response.get("schema_version") != "1.0"
        ):
            raise RuntimeError("Unsupported backend response structure.")
        # Failure responses describe partial outcomes; they never mean success.
        if response.get("status") == "error":
            if exit_code == 0:
                raise RuntimeError(
                    "Backend reported an error but returned exit code zero."
                )

            records = response.get("results")
            errors = response.get("errors")

            if (
                not isinstance(records, list)
                or not all(isinstance(record, dict) for record in records)
                or not isinstance(errors, list)
                or not errors
                or not all(
                    isinstance(error, dict)
                    and isinstance(error.get("code"), str)
                    for error in errors
                )
            ):
                raise RuntimeError("Invalid backend failure response.")

            expected = list(dict.fromkeys(components))
            actual = [record.get("component") for record in records]

            # A wrapper-level failure may have no component outcome data.
            if records and actual != expected:
                raise RuntimeError(
                    "Failure response components do not match the selections."
                )

            response["backend_exit_code"] = exit_code
            return response

        if exit_code != 0:
            raise RuntimeError(
                f"Backend exited with {exit_code} despite a non-error response."
            )
        if plan_only:
            records = response.get("steps")
            if type(response.get("eligible")) is not bool:
                raise RuntimeError("Backend plan eligibility is invalid.")
        else:
            records = response.get("results")
            if response.get("status") != "success":
                raise RuntimeError("Backend did not report successful setup.")

        if (
            not isinstance(records, list)
            or not all(isinstance(record, dict) for record in records)
        ):
            raise RuntimeError("Backend component records are invalid.")

        expected = list(dict.fromkeys(components))
        actual = [record.get("component") for record in records]

        if actual != expected:
            raise RuntimeError(
                "Backend response components do not match the selections."
            )

        return response