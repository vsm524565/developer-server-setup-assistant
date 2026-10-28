"""Controller-side metadata for read-only discovery evidence."""

from datetime import datetime, timezone
import time


class EvidenceCollector:
    """Track a single discovery collection interval."""

    def __init__(self):
        self.started_at_utc = datetime.now(timezone.utc)
        self.started_monotonic_ns = time.monotonic_ns()

    def complete(
        self, *, request_id, operation, target_id, data, parameters=None
    ):
        """Return a structured, controller-timestamped evidence envelope."""
        completed_monotonic_ns = time.monotonic_ns()
        completed_at_utc = datetime.now(timezone.utc)

        if completed_monotonic_ns < self.started_monotonic_ns:
            raise RuntimeError("Controller monotonic clock moved backward.")

        if not isinstance(data, dict):
            raise ValueError("Discovery evidence must be a JSON object.")

        for name, value in (
            ("request_id", request_id),
            ("operation", operation),
            ("target_id", target_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"Invalid evidence {name}.")
        if parameters is None:
            parameters = {}

        if not isinstance(parameters, dict):
            raise ValueError("Evidence parameters must be an object.")

        if operation == "discovery.swap_filesystem":
            requested_path = parameters.get("target_path")

            if (
                not isinstance(requested_path, str)
                or data.get("target_path") != requested_path
            ):
                raise RuntimeError(
                    "Swap filesystem evidence target does not match request."
                )
        return {
            "schema_version": "1.0",
            "request_id": request_id,
            "operation": operation,
            "target_id": target_id,
            "collection": {
                "started_at_utc": self.started_at_utc.isoformat(),
                "completed_at_utc": completed_at_utc.isoformat(),
                "duration_ms": (
                    completed_monotonic_ns - self.started_monotonic_ns
                ) // 1_000_000,
                "clock_source": "controller",
            },
            "parameters": dict(parameters),
            "data": data,
            "execution_allowed": False,
        }
