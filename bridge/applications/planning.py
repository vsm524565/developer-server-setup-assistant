"""Read-only application assessment integration.

Connects catalog definitions to the existing Linux evidence pipeline.
Does not invoke the installation backend or grant execution approval.
"""

from bridge.applications.catalog import build_application_catalog
from bridge.applications.evidence_coordinator import (
    collect_application_evidence,
    map_application_evidence,
)
from bridge.applications.plan_binding import evaluate_application_plan


def assess_application(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Assess a registered application using fresh server evidence."""

    registry = build_application_catalog()
    definition = registry.get(application_id)

    metadata = definition.metadata
    required_operations = metadata.get("discovery_required")

    if (
        not isinstance(required_operations, (list, tuple))
        or "discovery.system" not in required_operations
    ):
        raise ValueError(
            "Application has no valid system discovery declaration"
        )

    # Determine which port observations the application requests.
    ports = set()

    for rule in definition.conflicts:
        prefix = "tcp_port."
        suffix = ".listening"

        if (
            rule.conflict_id.startswith(prefix)
            and rule.conflict_id.endswith(suffix)
        ):
            value = rule.conflict_id[
                len(prefix):-len(suffix)
            ]

            if not value.isdecimal():
                raise ValueError("Invalid port conflict declaration")

            port = int(value)

            if not 1 <= port <= 65535:
                raise ValueError("Invalid port number")

            ports.add(port)

    collection = collect_application_evidence(
        backend,
        target_id,
        operations=required_operations,
        max_age_seconds=max_age_seconds,
    )

    mapped = map_application_evidence(
        collection,
        ports=tuple(sorted(ports)),
    )

    observations = mapped["observations"]

    system_evidence = collection["evidence"]["discovery.system"]
    system_info = system_evidence["data"]

    prerequisite_observations = {
        item.requirement_id: observations.get(item.requirement_id)
        for item in definition.prerequisites
    }

    conflict_observations = {
        item.conflict_id: observations.get(item.conflict_id)
        for item in definition.conflicts
    }

    assessment = evaluate_application_plan(
        definition,
        system_info=system_info,
        prerequisite_observations=prerequisite_observations,
        conflict_observations=conflict_observations,
    )

    # Draft policies cannot be installation-ready.
    # Preserve confirmed blockers instead of downgrading them.
    if metadata.get("policy_state") != "COMPLETE":
        reasons = set(assessment["reasons"])
        reasons.add("APPLICATION_POLICY_DRAFT")
        assessment["reasons"] = sorted(reasons)

        if assessment["status"] != "BLOCKED":
            assessment["status"] = "REVIEW_REQUIRED"

    assessment["target_id"] = target_id
    assessment["evidence_request_ids"] = {
        operation: record["request_id"]
        for operation, record in collection["evidence"].items()
    }

    assessment["planning_only"] = True
    assessment["execution_allowed"] = False

    return assessment
