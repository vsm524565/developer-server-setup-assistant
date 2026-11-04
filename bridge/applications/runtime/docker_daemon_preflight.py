
"""Interpret passive Docker daemon observations without API probing."""

import re
from bridge.evidence.gate import evaluate_evidence_gate

SERVICE_STATES = {
    "active", "inactive", "failed",
    "activating", "deactivating", "unknown",
}

PREFLIGHT_CLASSIFICATIONS = {
    "PASSIVE_CANDIDATE": "PASSIVE_CANDIDATE",
    "SERVICE_INACTIVE": "DAEMON_INACTIVE",
    "SERVICE_FAILED": "DAEMON_FAILED",
    "UNVERIFIED": "DAEMON_UNVERIFIED",
}

REASONS = {
    "PASSIVE_CANDIDATE": "DAEMON_PROCESS_AND_SOCKET_OBSERVED",
    "SERVICE_INACTIVE": "DOCKER_SERVICE_INACTIVE",
    "SERVICE_FAILED": "DOCKER_SERVICE_FAILED",
    "UNVERIFIED": "INSUFFICIENT_PASSIVE_EVIDENCE",
}


def classify_docker_daemon_preflight(data):
    """Validate and classify passive Docker service/socket evidence."""

    expected_keys = {
        "schema_version", "check", "service", "socket",
        "status", "reasons", "probe_attempted",
        "daemon_responsive", "read_only", "execution_allowed",
    }

    if (
        not isinstance(data, dict)
        or set(data) != expected_keys
        or data.get("schema_version") != "1.0"
        or data.get("check") != "docker_daemon_preflight"
        or data.get("probe_attempted") is not False
        or data.get("daemon_responsive") is not None
        or data.get("read_only") is not True
        or data.get("execution_allowed") is not False
    ):
        raise ValueError("Invalid Docker preflight identity or policy.")

    service = data["service"]
    socket = data["socket"]
    status = data["status"]

    if (
        not isinstance(service, dict)
        or set(service) != {
            "state", "main_pid", "process_verified"
        }
        or service["state"] not in SERVICE_STATES
        or type(service["main_pid"]) is not int
        or service["main_pid"] < 0
        or type(service["process_verified"]) is not bool
    ):
        raise ValueError("Invalid Docker service evidence.")

    if (
        service["process_verified"]
        and service["main_pid"] == 0
    ):
        raise ValueError("Verified Docker process requires a PID.")

    if (
        not isinstance(socket, dict)
        or set(socket) != {
            "unit_state", "path", "present",
            "permissions", "owner", "group", "listener_state"
        }
        or socket["unit_state"] not in SERVICE_STATES
        or socket["path"] != "/run/docker.sock"
        or type(socket["present"]) is not bool
        or socket["listener_state"] not in {
            "present", "absent", "unknown"
        }
    ):
        raise ValueError("Invalid Docker socket evidence.")

    for field in ("owner", "group"):
        value = socket[field]
        if value is not None and (
            not isinstance(value, str) or not value
        ):
            raise ValueError("Invalid socket ownership evidence.")

    permissions = socket["permissions"]
    if permissions is not None and (
        not isinstance(permissions, str)
        or re.fullmatch(r"[0-7]{3,4}", permissions) is None
    ):
        raise ValueError("Invalid Docker socket permissions.")

    if not socket["present"] and (
        permissions is not None
        or socket["owner"] is not None
        or socket["group"] is not None
        or socket["listener_state"] == "present"
    ):
        raise ValueError("Contradictory Docker socket evidence.")

    if service["state"] == "inactive":
        expected_status = "SERVICE_INACTIVE"
    elif service["state"] == "failed":
        expected_status = "SERVICE_FAILED"
    elif (
        service["state"] == "active"
        and service["process_verified"]
        and socket["present"]
        and socket["listener_state"] == "present"
    ):
        expected_status = "PASSIVE_CANDIDATE"
    else:
        expected_status = "UNVERIFIED"

    if status != expected_status:
        raise ValueError("Contradictory Docker preflight status.")

    if data["reasons"] != [REASONS[status]]:
        raise ValueError("Invalid Docker preflight reason.")

    return {
        "schema_version": "1.0",
        "check": "docker_daemon_preflight_classification",
        "status": PREFLIGHT_CLASSIFICATIONS[status],
        "source_status": status,
        "service_state": service["state"],
        "main_pid": service["main_pid"],
        "process_verified": service["process_verified"],
        "socket_state": socket["unit_state"],
        "socket_present": socket["present"],
        "socket_listener_state": socket["listener_state"],
        "daemon_responsive": None,
        "probe_attempted": False,
        "reasons": list(data["reasons"]),
        "planning_only": True,
        "execution_allowed": False,
    }

def classify_docker_daemon_preflight_evidence(
    planning_result,
    *,
    expected_target_id,
    max_age_seconds=30,
):
    """Classify independently validated, fresh passive Docker evidence."""

    if not isinstance(planning_result, dict):
        raise ValueError("Invalid Docker preflight planning evidence.")

    if (
        not isinstance(expected_target_id, str)
        or not expected_target_id
    ):
        raise ValueError("Invalid Docker preflight target ID.")

    gate = planning_result.get("gate")
    evidence = planning_result.get("evidence")

    if (
        not isinstance(gate, dict)
        or gate.get("status") != "ACCEPTED"
        or gate.get("planning_allowed") is not True
        or gate.get("execution_allowed") is not False
        or planning_result.get("execution_allowed") is not False
        or not isinstance(evidence, dict)
        or evidence.get("operation")
            != "discovery.docker_daemon_preflight"
        or evidence.get("target_id") != expected_target_id
        or evidence.get("parameters") != {}
        or evidence.get("execution_allowed") is not False
    ):
        raise ValueError(
            "Docker preflight evidence identity or gate rejected."
        )

    independent_gate = evaluate_evidence_gate(
        evidence,
        expected_operation="discovery.docker_daemon_preflight",
        expected_target_id=expected_target_id,
        max_age_seconds=max_age_seconds,
    )

    if (
        not isinstance(independent_gate, dict)
        or independent_gate.get("status") != "ACCEPTED"
        or independent_gate.get("planning_allowed") is not True
        or independent_gate.get("execution_allowed") is not False
    ):
        raise ValueError(
            "Docker preflight evidence failed independent validation."
        )

    request_id = evidence.get("request_id")

    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Missing Docker preflight evidence request ID.")

    result = classify_docker_daemon_preflight(
        evidence.get("data")
    )

    result["target_id"] = expected_target_id
    result["evidence_request_id"] = request_id

    return result
