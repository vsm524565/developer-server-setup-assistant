
"""Coordinate DSSA's read-only application workflows."""

from copy import deepcopy

from bridge.workflows.contracts import (
    SCHEMA_VERSION,
    validate_workflow_request,
)
from bridge.applications.readiness.daemon_coordinator import (
    assess_daemon_aware_readiness,
)
from bridge.applications.readiness.readiness_explanation import (
    explain_docker_readiness,
)
from bridge.applications.readiness.report_renderer import (
    validate_explanation,
)
from bridge.workflows.registry import build_workflow_registry
from bridge.workflows.handlers import (
    DOCKER_READINESS_HANDLER,
    resolve_workflow_handler,
)

def assess_application_workflow(backend, request):
    """Run an approved workflow through existing evidence-gated services."""

    validated = validate_workflow_request(request)

    target_id = validated["target"]["id"]
    application_id = validated["application_id"]
    max_age_seconds = validated["max_age_seconds"]

    # Require an explicitly registered read-only workflow capability.
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

    # Resolve a real implementation before backend discovery.
    handler_id = resolve_workflow_handler(
        application_id,
        validated["workflow"],
        validated["target"]["kind"],
    )

    if handler_id != DOCKER_READINESS_HANDLER:
        raise ValueError(
            "No implemented handler for this workflow capability."
        )

    # The existing coordinator owns discovery and evidence validation.
    readiness = assess_daemon_aware_readiness(
        backend,
        target_id,
        application_id,
        max_age_seconds=max_age_seconds,
    )

    if not isinstance(readiness, dict):
        raise RuntimeError("Invalid readiness assessment result.")

    if (
        readiness.get("target_id") != target_id
        or readiness.get("application_id") != application_id
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Readiness result violates workflow policy.")

    # Create an explanation without modifying the readiness result.
    explanation = explain_docker_readiness(readiness)

    # Revalidate the explanation before exposing it to callers.
    validate_explanation(explanation)

    if (
        explanation["target_id"] != target_id
        or explanation["application_id"] != application_id
        or explanation["status"] != readiness["status"]
        or explanation["evidence_request_ids"]
            != readiness["evidence_request_ids"]
        or explanation["source_reasons"]
            != readiness["reasons"]
        or explanation["runtime_status"]
            != readiness["runtime_status"]
        or explanation["daemon_preflight_status"]
            != readiness["daemon_preflight_status"]
    ):
        raise RuntimeError("Workflow explanation contradicts readiness.")

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
