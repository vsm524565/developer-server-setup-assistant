
"""
Read-only execution readiness evaluation.

This module does not connect to servers or execute commands.
READY is not permission to install software.
"""

from planning.approval_binding import ApprovalBindingError
from planning.preflight import PreflightValidator
from planning.state_consistency import ServerStateComparator


class ExecutionReadinessValidator:
    """Evaluate pre-execution safety evidence."""

    def __init__(
        self,
        approval_session,
        target,
        host_key_sha256,
        original_snapshot,
        current_snapshot,
        installation_plan,
        server_state,
    ):
        self.approval_session = approval_session
        self.target = target
        self.host_key_sha256 = host_key_sha256
        self.original_snapshot = original_snapshot
        self.current_snapshot = current_snapshot
        self.installation_plan = installation_plan
        self.server_state = server_state

    def validate(self):
        checks = {
            "preflight": False,
            "state_consistency": False,
            "approval_binding": False,
        }

        issues = []

        # Gate 1: Structural preflight validation
        try:
            preflight = PreflightValidator(
                self.server_state,
                self.installation_plan,
            ).validate()

            checks["preflight"] = preflight["passed"]

            if not checks["preflight"]:
                issues.extend(
                    f"Preflight: {issue}"
                    for issue in preflight["issues"]
                )

        except (TypeError, ValueError, KeyError) as error:
            issues.append(f"Preflight validation failed: {error}")

        # Gate 2: Compare original and freshly supplied snapshots
        try:
            consistency = ServerStateComparator(
                self.original_snapshot
            ).compare(self.current_snapshot)

            checks["state_consistency"] = consistency["consistent"]

            if not checks["state_consistency"]:
                issues.append("Server state changed since planning.")
                issues.extend(
                    f"State: {change}"
                    for change in consistency["changes"]
                )
                issues.extend(
                    f"State: {issue}"
                    for issue in consistency["issues"]
                )

        except (TypeError, ValueError, KeyError) as error:
            issues.append(f"State comparison failed: {error}")

        # Gate 3: Validate approval against the supplied context
        try:
            checks["approval_binding"] = (
                self.approval_session is not None
                and self.approval_session.is_valid(
                    self.target,
                    self.host_key_sha256,
                    self.current_snapshot,
                    self.installation_plan,
                )
            )

            if not checks["approval_binding"]:
                issues.append(
                    "No valid approval exists for the current "
                    "SSH identity, snapshot, and installation plan."
                )

        except (
            ApprovalBindingError,
            TypeError,
            ValueError,
            AttributeError,
        ) as error:
            issues.append(f"Approval binding validation failed: {error}")

        ready = all(checks.values())

        return {
            "ready": ready,
            "status": "READY" if ready else "BLOCKED",
            "checks": checks,
            "issues": issues,
        }
