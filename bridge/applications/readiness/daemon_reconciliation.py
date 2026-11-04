
"""Conservative Docker readiness reconciliation using passive daemon evidence."""

from copy import deepcopy


PREFLIGHT_REASONS = {
    "DAEMON_UNIT_NOT_FOUND": "DOCKER_DAEMON_UNIT_NOT_FOUND",
    "DAEMON_UNIT_MASKED": "DOCKER_DAEMON_UNIT_MASKED",
    "DAEMON_INACTIVE": "DOCKER_DAEMON_INACTIVE",
    "DAEMON_FAILED": "DOCKER_DAEMON_FAILED",
    "DAEMON_UNVERIFIED": "DOCKER_DAEMON_UNVERIFIED",
    "PASSIVE_CANDIDATE": "DOCKER_DAEMON_PASSIVE_CANDIDATE_UNPROBED",
}

SOURCE_STATUSES = {
    "DAEMON_UNIT_NOT_FOUND": "SERVICE_NOT_FOUND",
    "DAEMON_UNIT_MASKED": "SERVICE_MASKED",
    "DAEMON_INACTIVE": "SERVICE_INACTIVE",
    "DAEMON_FAILED": "SERVICE_FAILED",
    "DAEMON_UNVERIFIED": "UNVERIFIED",
    "PASSIVE_CANDIDATE": "PASSIVE_CANDIDATE",
}

SOURCE_REASONS = {
    "DAEMON_UNIT_NOT_FOUND": "DOCKER_SERVICE_UNIT_NOT_FOUND",
    "DAEMON_UNIT_MASKED": "DOCKER_SERVICE_UNIT_MASKED",
    "DAEMON_INACTIVE": "DOCKER_SERVICE_INACTIVE",
    "DAEMON_FAILED": "DOCKER_SERVICE_FAILED",
    "DAEMON_UNVERIFIED": "INSUFFICIENT_PASSIVE_EVIDENCE",
    "PASSIVE_CANDIDATE": "DAEMON_PROCESS_AND_SOCKET_OBSERVED",
}

READINESS_STATUSES = frozenset({
    "READY_INSTALLED",
    "INSTALL_CANDIDATE_DRAFT",
    "REVIEW_REQUIRED",
    "BLOCKED",
})


