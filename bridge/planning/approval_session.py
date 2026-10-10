
"""
Manage demonstration-only approval for a specific server and plan.

No installation execution or persistent authorization.
"""

from bridge.planning.approval_binding import ApprovalBinding


class ApprovalSession:
    """Track an approval bound to an exact server and plan context."""

    def __init__(self):
        self._approved_fingerprint = None

    def approve(self, fingerprint):
        """Record approval for an already reviewed fingerprint."""
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise ValueError("Invalid approval fingerprint.")

        try:
            int(fingerprint, 16)
        except ValueError as error:
            raise ValueError("Invalid approval fingerprint.") from error

        self._approved_fingerprint = fingerprint

    def is_valid(self, target, host_key_sha256, snapshot, plan):
        """Check whether the approved context still matches."""
        if self._approved_fingerprint is None:
            return False

        return ApprovalBinding.matches(
            self._approved_fingerprint,
            target,
            host_key_sha256,
            snapshot,
            plan,
        )

    def invalidate(self):
        """Explicitly revoke the current demonstration approval."""
        self._approved_fingerprint = None
