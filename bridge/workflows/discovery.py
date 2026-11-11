
"""Offline discovery and validation of DSSA workflow capabilities."""

from copy import deepcopy

from bridge.workflows.contracts import SCHEMA_VERSION
from bridge.workflows.handlers import resolve_workflow_handler
from bridge.workflows.registry import build_workflow_registry


CAPABILITY_FIELDS = frozenset({
    "application_id",
    "workflow",
    "target_kind",
    "planning_only",
    "execution_allowed",
})


def validate_capability_report(report):
    """Validate the complete offline capability discovery contract."""

    if (
        not isinstance(report, dict)
        or set(report) != {
            "schema_version",
            "check",
            "capabilities",
        }
        or report.get("schema_version") != SCHEMA_VERSION
        or report.get("check") != "workflow_capabilities"
        or not isinstance(report.get("capabilities"), list)
    ):
        raise ValueError("Invalid workflow capability report.")

    seen = set()

    for capability in report["capabilities"]:
        if (
            not isinstance(capability, dict)
            or set(capability) != CAPABILITY_FIELDS
        ):
            raise ValueError("Invalid capability declaration.")

        application_id = capability["application_id"]
        workflow = capability["workflow"]
        target_kind = capability["target_kind"]

        if any(
            not isinstance(value, str) or not value
            for value in (
                application_id,
                workflow,
                target_kind,
            )
        ):
            raise ValueError("Invalid capability identity.")

        if (
            capability["planning_only"] is not True
            or capability["execution_allowed"] is not False
        ):
            raise ValueError("Unsafe workflow capability.")

        key = (application_id, workflow, target_kind)

        if key in seen:
            raise ValueError("Duplicate workflow capability.")

        seen.add(key)

        if resolve_workflow_handler(*key) is None:
            raise ValueError(
                "Capability has no implemented workflow handler."
            )


    # Validate the original declaration order, not set iteration order.
    keys = [
        (
            capability["application_id"],
            capability["workflow"],
            capability["target_kind"],
        )
        for capability in report["capabilities"]
    ]

    if keys != sorted(keys):
        raise ValueError("Capability report is not sorted.")


    return deepcopy(report)


def discover_workflow_capabilities():
    """Build a validated offline report without backend communication."""

    registry = build_workflow_registry()

    capabilities = [
        deepcopy(capability)
        for capability in registry.list_capabilities()
    ]

    # Emit deterministic order even if a registry changes its iteration order.
    capabilities.sort(
        key=lambda capability: (
            capability["application_id"],
            capability["workflow"],
            capability["target_kind"],
        )
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "check": "workflow_capabilities",
        "capabilities": capabilities,
    }

    return validate_capability_report(report)
