
"""Read-only lifecycle coordination for DSSA."""

from bridge.lifecycle.contracts import (
    LifecycleReport,
    OperationContext,
    PostOperationAssessment,
)
from bridge.lifecycle.pre_operation import evaluate_pre_operation
from bridge.lifecycle.pre_acceptance import (
    accept_pre_operation_system_evidence,
)
from bridge.lifecycle.evidence_binding import (
    compare_accepted_system_evidence,
)
from bridge.lifecycle.post_operation import evaluate_post_operation
from bridge.lifecycle.artifact_cleanup import plan_artifact_cleanup
from bridge.lifecycle.reporting import build_lifecycle_report


DISCOVERY_OPERATIONS = (
    "discovery.bootstrap",
    "discovery.common_health",
    "discovery.system",
)


def _collect_evidence(backend, target_id):
    """Collect independent controller-side discovery evidence."""

    return {
        operation: backend.discover_with_evidence(
            operation,
            target_id,
        )
        for operation in DISCOVERY_OPERATIONS
    }


def _assess_health(context, evidence):
    """Evaluate common readiness from an evidence collection set."""

    return evaluate_pre_operation(
        context,
        bootstrap_evidence=evidence["discovery.bootstrap"],
        health_evidence=evidence["discovery.common_health"],
        system_evidence=evidence["discovery.system"],
    )


def run_read_only_lifecycle(
    context: OperationContext,
    backend,
    *,
    manifest=None,
    staging_root=None,
) -> LifecycleReport:
    """Assess a server lifecycle without executing an application operation."""

    if not isinstance(context, OperationContext):
        raise ValueError("Invalid lifecycle context")

    if (manifest is None) != (staging_root is None):
        raise ValueError(
            "Artifact manifest and staging root must be supplied together"
        )

    # Phase 1: Collect and assess the pre-operation server baseline.
    try:
        before = _collect_evidence(backend, context.target_id)
        pre = _assess_health(context, before)

    except Exception as exc:
        return LifecycleReport(
            context=context,
            outcome="BLOCKED",
            errors=[
                f"PRE_OPERATION_COLLECTION_FAILED: {type(exc).__name__}"
            ],
            recovery_required=False,
            execution_allowed=False,
        )

    # No read-only lifecycle proceeds with untrusted or blocked evidence.
    if pre.status in ("UNKNOWN", "BLOCKED", "UNSUPPORTED"):
        return build_lifecycle_report(
            context,
            pre_operation=pre,
            operation_state="NOT_STARTED",
        )

    try:
        accepted_before = accept_pre_operation_system_evidence(
            context,
            before["discovery.system"],
        )

    except Exception as exc:
        return LifecycleReport(
            context=context,
            outcome="BLOCKED",
            pre_operation=pre,
            errors=[
                f"PRE_OPERATION_ACCEPTANCE_FAILED: {type(exc).__name__}"
            ],
            recovery_required=False,
            execution_allowed=False,
        )

    # There is intentionally no application execution in Milestone 4.10.

    # Phase 2: Independently observe the server again.
    try:
        after = _collect_evidence(backend, context.target_id)
        post_health = _assess_health(context, after)

        binding = compare_accepted_system_evidence(
            context,
            accepted_before=accepted_before,
            after_evidence=after["discovery.system"],
        )

        cleanup_status = "NOT_STARTED"
        cleanup_results = []

        if manifest is not None:
            cleanup_plan = plan_artifact_cleanup(
                context,
                manifest,
                staging_root=staging_root,
            )

            cleanup_status = (
                "PENDING"
                if cleanup_plan["planned"]
                else "NOT_STARTED"
            )
            cleanup_results = cleanup_plan["planned"]

        post = evaluate_post_operation(
            context,
            health_assessment=post_health,
            evidence_binding=binding,
            cleanup_status=cleanup_status,
            cleanup_results=cleanup_results,
        )

    except Exception as exc:
        post = PostOperationAssessment(
            context=context,
            status="UNKNOWN",
            warnings=[
                f"POST_OPERATION_VERIFICATION_FAILED: {type(exc).__name__}"
            ],
            cleanup_status="NOT_STARTED",
            execution_allowed=False,
        )

    # Read-only observation must never be represented as installation success.
    # A completed operation state is reserved for a future trusted executor.
    report = build_lifecycle_report(
        context,
        pre_operation=pre,
        post_operation=post,
        operation_state="INSPECTED",
    )

    return report
