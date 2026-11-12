
"""Build read-only explanations from Docker Compose runtime readiness."""

from copy import deepcopy

from bridge.applications.readiness.runtime_reconciliation import (
    READINESS_STATUSES,
    RUNTIME_STATUSES,
)


COMPOSE_RUNTIME_FINDINGS = {
    "PLUGIN_AVAILABLE": (
        "COMPOSE_PLUGIN_AVAILABLE",
        "Docker Compose plugin detected",
        "The Compose plugin was observed in the checked environment.",
        "Verify Docker Engine and project compatibility separately.",
        "COMPOSE_PLUGIN_VERIFIED",
    ),
    "PLUGIN_ABSENT": (
        "COMPOSE_PLUGIN_ABSENT",
        "Docker Compose plugin not found",
        "The Compose plugin was not discovered in the checked environment.",
        "Review plugin installation and executable availability.",
        "COMPOSE_PLUGIN_ABSENT",
    ),
    "UNVERIFIED": (
        "COMPOSE_PLUGIN_UNVERIFIED",
        "Docker Compose plugin status unverified",
        "The collected runtime evidence does not establish plugin availability.",
        "Review runtime discovery evidence before taking action.",
        "COMPOSE_RUNTIME_UNVERIFIED",
    ),
}


def explain_compose_readiness(readiness):
    """Explain an eight-source Compose assessment without mutation."""

    if not isinstance(readiness, dict):
        raise ValueError("Invalid Compose readiness result.")

    target = readiness.get("target_id")
    status = readiness.get("status")
    runtime = readiness.get("runtime_status")
    reasons = readiness.get("reasons")
    identities = readiness.get("evidence_request_ids")

    if (
        not isinstance(target, str)
        or not target.strip()
        or readiness.get("application_id") != "docker_compose"
        or status not in READINESS_STATUSES
        or runtime not in RUNTIME_STATUSES["docker_compose"]
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
        or not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason
               for reason in reasons)
    ):
        raise ValueError("Invalid Compose readiness identity or policy.")

    if (
        not isinstance(identities, dict)
        or len(identities) != 8
        or "discovery.application_runtime" not in identities
        or "discovery.docker_daemon_preflight" in identities
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in identities.items()
        )
        or len(set(identities.values())) != 8
    ):
        raise ValueError("Invalid Compose readiness evidence identities.")

    code, title, description, investigation, reason = (
        COMPOSE_RUNTIME_FINDINGS[runtime]
    )

    if reason not in reasons:
        raise ValueError("Compose runtime classification reason is missing.")

    findings = [
        {
            "category": "runtime",
            "code": code,
            "title": title,
            "description": description,
            "investigation": investigation,
            "execution_allowed": False,
        },
        {
            "category": "health",
            "code": "COMPOSE_EXECUTION_UNVERIFIED",
            "title": "Compose execution unverified",
            "description": (
                "Neither Docker Engine responsiveness nor Compose "
                "project execution has been verified."
            ),
            "investigation": (
                "Use a separately approved verification workflow "
                "before deploying workloads."
            ),
            "execution_allowed": False,
        },
    ]

    if status == "BLOCKED":
        findings.append({
            "category": "readiness",
            "code": "EXISTING_READINESS_BLOCKER",
            "title": "Application readiness blocked",
            "description": "Existing readiness evidence contains a blocker.",
            "investigation": "Review the original readiness reasons.",
            "execution_allowed": False,
        })

    return {
        "schema_version": "1.0",
        "check": "docker_compose_readiness_explanation",
        "target_id": target,
        "application_id": "docker_compose",
        "status": status,
        "runtime_status": runtime,
        "summary": (
            f"Docker Compose readiness is {status}. "
            "Docker Engine and Compose execution remain unverified."
        ),
        "findings": findings,
        "source_reasons": deepcopy(reasons),
        "evidence_request_ids": deepcopy(identities),
        "daemon_responsive": None,
        "planning_only": True,
        "execution_allowed": False,
    }
