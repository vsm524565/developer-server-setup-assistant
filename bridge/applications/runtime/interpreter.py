
"""Interpret read-only Docker runtime observations."""

from bridge.evidence.gate import evaluate_evidence_gate


APPLICATIONS = frozenset({"docker", "docker_compose"})

SYSTEMD_STATES = frozenset({
    "active",
    "inactive",
    "failed",
    "activating",
    "deactivating",
    "unknown",
    "not_applicable",
})


def classify_application_runtime(data, *, expected_application_id):
    """Classify Docker runtime observations without probing the daemon."""

    if expected_application_id not in APPLICATIONS:
        raise ValueError("Unsupported runtime application.")

    if not isinstance(data, dict):
        raise ValueError("Runtime observations must be an object.")

    if (
        set(data) != {
            "schema_version",
            "application_id",
            "cli",
            "service",
            "socket",
            "plugin",
            "daemon_responsive",
            "read_only",
            "execution_allowed",
        }
        or data.get("schema_version") != "1.0"
        or data.get("application_id") != expected_application_id
        or data.get("read_only") is not True
        or data.get("execution_allowed") is not False
        or data.get("daemon_responsive") is not None
    ):
        raise ValueError("Invalid runtime identity or execution policy.")

    cli = data.get("cli")
    service = data.get("service")
    socket = data.get("socket")
    plugin = data.get("plugin")

    if (
        not isinstance(cli, dict)
        or set(cli) != {"path", "status", "version_output"}
        or not isinstance(service, dict)
        or set(service) != {"state"}
        or not isinstance(socket, dict)
        or set(socket) != {"state"}
        or not isinstance(plugin, dict)
        or set(plugin) != {"state"}
    ):
        raise ValueError("Invalid runtime observation structure.")

    cli_status = cli["status"]
    cli_path = cli["path"]
    version = cli["version_output"]
    service_state = service["state"]
    socket_state = socket["state"]
    plugin_state = plugin["state"]

    if (
        cli_status not in ("available", "absent", "unknown")
        or service_state not in SYSTEMD_STATES
        or socket_state not in SYSTEMD_STATES
        or plugin_state not in (
            "available", "unknown", "not_applicable"
        )
    ):
        raise ValueError("Invalid runtime observation values.")

    if cli_path is None:
        if cli_status != "absent" or version is not None:
            raise ValueError("Inconsistent absent CLI observation.")
    else:
        if (
            not isinstance(cli_path, str)
            or not cli_path.startswith("/")
            or "\n" in cli_path
            or "\r" in cli_path
        ):
            raise ValueError("Invalid Docker CLI path.")

        if cli_status == "absent":
            raise ValueError("CLI cannot be absent with a path.")

        if cli_status == "available":
            if (
                not isinstance(version, str)
                or not 0 < len(version) <= 256
            ):
                raise ValueError("Invalid runtime version output.")
        elif version is not None:
            raise ValueError("Unverified CLI cannot have a version.")

    reasons = []

    if expected_application_id == "docker":
        if (
            plugin_state != "not_applicable"
            or service_state == "not_applicable"
            or socket_state == "not_applicable"
        ):
            raise ValueError("Invalid Docker Engine runtime fields.")

        if cli_status == "available":
            status = "CLI_AVAILABLE"
            reasons.append("DOCKER_CLI_VERSION_VERIFIED")
        elif cli_status == "absent":
            status = "CLI_ABSENT"
            reasons.append("DOCKER_CLI_NOT_FOUND")
        else:
            status = "UNVERIFIED"
            reasons.append("DOCKER_CLI_VERSION_UNVERIFIED")

        if service_state == "active":
            reasons.append("DOCKER_SERVICE_ACTIVE")
        elif service_state == "failed":
            reasons.append("DOCKER_SERVICE_FAILED")

        if socket_state == "active":
            reasons.append("DOCKER_SOCKET_ACTIVE")

        reasons.append("DOCKER_DAEMON_NOT_PROBED")

    else:
        if (
            service_state != "not_applicable"
            or socket_state != "not_applicable"
        ):
            raise ValueError("Invalid Compose runtime fields.")

        if plugin_state == "available":
            if cli_status != "available":
                raise ValueError("Compose plugin/CLI mismatch.")

            status = "PLUGIN_AVAILABLE"
            reasons.append("COMPOSE_VERSION_VERIFIED")

        elif plugin_state == "unknown":
            if cli_status == "available":
                raise ValueError("Unverified Compose cannot be available.")

            status = "UNVERIFIED"

            if cli_status == "absent":
                reasons.append("DOCKER_CLI_NOT_FOUND")
            else:
                reasons.append("COMPOSE_PLUGIN_NOT_VERIFIED")
        else:
            raise ValueError("Invalid Compose plugin state.")

    return {
        "schema_version": "1.0",
        "check": "application_runtime_classification",
        "application_id": expected_application_id,
        "status": status,
        "cli_path": cli_path,
        "version_output": version,
        "service_state": service_state,
        "socket_state": socket_state,
        "plugin_state": plugin_state,
        "daemon_responsive": None,
        "reasons": reasons,
        "planning_only": True,
        "execution_allowed": False,
    }


def classify_application_runtime_evidence(
    planning_result,
    *,
    expected_application_id,
    expected_target_id,
    max_age_seconds=30,
):
    """Require matching, fresh evidence before runtime classification."""

    if not isinstance(planning_result, dict):
        raise ValueError("Invalid runtime planning evidence.")

    gate = planning_result.get("gate")
    evidence = planning_result.get("evidence")

    if (
        not isinstance(gate, dict)
        or gate.get("status") != "ACCEPTED"
        or gate.get("planning_allowed") is not True
        or gate.get("execution_allowed") is not False
        or planning_result.get("execution_allowed") is not False
        or not isinstance(evidence, dict)
        or evidence.get("operation") != "discovery.application_runtime"
        or evidence.get("target_id") != expected_target_id
        or evidence.get("parameters") != {
            "application_id": expected_application_id
        }
        or evidence.get("execution_allowed") is not False
    ):
        raise ValueError("Runtime evidence identity or gate rejected.")

    independent_gate = evaluate_evidence_gate(
        evidence,
        expected_operation="discovery.application_runtime",
        expected_target_id=expected_target_id,
        max_age_seconds=max_age_seconds,
    )

    if independent_gate.get("status") != "ACCEPTED":
        raise ValueError("Runtime evidence failed independent validation.")

    request_id = evidence.get("request_id")

    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Missing runtime evidence request ID.")

    result = classify_application_runtime(
        evidence.get("data"),
        expected_application_id=expected_application_id,
    )

    result["target_id"] = expected_target_id
    result["evidence_request_id"] = request_id

    return result
