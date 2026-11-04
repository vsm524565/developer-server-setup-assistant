
"""Coordinate read-only Docker readiness with passive daemon preflight."""

from bridge.applications.readiness.runtime_coordinator import (
    assess_runtime_aware_readiness,
)
from bridge.applications.readiness.daemon_reconciliation import (
    reconcile_daemon_readiness,
)
from bridge.applications.runtime.docker_daemon_preflight import (
    classify_docker_daemon_preflight_evidence,
)


def assess_daemon_aware_readiness(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Join eight-source Docker readiness with fresh daemon evidence."""

    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("Invalid Docker readiness target.")

    if application_id != "docker":
        raise ValueError("Daemon-aware readiness supports Docker only.")

    if (
        type(max_age_seconds) not in (int, float)
        or not 0 < max_age_seconds <= 300
    ):
        raise ValueError("Invalid evidence freshness window.")

    # Reuse the existing coordinator and its eight evidence records.
    readiness = assess_runtime_aware_readiness(
        backend,
        target_id,
        application_id,
        max_age_seconds=max_age_seconds,
    )

    if (
        not isinstance(readiness, dict)
        or readiness.get("target_id") != target_id
        or readiness.get("application_id") != "docker"
        or readiness.get("planning_only") is not True
        or readiness.get("execution_allowed") is not False
        or readiness.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Invalid runtime-aware Docker readiness.")

    previous_ids = readiness.get("evidence_request_ids")

    if (
        not isinstance(previous_ids, dict)
        or len(previous_ids) != 8
        or "discovery.application_runtime" not in previous_ids
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in previous_ids.items()
        )
        or len(set(previous_ids.values())) != 8
        or "discovery.docker_daemon_preflight" in previous_ids
    ):
        raise RuntimeError("Invalid eight-source evidence identities.")

    # Collect only the approved passive discovery operation.
    planning = backend.discover_for_planning(
        "discovery.docker_daemon_preflight",
        target_id,
        parameters={},
        max_age_seconds=max_age_seconds,
    )

    # Independently validate gate, freshness, operation, and target.
    daemon = classify_docker_daemon_preflight_evidence(
        planning,
        expected_target_id=target_id,
        max_age_seconds=max_age_seconds,
    )

    # The pure function verifies identity uniqueness and policy.
    result = reconcile_daemon_readiness(
        readiness,
        daemon,
    )

    result_ids = result.get("evidence_request_ids")

    if (
        not isinstance(result_ids, dict)
        or len(result_ids) != 9
        or len(set(result_ids.values())) != 9
        or result_ids.get("discovery.docker_daemon_preflight")
            != daemon.get("evidence_request_id")
        or any(
            result_ids.get(key) != value
            for key, value in previous_ids.items()
        )
        or result.get("target_id") != target_id
        or result.get("application_id") != "docker"
        or result.get("planning_only") is not True
        or result.get("execution_allowed") is not False
        or result.get("daemon_responsive") is not None
    ):
        raise RuntimeError("Invalid daemon-aware readiness decision.")

    return result
