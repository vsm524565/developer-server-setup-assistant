
"""Common lifecycle outcome reporting for DSSA."""

from bridge.lifecycle.contracts import (
    LifecycleReport,
    OperationContext,
    PreOperationAssessment,
    PostOperationAssessment,
)


OPERATION_STATES = frozenset({
    "NOT_STARTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INSPECTED",
})


def build_lifecycle_report(
    context: OperationContext,
    *,
    pre_operation: PreOperationAssessment,
    post_operation: PostOperationAssessment | None = None,
    operation_state: str = "NOT_STARTED",
    errors=None,
):
    """Build a lifecycle report without authorizing execution."""

    if not isinstance(context, OperationContext):
        raise ValueError("Invalid lifecycle context")

    if not isinstance(pre_operation, PreOperationAssessment):
        raise ValueError("Invalid pre-operation assessment")

    if pre_operation.context != context:
        raise ValueError("Pre-operation context mismatch")

    if pre_operation.execution_allowed is not False:
        raise ValueError("Pre-operation assessment cannot authorize execution")

    if post_operation is not None:
        if not isinstance(post_operation, PostOperationAssessment):
            raise ValueError("Invalid post-operation assessment")

        if post_operation.context != context:
            raise ValueError("Post-operation context mismatch")

        if post_operation.execution_allowed is not False:
            raise ValueError("Post-operation assessment cannot authorize execution")

    if operation_state not in OPERATION_STATES:
        raise ValueError("Invalid operation state")

    if errors is None:
        errors = []

    if not isinstance(errors, list) or any(
        not isinstance(error, str) for error in errors
    ):
        raise ValueError("Errors must be a list of strings")

    # An operation must not be reported as completed without verification.
    if operation_state == "COMPLETED" and post_operation is None:
        outcome = "VERIFICATION_FAILED"

    elif operation_state == "FAILED":
        outcome = "FAILED"

    elif operation_state == "CANCELLED":
        outcome = "CANCELLED"

    elif operation_state == "INSPECTED":
        if (
            post_operation is None
            or post_operation.status in (
                "UNKNOWN", "BLOCKED", "UNSUPPORTED"
            )
        ):
            outcome = "VERIFICATION_FAILED"
        else:
            outcome = "INSPECTED"
    elif operation_state == "NOT_STARTED":
        outcome = "BLOCKED"

    elif post_operation.status in ("BLOCKED", "UNKNOWN", "UNSUPPORTED"):
        outcome = "VERIFICATION_FAILED"

    elif post_operation.cleanup_status in ("FAILED", "PARTIAL"):
        outcome = "PARTIAL"

    elif post_operation.status == "DEGRADED":
        outcome = "PARTIAL"

    elif post_operation.cleanup_status in ("NOT_STARTED", "PENDING"):
        outcome = "PARTIAL"

    else:
        outcome = "SUCCESS"

    # A failed operation or failed verification may require recovery.
    recovery_required = outcome in {
        "FAILED",
        "VERIFICATION_FAILED",
    }

    if post_operation is not None and post_operation.cleanup_status in {
        "FAILED",
        "PARTIAL",
    }:
        recovery_required = True

    return LifecycleReport(
        context=context,
        outcome=outcome,
        pre_operation=pre_operation,
        post_operation=post_operation,
        errors=list(errors),
        recovery_required=recovery_required,
        execution_allowed=False,
    )