def reconcile_daemon_readiness(readiness, daemon):
    """Join classified daemon evidence with runtime-aware Docker readiness."""

    if not isinstance(readiness, dict) or not isinstance(daemon, dict):
        raise ValueError("Invalid Docker reconciliation inputs.")

    target = readiness.get("target_id")
    status = readiness.get("status")
    daemon_status = daemon.get("status")
    request_ids = readiness.get("evidence_request_ids")
    daemon_request_id = daemon.get("evidence_request_id")

    if (
        not isinstance(target, str)
        or not target.strip()
        or readiness.get("application_id") != "docker"
        or status not in READINESS_STATUSES
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
        or readiness.get("runtime_status")
            not in {"CLI_AVAILABLE", "CLI_ABSENT", "UNVERIFIED"}
    ):
        raise ValueError("Invalid runtime-aware Docker readiness.")

    if (
        not isinstance(request_ids, dict)
        or len(request_ids) != 8
        or "discovery.application_runtime" not in request_ids
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in request_ids.items()
        )
        or len(set(request_ids.values())) != 8
        or "discovery.docker_daemon_preflight" in request_ids
    ):
        raise ValueError("Invalid existing evidence identities.")

    if (
        daemon.get("target_id") != target
        or daemon.get("check") != "docker_daemon_preflight_classification"
        or daemon_status not in PREFLIGHT_REASONS
        or daemon.get("source_status") != SOURCE_STATUSES[daemon_status]
        or daemon.get("planning_only") is not True
        or daemon.get("execution_allowed") is not False
        or daemon.get("daemon_responsive") is not None
        or daemon.get("probe_attempted") is not False
        or not isinstance(daemon_request_id, str)
        or not daemon_request_id
        or daemon_request_id in request_ids.values()
    ):
        raise ValueError("Invalid daemon evidence identity or policy.")

    # The preflight interpreter has already validated the raw observation.
    # Require the classified result to retain its essential fields.

    daemon_reasons = daemon.get("reasons")

    if (
        not isinstance(daemon_reasons, list)
        or daemon_reasons != [SOURCE_REASONS[daemon_status]]
        or any(
            not isinstance(reason, str) or not reason
            for reason in daemon_reasons
        )
        or daemon.get("process_verified") not in (True, False)
        or type(daemon.get("process_verified")) is not bool
        or type(daemon.get("socket_present")) is not bool
        or daemon.get("socket_listener_state")
            not in {"present", "absent", "unknown"}
        or (
            daemon.get("socket_present") is False
            and daemon.get("socket_listener_state") == "present"
        )
    ):
        raise ValueError("Invalid classified daemon observation.")

    load_state = daemon.get("service_load_state")
    service_state = daemon.get("service_state")

    expected_states = {
        "DAEMON_UNIT_NOT_FOUND": ("not-found", "inactive"),
        "DAEMON_UNIT_MASKED": ("masked", "inactive"),
        "DAEMON_INACTIVE": ("loaded", "inactive"),
        "DAEMON_FAILED": ("loaded", "failed"),
    }

    if daemon_status in expected_states:
        if (load_state, service_state) != expected_states[daemon_status]:
            raise ValueError("Contradictory daemon service classification.")

    elif daemon_status == "PASSIVE_CANDIDATE":
        if (
            load_state != "loaded"
            or service_state != "active"
            or daemon.get("process_verified") is not True
            or daemon.get("socket_present") is not True
            or daemon.get("socket_listener_state") != "present"
        ):
            raise ValueError("Contradictory passive daemon candidate.")

    elif (
        load_state not in {
            "loaded", "not-found", "masked",
            "error", "bad-setting", "unknown",
        }
        or service_state not in {
            "active", "inactive", "failed",
            "activating", "deactivating", "unknown",
        }
        or (load_state, service_state) in expected_states.values()
        or (
            load_state == "loaded"
            and service_state == "active"
            and daemon.get("process_verified") is True
            and daemon.get("socket_present") is True
            and daemon.get("socket_listener_state") == "present"
        )
    ):
        raise ValueError("Invalid unverified daemon classification.")

    result = deepcopy(readiness)
    reasons = result.get("reasons")

    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) for reason in reasons)
    ):
        raise ValueError("Invalid readiness reasons.")

    # Never upgrade based on passive observations.
    if status == "READY_INSTALLED":
        result["status"] = "REVIEW_REQUIRED"
        reasons.append("DAEMON_HEALTH_NOT_VERIFIED")

    elif (
        status == "INSTALL_CANDIDATE_DRAFT"
        and daemon_status == "PASSIVE_CANDIDATE"
    ):
        result["status"] = "REVIEW_REQUIRED"
        reasons.append("DAEMON_INSTALLATION_STATE_CONTRADICTION")

    reasons.append(PREFLIGHT_REASONS[daemon_status])

    result["daemon_preflight_status"] = daemon_status
    result["daemon_preflight_reasons"] = list(daemon.get("reasons", []))
    result["daemon_service_load_state"] = load_state
    result["daemon_service_state"] = service_state
    result["daemon_process_verified"] = daemon.get("process_verified")
    result["daemon_socket_present"] = daemon.get("socket_present")
    result["daemon_socket_listener_state"] = daemon.get(
        "socket_listener_state"
    )
    result["daemon_responsive"] = None

    result["evidence_request_ids"][
        "discovery.docker_daemon_preflight"
    ] = daemon_request_id

    result["planning_only"] = True
    result["execution_allowed"] = False

    return result
