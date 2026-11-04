
"""Conservative runtime-aware application readiness reconciliation."""

from copy import deepcopy


DOCKER_APPLICATIONS = frozenset({"docker", "docker_compose"})

READINESS_STATUSES = frozenset({
    "READY_INSTALLED",
    "INSTALL_CANDIDATE_DRAFT",
    "REVIEW_REQUIRED",
    "BLOCKED",
})

RUNTIME_STATUSES = {
    "docker": frozenset({
        "CLI_AVAILABLE", "CLI_ABSENT", "UNVERIFIED"
    }),
    "docker_compose": frozenset({
        "PLUGIN_AVAILABLE",
        "PLUGIN_ABSENT",
        "UNVERIFIED",
    }),
}


def reconcile_runtime_readiness(readiness, runtime):
    """Reconcile accepted classifications without authorizing execution."""

    if not isinstance(readiness, dict):
        raise ValueError("Invalid application readiness.")

    if not isinstance(runtime, dict):
        raise ValueError("Invalid application runtime.")

    application = readiness.get("application_id")
    target = readiness.get("target_id")
    status = readiness.get("status")
    runtime_status = runtime.get("status")

    if (
        application not in DOCKER_APPLICATIONS
        or not isinstance(target, str)
        or not target
        or status not in READINESS_STATUSES
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or runtime.get("application_id") != application
        or runtime.get("target_id") != target
        or runtime_status not in RUNTIME_STATUSES[application]
        or runtime.get("planning_only") is not True
        or runtime.get("execution_allowed") is not False
        or runtime.get("daemon_responsive") is not None
    ):
        raise ValueError("Invalid readiness/runtime identity or policy.")

    request_id = runtime.get("evidence_request_id")

    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Missing runtime evidence identity.")

    previous_ids = readiness.get("evidence_request_ids")

    if not isinstance(previous_ids, dict):
        raise ValueError("Missing readiness evidence identities.")

    if not previous_ids or any(
        not isinstance(value, str) or not value
        for value in previous_ids.values()
    ):
        raise ValueError("Invalid readiness evidence identities.")

    if (
        len(set(previous_ids.values())) != len(previous_ids)
        or request_id in previous_ids.values()
        or "discovery.application_runtime" in previous_ids
    ):
        raise ValueError("Duplicate or conflicting evidence identity.")

    result = deepcopy(readiness)
    reasons = list(result.get("reasons", []))

    if not all(isinstance(reason, str) for reason in reasons):
        raise ValueError("Invalid readiness reasons.")

    verified = runtime_status in {
        "CLI_AVAILABLE",
        "PLUGIN_AVAILABLE",
    }
    # No current Docker/Compose runtime observation proves full health.
    # Preserve blockers and downgrade unverified ready applications.
    contradicted = (
        status == "READY_INSTALLED"
        or (
            status == "INSTALL_CANDIDATE_DRAFT"
            and verified
        )
    )

    if contradicted:
        result["status"] = "REVIEW_REQUIRED"
        reasons.append("RUNTIME_READINESS_CONTRADICTION")

    if application == "docker":
        if runtime_status == "CLI_AVAILABLE":
            reasons.append("DOCKER_CLI_VERIFIED_DAEMON_UNPROBED")
        elif runtime_status == "CLI_ABSENT":
            reasons.append("DOCKER_CLI_ABSENT")
        else:
            reasons.append("DOCKER_RUNTIME_UNVERIFIED")
    else:
        if runtime_status == "PLUGIN_AVAILABLE":
            reasons.append("COMPOSE_PLUGIN_VERIFIED")
        elif runtime_status == "PLUGIN_ABSENT":
            reasons.append("COMPOSE_PLUGIN_ABSENT")
        else:
            reasons.append("COMPOSE_RUNTIME_UNVERIFIED")


    result["reasons"] = reasons
    result["runtime_status"] = runtime_status
    result["runtime_reasons"] = list(runtime.get("reasons", []))
    result["runtime_service_state"] = runtime.get("service_state")
    result["runtime_socket_state"] = runtime.get("socket_state")
    result["runtime_plugin_state"] = runtime.get("plugin_state")
    result["daemon_responsive"] = None

    result["evidence_request_ids"][
        "discovery.application_runtime"
    ] = request_id

    result["planning_only"] = True
    result["execution_allowed"] = False

    return result

