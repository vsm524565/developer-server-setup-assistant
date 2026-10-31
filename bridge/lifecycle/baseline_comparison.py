"""Read-only comparison of DSSA server baselines."""

from __future__ import annotations

from typing import Any


TRACKED_FIELDS = (
    "hostname",
    "os_id",
    "os_version",
    "architecture",
    "cpu_logical_cores",
    "memory_total_bytes",
    "swap_total_bytes",
    "disk_total_bytes",
    "disk_available_bytes",
)
IDENTITY_FIELDS = frozenset({
    "hostname",
    "os_id",
    "os_version",
    "architecture",
    "cpu_logical_cores",
})

CAPACITY_FIELDS = frozenset({
    "memory_total_bytes",
    "swap_total_bytes",
    "disk_total_bytes",
})

VOLATILE_FIELDS = frozenset({
    "disk_available_bytes",
})

def compare_baselines(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Compare validated system discovery records without changing state."""

    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("Baselines must be dictionaries")

    changes = []

    for field in TRACKED_FIELDS:
        if field not in before or field not in after:
            raise ValueError(f"Missing baseline field: {field}")

        old_value = before[field]
        new_value = after[field]

        if type(old_value) is not type(new_value):
            raise ValueError(f"Baseline field type mismatch: {field}")

        if old_value == new_value:
            continue

        changes.append({
            "field": field,
            "before": old_value,
            "after": new_value,
            "classification": (
                "REVIEW_REQUIRED"
                if field in IDENTITY_FIELDS
                else "CAPACITY_CHANGE"
                if field in CAPACITY_FIELDS
                else "OBSERVED_CHANGE"
            ),
        })

    return {
        "schema_version": "1.0",
        "check": "baseline_comparison",
        "status": "CHANGED" if changes else "UNCHANGED",
        "changes": changes,
        "execution_allowed": False,
    }
