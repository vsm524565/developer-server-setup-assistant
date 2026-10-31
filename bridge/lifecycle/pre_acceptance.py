
"""Controller-side acceptance snapshot for pre-operation system evidence."""

from copy import deepcopy
from datetime import datetime, timezone

from bridge.evidence.gate import evaluate_evidence_gate


def accept_pre_operation_system_evidence(
    context,
    evidence,
    *,
    max_age_seconds=30,
):
    """Accept and snapshot fresh system evidence before an operation."""

    gate = evaluate_evidence_gate(
        evidence,
        expected_operation="discovery.system",
        expected_target_id=context.target_id,
        max_age_seconds=max_age_seconds,
    )

    if gate["status"] != "ACCEPTED":
        raise ValueError("Pre-operation system evidence rejected")

    if evidence.get("parameters") != {}:
        raise ValueError("Unexpected system discovery parameters")

    if not isinstance(evidence.get("data"), dict):
        raise ValueError("Invalid system baseline")

    from bridge.lifecycle.baseline_comparison import TRACKED_FIELDS

    baseline = evidence["data"]

    for field in TRACKED_FIELDS:
        if field not in baseline:
            raise ValueError(f"Missing system baseline field: {field}")

    return {
        "schema_version": "1.0",
        "check": "pre_operation_system_acceptance",
        "lifecycle_request_id": context.request_id,
        "target_id": context.target_id,
        "operation": context.operation,
        "evidence_request_id": evidence["request_id"],
        "evidence_completed_at_utc": (
            evidence["collection"]["completed_at_utc"]
        ),
        "accepted_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "baseline": deepcopy(baseline),
        "execution_allowed": False,
    }
