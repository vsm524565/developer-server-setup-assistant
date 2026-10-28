"""Read-only evidence acceptance gate for planning."""

from bridge.evidence.freshness import evaluate_freshness


def evaluate_evidence_gate(
    evidence,
    *,
    expected_operation,
    expected_target_id,
    expected_target_path=None,
    max_age_seconds=30,
):
    """Decide whether evidence may be consumed for read-only planning."""

    reasons = []

    freshness = evaluate_freshness(
        evidence,
        max_age_seconds=max_age_seconds,
    )

    if freshness["status"] != "FRESH":
        reasons.append("EVIDENCE_NOT_FRESH")

    if not isinstance(evidence, dict):
        reasons.append("INVALID_EVIDENCE")
    else:
        if evidence.get("operation") != expected_operation:
            reasons.append("OPERATION_MISMATCH")

        if evidence.get("target_id") != expected_target_id:
            reasons.append("TARGET_MISMATCH")

        if expected_target_path is not None:
            parameters = evidence.get("parameters")
            data = evidence.get("data")

            if (
                not isinstance(parameters, dict)
                or not isinstance(data, dict)
                or parameters.get("target_path") != expected_target_path
                or data.get("target_path") != expected_target_path
            ):
                reasons.append("TARGET_PATH_MISMATCH")

    return {
        "schema_version": "1.0",
        "check": "evidence_gate",
        "status": "ACCEPTED" if not reasons else "REJECTED",
        "freshness": freshness,
        "reasons": reasons,
        "planning_allowed": not reasons,
        "execution_allowed": False,
    }
