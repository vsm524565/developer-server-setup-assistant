"""Invoke the Linux backend without handling SSH credentials."""

import json
import subprocess
import uuid
from pathlib import Path
from bridge.evidence.collection import EvidenceCollector
from bridge.evidence.freshness import evaluate_freshness
from bridge.evidence.gate import evaluate_evidence_gate

ROOT = Path(__file__).resolve().parents[2]


class LinuxBackend:
    """Coordinate read-only discovery. Installs go through dssa-run.sh (4.34)."""

    def _request(
        self, operation, target_id, *,
        parameters=None, include_metadata=False
    ):
        if parameters is not None and not isinstance(parameters, dict):
            raise ValueError("Discovery parameters must be a dictionary.")
        """Send a discovery request and validate the backend response."""
        request = {
            "schema_version": "1.0",
            "request_id": f"bridge-{uuid.uuid4().hex}",
            "operation": operation,
            "target_id": target_id,
            "parameters": {} if parameters is None else parameters,
        }
        collector = EvidenceCollector() if include_metadata else None
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

        if include_metadata:
            return collector.complete(
                request_id=request["request_id"],
                operation=operation,
                target_id=target_id,
                data=data,
                parameters=request["parameters"],
            )

        return data

    def discover_system(self, target_id):
        """Retrieve system information for compatibility decisions."""
        return self._request("discovery.system", target_id)
    def discover_bootstrap(self, target_id):
        """Discover common remote capabilities without modifying the server."""
        data = self._request("discovery.bootstrap", target_id)

        if (
            data.get("schema_version") != "1.0"
            or data.get("check") != "bootstrap_capabilities"
            or data.get("execution_allowed") is not False
        ):
            raise RuntimeError("Invalid bootstrap discovery response.")

        return data
    def discover_common_health(self, target_id):
        """Retrieve read-only identity, privilege and time-health data."""
        data = self._request("discovery.common_health", target_id)

        if (
            data.get("schema_version") != "1.0"
            or data.get("check") != "common_health"
            or data.get("execution_allowed") is not False
        ):
            raise RuntimeError("Invalid common-health discovery response.")

        for section, expected_check in (
            ("identity", "identity"),
            ("privileges", "privileges"),
            ("time_health", "time_health"),
        ):
            value = data.get(section)

            if (
                not isinstance(value, dict)
                or value.get("schema_version") != "1.0"
                or value.get("check") != expected_check
                or value.get("execution_allowed") is not False
            ):
                raise RuntimeError(
                    f"Invalid common-health section: {section}"
                )

        return data
    def discover_web_servers(self, target_id):
        """Retrieve web-server discovery results."""
        data = self._request("discovery.web_servers", target_id)
        servers = data.get("web_servers")

        if not isinstance(servers, dict):
            raise RuntimeError("Backend web-server data must be an object.")

        return servers

    def discover_with_evidence(
        self, operation, target_id, *, parameters=None
    ):
        """Collect approved discovery with controller timing metadata."""

        allowed = {
            "discovery.system",
            "discovery.bootstrap",
            "discovery.common_health",
            "discovery.package_manager",
            "discovery.runtimes",
            "discovery.web_servers",
            "discovery.swap_filesystem",
            "discovery.ports",
            "discovery.panels",
            "discovery.application_state",
            "discovery.application_inventory",
            "discovery.application_runtime",
            "discovery.docker_daemon_preflight",
        }

        if operation not in allowed:
            raise ValueError("Unsupported evidence discovery operation.")

        if operation == "discovery.swap_filesystem":
            if not isinstance(parameters, dict):
                raise ValueError("Swap filesystem parameters are required.")

            if set(parameters) != {"target_path"}:
                raise ValueError("Expected only target_path.")

            target_path = parameters["target_path"]

            if (
                not isinstance(target_path, str)
                or not target_path.startswith("/")
                or target_path == "/"
                or len(target_path) > 4096
                or "\n" in target_path
                or "\r" in target_path
            ):
                raise ValueError("Invalid swap filesystem target path.")

        elif operation in (
            "discovery.application_state",
            "discovery.application_inventory",
        ):
            allowed_applications = {
                "git",
                "nginx",
                "docker",
                "docker_compose",
            }

            if not isinstance(parameters, dict):
                raise ValueError(
                    "Application-state parameters are required."
                )

            if set(parameters) != {"application_id"}:
                raise ValueError(
                    "Expected only application_id."
                )

            application_id = parameters["application_id"]

            if (
                not isinstance(application_id, str)
                or application_id not in allowed_applications
            ):
                raise ValueError(
                    "Unsupported application-state discovery target."
                )

        elif operation == "discovery.application_runtime":
            if not isinstance(parameters, dict):
                raise ValueError(
                    "Runtime discovery parameters are required."
                )

            if set(parameters) != {"application_id"}:
                raise ValueError(
                    "Expected only application_id."
                )

            application_id = parameters["application_id"]

            if (
                not isinstance(application_id, str)
                or application_id not in (
                    "docker",
                    "docker_compose",
                )
            ):
                raise ValueError(
                    "Unsupported runtime discovery application."
                )

        elif operation == "discovery.docker_daemon_preflight":
            if parameters != {}:
                raise ValueError(
                    "Docker daemon preflight requires empty parameters."
                )

        elif parameters not in (None, {}):
            raise ValueError("Unexpected discovery parameters.")

        return self._request(
            operation,
            target_id,
            parameters=parameters,
            include_metadata=True,
        )
    def discover_with_freshness(
        self,
        operation,
        target_id,
        *,
        parameters=None,
        max_age_seconds=30,
    ):
        """Collect discovery evidence and assess its freshness."""

        evidence = self.discover_with_evidence(
            operation,
            target_id,
            parameters=parameters,
        )

        freshness = evaluate_freshness(
            evidence,
            max_age_seconds=max_age_seconds,
        )

        return {
            "evidence": evidence,
            "freshness": freshness,
            "execution_allowed": False,
        }
    def discover_for_planning(
        self,
        operation,
        target_id,
        *,
        parameters=None,
        max_age_seconds=30,
    ):
        """Collect and validate evidence for read-only planning."""

        evidence = self.discover_with_evidence(
            operation,
            target_id,
            parameters=parameters,
        )

        expected_target_path = None

        if operation == "discovery.swap_filesystem":
            expected_target_path = parameters["target_path"]

        gate = evaluate_evidence_gate(
            evidence,
            expected_operation=operation,
            expected_target_id=target_id,
            expected_target_path=expected_target_path,
            max_age_seconds=max_age_seconds,
        )

        return {
            "evidence": evidence,
            "gate": gate,
            "execution_allowed": False,
        }
    def discover_application_state(self, target_id, application_id):
        """Collect read-only package, binary and service observations."""

        allowed = {
            "git",
            "nginx",
            "docker",
            "docker_compose",
        }

        if application_id not in allowed:
            raise ValueError("Unsupported application ID")

        data = self._request(
            "discovery.application_state",
            target_id,
            parameters={"application_id": application_id},
        )

        if (
            data.get("schema_version") != "1.0"
            or data.get("application_id") != application_id
            or data.get("read_only") is not True
            or data.get("execution_allowed") is not False
        ):
            raise RuntimeError(
                "Invalid application-state discovery response"
            )

        return data

    def discover_application_inventory(self, target_id, application_id):
        """Collect validated read-only application package inventory."""

        allowed = {
            "git",
            "nginx",
            "docker",
            "docker_compose",
        }

        if not isinstance(application_id, str) or application_id not in allowed:
            raise ValueError("Unsupported application inventory target.")

        data = self._request(
            "discovery.application_inventory",
            target_id,
            parameters={"application_id": application_id},
        )

        if (
            data.get("schema_version") != "1.0"
            or data.get("application_id") != application_id
            or data.get("read_only") is not True
            or data.get("execution_allowed") is not False
            or not isinstance(data.get("packages"), list)
            or not isinstance(data.get("binary"), dict)
        ):
            raise RuntimeError("Invalid application inventory response.")

        return data
