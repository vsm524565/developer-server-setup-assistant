
"""Bind before/after system evidence for read-only lifecycle comparison."""

from datetime import datetime, timezone

from bridge.evidence.gate import evaluate_evidence_gate
from bridge.lifecycle.baseline_comparison import compare_baselines


def _completion_time(evidence):
    """Read a controller-issued, timezone-aware completion timestamp."""
    try:
        collection = evidence["collection"]

        if collection["clock_source"] != "controller":
            raise ValueError("Untrusted evidence clock")

        timestamp = datetime.fromisoformat(
            collection["completed_at_utc"]
        )

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Evidence timestamp has no timezone")

        return timestamp.astimezone(timezone.utc)

    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid evidence completion timestamp") from exc


def compare_system_evidence(
    context,
    *,
    before_evidence,
    after_evidence,
    max_age_seconds=30,
):
    """Compare ordered system evidence without authorizing execution."""

    for name, evidence in (
        ("before", before_evidence),
        ("after", after_evidence),
    ):
        if not isinstance(evidence, dict):
            raise ValueError(f"Invalid {name} evidence")

        if (
            evidence.get("schema_version") != "1.0"
            or evidence.get("execution_allowed") is not False
            or evidence.get("operation") != "discovery.system"
            or evidence.get("target_id") != context.target_id
            or evidence.get("parameters") != {}
            or not isinstance(evidence.get("data"), dict)
        ):
            raise ValueError(f"Invalid {name} evidence contract")

    if before_evidence["request_id"] == after_evidence["request_id"]:
        raise ValueError("Before/after evidence request IDs must differ")

    # Historical evidence must have been accepted at operation start.
    # Until the lifecycle coordinator records that acceptance,
    # both records are checked for current freshness.
    for name, evidence in (
        ("before", before_evidence),
        ("after", after_evidence),
    ):
        gate = evaluate_evidence_gate(
            evidence,
            expected_operation="discovery.system",
            expected_target_id=context.target_id,
            max_age_seconds=max_age_seconds,
        )

        if gate["status"] != "ACCEPTED":
            raise ValueError(f"{name.capitalize()} evidence rejected")

    before_time = _completion_time(before_evidence)
    after_time = _completion_time(after_evidence)

    if after_time <= before_time:
        raise ValueError("Post-operation evidence is not newer")

    comparison = compare_baselines(
        before_evidence["data"],
        after_evidence["data"],
    )

    return {
        "schema_version": "1.0",
        "check": "system_evidence_binding",
        "target_id": context.target_id,
        "before_request_id": before_evidence["request_id"],
        "after_request_id": after_evidence["request_id"],
        "comparison": comparison,
        "execution_allowed": False,
    }

def compare_accepted_system_evidence(
    context,
    *,
    accepted_before,
    after_evidence,
    max_age_seconds=30,
):
    """Compare an accepted historical baseline with fresh system evidence."""

    if not isinstance(accepted_before, dict):
        raise ValueError("Invalid historical acceptance record")

    if (
        accepted_before.get("schema_version") != "1.0"
        or accepted_before.get("check") != "pre_operation_system_acceptance"
        or accepted_before.get("execution_allowed") is not False
        or accepted_before.get("lifecycle_request_id") != context.request_id
        or accepted_before.get("target_id") != context.target_id
        or accepted_before.get("operation") != context.operation
    ):
        raise ValueError("Historical acceptance context mismatch")

    before_id = accepted_before.get("evidence_request_id")

    if not isinstance(before_id, str) or not before_id:
        raise ValueError("Invalid historical evidence request ID")

    baseline = accepted_before.get("baseline")

    if not isinstance(baseline, dict):
        raise ValueError("Invalid historical baseline")

    # Only the newly collected post-operation evidence needs
    # to satisfy the current freshness window.
    gate = evaluate_evidence_gate(
        after_evidence,
        expected_operation="discovery.system",
        expected_target_id=context.target_id,
        max_age_seconds=max_age_seconds,
    )

    if gate["status"] != "ACCEPTED":
        raise ValueError("Post-operation system evidence rejected")

    if after_evidence.get("parameters") != {}:
        raise ValueError("Unexpected post-operation discovery parameters")

    after_id = after_evidence["request_id"]

    if before_id == after_id:
        raise ValueError("Before/after evidence IDs must differ")

    # Validate timestamps and observation ordering.
    try:
        before_time = datetime.fromisoformat(
            accepted_before["evidence_completed_at_utc"]
        )
        accepted_time = datetime.fromisoformat(
            accepted_before["accepted_at_utc"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid historical timestamps") from exc

    if any(
        value.tzinfo is None or value.utcoffset() is None
        for value in (before_time, accepted_time)
    ):
        raise ValueError("Historical timestamps must be timezone-aware")

    before_time = before_time.astimezone(timezone.utc)
    accepted_time = accepted_time.astimezone(timezone.utc)
    after_time = _completion_time(after_evidence)

    if accepted_time < before_time:
        raise ValueError("Acceptance predates baseline collection")

    if after_time <= accepted_time:
        raise ValueError("Post-operation evidence is not newer than acceptance")

    comparison = compare_baselines(
        baseline,
        after_evidence["data"],
    )

    return {
        "schema_version": "1.0",
        "check": "system_evidence_binding",
        "target_id": context.target_id,
        "lifecycle_request_id": context.request_id,
        "before_request_id": before_id,
        "after_request_id": after_id,
        "binding_mode": "HISTORICAL_ACCEPTANCE",
        "comparison": comparison,
        "execution_allowed": False,
    }
