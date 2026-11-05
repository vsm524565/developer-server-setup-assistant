
"""Build read-only explanations from reconciled Docker readiness."""

from bridge.applications.readiness.daemon_reconciliation import (
    PREFLIGHT_REASONS,
    READINESS_STATUSES,
)


RUNTIME_FINDINGS = {
    "CLI_AVAILABLE": (
        "DOCKER_CLI_AVAILABLE",
        "Docker CLI available",
        "The Docker command-line executable was observed.",
        "Confirm daemon accessibility separately before using Docker.",
    ),
    "CLI_ABSENT": (
        "DOCKER_CLI_ABSENT",
        "Docker CLI not found",
        "The Docker executable was not discovered in the checked environment.",
        "Review Docker installation and the executable search path.",
    ),
    "UNVERIFIED": (
        "DOCKER_CLI_UNVERIFIED",
        "Docker CLI status unverified",
        "The runtime discovery could not establish CLI availability.",
        "Review the runtime discovery evidence.",
    ),
}


DAEMON_FINDINGS = {
    "DAEMON_UNIT_NOT_FOUND": (
        "DOCKER_DAEMON_UNIT_NOT_FOUND",
        "Docker service unit not found",
        "systemd did not find the conventional docker.service unit.",
        "Review the installation method and any rootless or alternative service.",
    ),
    "DAEMON_UNIT_MASKED": (
        "DOCKER_DAEMON_UNIT_MASKED",
        "Docker service unit masked",
        "systemd reports that the Docker service unit is masked.",
        "Review why the service unit was masked before proposing changes.",
    ),
    "DAEMON_INACTIVE": (
        "DOCKER_DAEMON_INACTIVE",
        "Docker service inactive",
        "The Docker service unit is loaded but inactive.",
        "Review service state and available service logs.",
    ),
    "DAEMON_FAILED": (
        "DOCKER_DAEMON_FAILED",
        "Docker service failed",
        "systemd reports that the loaded Docker service failed.",
        "Review service failure logs and configuration.",
    ),
    "DAEMON_UNVERIFIED": (
        "DOCKER_DAEMON_UNVERIFIED",
        "Docker daemon state unverified",
        "Passive observations did not establish a supported daemon state.",
        "Review the collected service, process and socket evidence.",
    ),
    "PASSIVE_CANDIDATE": (
        "DOCKER_DAEMON_PASSIVE_CANDIDATE_UNPROBED",
        "Docker process and listener observed",
        "Passive observations found the Docker process and listening socket, "
        "but API responsiveness has not been verified.",
        "Treat daemon health as unknown until separately verified.",
    ),
}


def _finding(category, values):
    """Create a stable structured finding."""
    code, title, description, investigation = values

    return {
        "category": category,
        "code": code,
        "title": title,
        "description": description,
        "investigation": investigation,
        "execution_allowed": False,
    }


def explain_docker_readiness(readiness):
    """Explain a nine-source Docker decision without modifying it."""

    if not isinstance(readiness, dict):
        raise ValueError("Invalid Docker readiness result.")

    target = readiness.get("target_id")
    status = readiness.get("status")
    runtime = readiness.get("runtime_status")
    daemon = readiness.get("daemon_preflight_status")
    ids = readiness.get("evidence_request_ids")
    reasons = readiness.get("reasons")

    if (
        not isinstance(target, str)
        or not target.strip()
        or readiness.get("application_id") != "docker"
        or status not in READINESS_STATUSES
        or runtime not in RUNTIME_FINDINGS
        or daemon not in DAEMON_FINDINGS
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
        or not isinstance(reasons, list)
        or any(
            not isinstance(reason, str) or not reason
            for reason in reasons
        )
    ):
        raise ValueError("Invalid Docker readiness explanation contract.")

    if (
        not isinstance(ids, dict)
        or len(ids) != 9
        or "discovery.application_runtime" not in ids
        or "discovery.docker_daemon_preflight" not in ids
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in ids.items()
        )
        or len(set(ids.values())) != 9
    ):
        raise ValueError("Invalid Docker readiness evidence identities.")

    # Require the explanation to agree with the reconciled reason.
    daemon_reason = PREFLIGHT_REASONS[daemon]

    if daemon_reason not in reasons:
        raise ValueError("Daemon classification reason is missing.")

    findings = [
        _finding("runtime", RUNTIME_FINDINGS[runtime]),
        _finding("daemon", DAEMON_FINDINGS[daemon]),
        {
            "category": "health",
            "code": "DOCKER_API_HEALTH_UNVERIFIED",
            "title": "Docker API health unverified",
            "description": (
                "No Docker API responsiveness check was performed."
            ),
            "investigation": (
                "Use a separately approved verification workflow "
                "when active health checks become supported."
            ),
            "execution_allowed": False,
        },
    ]

    # Existing blockers remain visible in the explanation.
    if status == "BLOCKED":
        findings.append({
            "category": "readiness",
            "code": "EXISTING_READINESS_BLOCKER",
            "title": "Application readiness blocked",
            "description": (
                "The existing readiness decision contains a blocker."
            ),
            "investigation": "Review the original readiness reasons.",
            "execution_allowed": False,
        })

    return {
        "schema_version": "1.0",
        "check": "docker_readiness_explanation",
        "target_id": target,
        "application_id": "docker",
        "status": status,
        "runtime_status": runtime,
        "daemon_preflight_status": daemon,
        "summary": (
            f"Docker readiness is {status}. "
            "Docker API health has not been verified."
        ),
        "findings": findings,
        "source_reasons": list(reasons),
        "evidence_request_ids": dict(ids),
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }
