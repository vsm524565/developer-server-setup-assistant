
"""Coordinate read-only runtime-aware Docker readiness assessments."""

from bridge.applications.readiness.coordinator import (
    assess_application_readiness,
)
from bridge.applications.readiness.runtime_reconciliation import (
    reconcile_runtime_readiness,
)
from bridge.applications.runtime.interpreter import (
    classify_application_runtime_evidence,
)


RUNTIME_APPLICATIONS = frozenset({
    "docker",
    "docker_compose",
})


def assess_runtime_aware_readiness(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Combine existing readiness with verified runtime observations."""

    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("Invalid target ID.")

    if (
        not isinstance(application_id, str)
        or application_id not in RUNTIME_APPLICATIONS
    ):
        raise ValueError("Unsupported runtime readiness application.")

    if (
        type(max_age_seconds) not in (int, float)
        or not 0 < max_age_seconds <= 300
    ):
        raise ValueError("Invalid evidence freshness window.")

    # Reuse the completed Milestone 4.15 assessment.
    # It collects and validates seven discovery evidence records.
    readiness = assess_application_readiness(
        backend,
        target_id,
        application_id,
        max_age_seconds=max_age_seconds,
    )

    if (
        not isinstance(readiness, dict)
        or readiness.get("target_id") != target_id
        or readiness.get("application_id") != application_id
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
    ):
        raise RuntimeError("Invalid base application readiness.")

    base_request_ids = readiness.get("evidence_request_ids")

    if (
        not isinstance(base_request_ids, dict)
        or len(base_request_ids) != 7
        or len(set(base_request_ids.values())) != 7
    ):
        raise RuntimeError("Invalid base readiness evidence identities.")

    # Collect an eighth discovery record through the existing
    # evidence-gated Python adapter. No direct SSH or installation.
    runtime_evidence = backend.discover_for_planning(
        "discovery.application_runtime",
        target_id,
        parameters={"application_id": application_id},
        max_age_seconds=max_age_seconds,
    )

    # The runtime interpreter independently validates freshness,
    # target binding, application binding and the execution policy.
    runtime = classify_application_runtime_evidence(
        runtime_evidence,
        expected_application_id=application_id,
        expected_target_id=target_id,
        max_age_seconds=max_age_seconds,
    )

    # The policy function does not execute commands or alter inputs.
    result = reconcile_runtime_readiness(
        readiness,
        runtime,
    )

    request_ids = result.get("evidence_request_ids")

    if (
        not isinstance(request_ids, dict)
        or len(request_ids) != 8
        or len(set(request_ids.values())) != 8
        or result.get("planning_only") is not True
        or result.get("execution_allowed") is not False
        or result.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Invalid runtime-aware readiness decision.")

    return result
