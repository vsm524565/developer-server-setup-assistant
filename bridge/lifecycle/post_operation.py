
"""Read-only post-operation assessment for DSSA."""

from bridge.lifecycle.contracts import PostOperationAssessment


def evaluate_post_operation(
    context,
    *,
    health_assessment,
    comparison=None,
    evidence_binding=None,
    cleanup_status="NOT_STARTED",
    cleanup_results=None,
):
    """Combine server health, baseline changes and cleanup evidence."""

    if health_assessment.context != context:
        raise ValueError("Post-operation context mismatch")
    if health_assessment.execution_allowed is not False:
        raise ValueError("Health assessment cannot authorize execution")

    if health_assessment.status not in (
        "READY", "DEGRADED", "BLOCKED", "UNKNOWN", "UNSUPPORTED"
    ):
        raise ValueError("Invalid health assessment status")

    if not isinstance(health_assessment.checks, dict):
        raise ValueError("Invalid health assessment checks")
    # A binding is required for verified lifecycle assessment.
    if evidence_binding is not None:
        if not isinstance(evidence_binding, dict):
            raise ValueError("Invalid evidence binding")

        if (
            evidence_binding.get("schema_version") != "1.0"
            or evidence_binding.get("check") != "system_evidence_binding"
            or evidence_binding.get("target_id") != context.target_id
            or evidence_binding.get("execution_allowed") is not False
        ):
            raise ValueError("Invalid evidence binding contract")
        # Historical bindings must belong to this lifecycle request.
        binding_mode = evidence_binding.get("binding_mode")

        if binding_mode is not None:
            if binding_mode != "HISTORICAL_ACCEPTANCE":
                raise ValueError("Unsupported evidence binding mode")

            if evidence_binding.get("lifecycle_request_id") != context.request_id:
                raise ValueError("Historical binding lifecycle mismatch")
        before_id = evidence_binding.get("before_request_id")
        after_id = evidence_binding.get("after_request_id")

        if (
            not isinstance(before_id, str)
            or not before_id
            or not isinstance(after_id, str)
            or not after_id
            or before_id == after_id
        ):
            raise ValueError("Invalid evidence request identities")

        if comparison is not None:
            raise ValueError(
                "Supply evidence binding or comparison, not both"
            )

        comparison = evidence_binding.get("comparison")

    elif comparison is None:
        raise ValueError("Missing baseline comparison")
    # Confirm the post-operation health assessment includes
    # the same system observation used in baseline comparison.
    if evidence_binding is not None:
        after_id = evidence_binding["after_request_id"]

        if after_id not in health_assessment.evidence_request_ids:
            raise ValueError(
                "Post-operation health and system evidence mismatch"
            )

        if len(health_assessment.evidence_request_ids) != len(
            set(health_assessment.evidence_request_ids)
        ):
            raise ValueError(
                "Duplicate post-operation health evidence identities"
            )
    if comparison.get("schema_version") != "1.0":
        raise ValueError("Invalid comparison schema")

    if comparison.get("check") != "baseline_comparison":
        raise ValueError("Invalid comparison type")

    if comparison.get("execution_allowed") is not False:
        raise ValueError("Comparison cannot authorize execution")

    if comparison.get("status") not in ("CHANGED", "UNCHANGED"):
        raise ValueError("Invalid comparison status")

    changes = comparison.get("changes")

    if not isinstance(changes, list):
        raise ValueError("Invalid comparison changes")

    if comparison["status"] == "UNCHANGED" and changes:
        raise ValueError("Unchanged comparison contains changes")

    if comparison["status"] == "CHANGED" and not changes:
        raise ValueError("Changed comparison contains no changes")

    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Invalid baseline change entry")

        if not all(
            key in change
            for key in ("field", "before", "after", "classification")
        ):
            raise ValueError("Incomplete baseline change entry")

        if change["classification"] not in (
            "REVIEW_REQUIRED",
            "CAPACITY_CHANGE",
            "OBSERVED_CHANGE",
        ):
            raise ValueError("Invalid baseline change classification")

        if change["before"] == change["after"]:
            raise ValueError("Baseline change contains identical values")

    warnings = list(health_assessment.warnings)

    review_required = [
        change
        for change in changes
        if change.get("classification") == "REVIEW_REQUIRED"
    ]

    if review_required:
        warnings.append("BASELINE_REVIEW_REQUIRED")

    if cleanup_status in ("PARTIAL", "FAILED"):
        warnings.append(f"CLEANUP_STATUS:{cleanup_status}")

    if health_assessment.status in (
        "UNKNOWN", "UNSUPPORTED", "BLOCKED"
    ):
        status = health_assessment.status
    elif warnings or health_assessment.status == "DEGRADED":
        status = "DEGRADED"
    else:
        status = "READY"

    return PostOperationAssessment(
        context=context,
        status=status,
        health_checks={
            "post_operation_health_status": health_assessment.status,
            "baseline_comparison_status": comparison["status"],
            "evidence_binding_mode": (
                evidence_binding.get("binding_mode", "IMMEDIATE")
                if evidence_binding is not None
                else "UNVERIFIED_COMPARISON"
            ),
            "post_operation_evidence_request_ids": list(
                health_assessment.evidence_request_ids
            ),
        },
        observed_changes=changes,
        cleanup_status=cleanup_status,
        cleanup_results=list(cleanup_results or []),
        warnings=warnings,
        execution_allowed=False,
    )
