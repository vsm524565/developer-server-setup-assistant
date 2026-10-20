"""Invoke the Linux backend without handling SSH credentials."""

import json
import subprocess
import uuid
from pathlib import Path


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

    def setup_components(self, target_id, components):
        """Pass component selections to the authoritative Bash backend."""
        if not isinstance(components, list) or not components:
            raise ValueError("Select at least one component.")

        if any(
            not isinstance(component, str) or not component.strip()
            for component in components
        ):
            raise ValueError("Component selections must be nonempty strings.")

        result = subprocess.run(
            [
                str(ROOT / "backend/linux/bin/setup-components.sh"),
                target_id,
                *components,
            ],
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Backend setup did not complete successfully "
                f"(exit {result.returncode}). "
                "Inspect the backend output before retrying."
            )
