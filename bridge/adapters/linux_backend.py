"""Invoke the Linux backend without handling SSH credentials."""

import json
import subprocess
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LinuxBackend:
    """Coordinate discovery and the existing interactive lab installer."""

    def discover_web_servers(self, target_id):
        """Request validated web-server discovery from the dispatcher."""
        request = {
            "schema_version": "1.0",
            "request_id": f"bridge-{uuid.uuid4().hex}",
            "operation": "discovery.web_servers",
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

        if result.returncode != 0 or response.get("status") != "success":
            raise RuntimeError(
                f"Discovery failed: {response.get('errors', [])}. "
                f"{result.stderr.strip()}"
            )

        if (
            response.get("request_id") != request["request_id"]
            or response.get("operation") != request["operation"]
        ):
            raise RuntimeError("Backend response does not match the request.")

        return response["data"]["web_servers"]

    def install_nginx(self, target_id):
        """Run the fixed backend installer with terminal prompts intact."""
        result = subprocess.run(
            [
                str(ROOT / "backend/linux/bin/install-nginx-lab.sh"),
                target_id,
            ],
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Installation did not complete successfully "
                f"(exit {result.returncode}). "
                "Inspect the backend output before retrying."
            )
