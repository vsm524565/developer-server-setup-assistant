
"""Contracts for DSSA's read-only application workflows."""

import re

SCHEMA_VERSION = "1.0"

SUPPORTED_WORKFLOWS = frozenset({
    "readiness_assessment",
})

SUPPORTED_APPLICATIONS = frozenset({
    "docker",
})

TARGET_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
)


def validate_workflow_request(request):
    """Validate and normalize a read-only inventory workflow request."""

    # Reject malformed requests before accessing individual fields.
    if not isinstance(request, dict):
        raise ValueError("Workflow request must be an object.")

    required_fields = {
        "schema_version",
        "workflow",
        "target",
        "application_id",
        "max_age_seconds",
    }

    if set(request) != required_fields:
        raise ValueError("Invalid workflow request fields.")

    if request["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported workflow schema.")

    workflow = request["workflow"]
    application_id = request["application_id"]

    if (
        not isinstance(workflow, str)
        or workflow not in SUPPORTED_WORKFLOWS
    ):
        raise ValueError("Unsupported workflow.")

    if (
        not isinstance(application_id, str)
        or application_id not in SUPPORTED_APPLICATIONS
    ):
        raise ValueError("Unsupported application.")

    target = request["target"]

    if (
        not isinstance(target, dict)
        or set(target) != {"kind", "id"}
        or target.get("kind") != "inventory"
    ):
        raise ValueError("Invalid workflow target contract.")

    target_id = target["id"]

    if (
        not isinstance(target_id, str)
        or TARGET_ID_PATTERN.fullmatch(target_id) is None
    ):
        raise ValueError("Invalid inventory target identifier.")

    max_age_seconds = request["max_age_seconds"]

    if (
        type(max_age_seconds) not in (int, float)
        or not 0 < max_age_seconds <= 300
    ):
        raise ValueError("Invalid evidence freshness window.")

    return {
        "schema_version": SCHEMA_VERSION,
        "workflow": request["workflow"],
        "target": {
            "kind": "inventory",
            "id": target_id,
        },
        "application_id": request["application_id"],
        "max_age_seconds": max_age_seconds,
    }
