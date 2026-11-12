
"""Coordinate DSSA's implemented read-only application workflows."""

from copy import deepcopy

from bridge.workflows.contracts import (
    SCHEMA_VERSION,
    validate_workflow_request,
)
from bridge.applications.readiness.daemon_coordinator import (
    assess_daemon_aware_readiness,
)
from bridge.applications.readiness.runtime_coordinator import (
    assess_runtime_aware_readiness,
)
from bridge.applications.readiness.readiness_explanation import (
    explain_docker_readiness,
)
from bridge.applications.readiness.compose_explanation import (
    explain_compose_readiness,
)
from bridge.applications.readiness.report_renderer import (
    validate_explanation,
)
from bridge.applications.readiness.compose_report import (
    validate_compose_explanation,
)
from bridge.workflows.registry import build_workflow_registry
from bridge.workflows.handlers import (
    DOCKER_READINESS_HANDLER,
    COMPOSE_READINESS_HANDLER,
    resolve_workflow_handler,
)


def assess_application_workflow(backend, request):
    """Dispatch approved read-only workflows through validated evidence."""

    validated = validate_workflow_request(request)

    target_id = validated["target"]["id"]
    application_id = validated["application_id"]
    max_age_seconds = validated["max_age_seconds"]

    # Validate the registered capability before backend discovery.
    registry = build_workflow_registry()

    capability = registry.get(
        application_id,
        validated["workflow"],
        validated["target"]["kind"],
    )

    if (
        capability.get("application_id") != application_id
        or capability.get("workflow") != validated["workflow"]
        or capability.get("target_kind") != validated["target"]["kind"]
        or capability.get("planning_only") is not True
        or capability.get("execution_allowed") is not False
    ):
        raise RuntimeError(
            "Workflow capability violates the read-only policy."
        )

    # Only explicit implemented handlers may reach discovery.
    handler_id = resolve_workflow_handler(
        application_id,
        validated["workflow"],
        validated["target"]["kind"],
    )

    if handler_id == DOCKER_READINESS_HANDLER:
        if application_id != "docker":
            raise ValueError("Docker handler identity mismatch.")

        readiness = assess_daemon_aware_readiness(
            backend,
            target_id,
            application_id,
            max_age_seconds=max_age_seconds,
        )

    elif handler_id == COMPOSE_READINESS_HANDLER:
        if application_id != "docker_compose":
            raise ValueError("Compose handler identity mismatch.")

        readiness = assess_runtime_aware_readiness(
            backend,
            target_id,
            application_id,
            max_age_seconds=max_age_seconds,
        )

    else:
        raise ValueError(
            "No implemented handler for this workflow capability."
        )

    # Both pipelines must preserve identity and deny execution.
    if (
        not isinstance(readiness, dict)
        or readiness.get("target_id") != target_id
        or readiness.get("application_id") != application_id
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Readiness result violates workflow policy.")

    # Apply the correct explanation and validation contract.
    if handler_id == DOCKER_READINESS_HANDLER:
        explanation = explain_docker_readiness(readiness)
        validate_explanation(explanation)

    else:
        explanation = explain_compose_readiness(readiness)
        validate_compose_explanation(explanation)

    # Cross-check the explanation against the evidence decision.
    if (
        not isinstance(explanation, dict)
        or explanation.get("target_id") != target_id
        or explanation.get("application_id") != application_id
        or explanation.get("status") != readiness.get("status")
        or explanation.get("evidence_request_ids")
            != readiness.get("evidence_request_ids")
        or explanation.get("source_reasons")
            != readiness.get("reasons")
        or explanation.get("runtime_status")
            != readiness.get("runtime_status")
        or explanation.get("planning_only") is not True
        or explanation.get("execution_allowed") is not False
        or explanation.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Workflow explanation contradicts readiness.")

    # Docker Engine additionally requires matching daemon classification.
    if handler_id == DOCKER_READINESS_HANDLER:
        if (
            explanation.get("daemon_preflight_status")
                != readiness.get("daemon_preflight_status")
        ):
            raise RuntimeError(
                "Docker daemon explanation contradicts readiness."
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "check": "application_workflow_result",
        "workflow": validated["workflow"],
        "target": deepcopy(validated["target"]),
        "application_id": application_id,
        "status": readiness["status"],
        "result": deepcopy(explanation),
        "planning_only": True,
        "execution_allowed": False,
    }
