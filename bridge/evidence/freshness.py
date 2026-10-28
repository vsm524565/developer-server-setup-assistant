"""Read-only freshness assessment for controller-collected evidence."""

from datetime import datetime, timezone
import math
import time


DEFAULT_MAX_AGE_SECONDS = 30


def evaluate_freshness(
    evidence,
    *,
    max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
    now_utc=None,
):
    """Classify evidence freshness without authorizing execution."""

    result = {
        "schema_version": "1.0",
        "check": "evidence_freshness",
        "status": "INVALID",
        "age_seconds": None,
        "max_age_seconds": max_age_seconds,
        "reasons": [],
        "execution_allowed": False,
    }

    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        result["reasons"].append("INVALID_MAX_AGE")
        return result

    if not isinstance(evidence, dict):
        result["reasons"].append("INVALID_EVIDENCE")
        return result
    if (
        evidence.get("schema_version") != "1.0"
        or evidence.get("execution_allowed") is not False
        or not isinstance(evidence.get("data"), dict)
        or any(
            not isinstance(evidence.get(field), str)
            or not evidence[field]
            for field in ("request_id", "target_id", "operation")
        )
    ):
        result["reasons"].append("INVALID_EVIDENCE_CONTRACT")
        return result

    collection = evidence.get("collection")

    if not isinstance(collection, dict):
        result["reasons"].append("MISSING_COLLECTION")
        return result

    if collection.get("clock_source") != "controller":
        result["reasons"].append("UNTRUSTED_CLOCK_SOURCE")
        return result

    try:
        started = datetime.fromisoformat(collection["started_at_utc"])
        completed = datetime.fromisoformat(collection["completed_at_utc"])

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (started, completed, now_utc)
        ):
            raise ValueError("Timezone-aware timestamps required.")

        started = started.astimezone(timezone.utc)
        completed = completed.astimezone(timezone.utc)
        now_utc = now_utc.astimezone(timezone.utc)

        duration_ms = collection["duration_ms"]

        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            raise ValueError("Invalid collection duration.")

        # Validate chronological ordering.
        if completed < started:
            raise ValueError("Completion precedes collection start.")

        if completed > now_utc:
            raise ValueError("Completion timestamp is in the future.")

        # Compare wall-clock duration with controller monotonic duration.
        # A significant difference may indicate a clock adjustment
        # during evidence collection.
        wall_duration_ms = (
            completed - started
        ).total_seconds() * 1000

        if abs(wall_duration_ms - duration_ms) > 2000:
            raise ValueError(
                "Collection clock duration is inconsistent."
            )

        # Calculate the age of the completed discovery evidence.
        age_seconds = (now_utc - completed).total_seconds()

        if not math.isfinite(age_seconds):
            raise ValueError("Invalid evidence age.")

    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        result["reasons"].append("INVALID_COLLECTION_TIMING")
        return result

    result["age_seconds"] = age_seconds

    if age_seconds > max_age_seconds:
        result["status"] = "STALE"
        result["reasons"].append("EVIDENCE_TOO_OLD")
    else:
        result["status"] = "FRESH"

    return result
