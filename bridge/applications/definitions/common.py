"""Shared helpers for DSSA application policy definitions.

Draft definitions register supported catalog entries without
claiming compatibility or installation readiness.
"""

from bridge.applications.contracts import ApplicationDefinition


def draft_application(
    application_id,
    display_name,
    category,
    *,
    discovery_required=(),
    notes="",
):
    """Build a non-executable draft application definition."""

    if not isinstance(discovery_required, tuple):
        raise ValueError("Discovery operations must be a tuple")

    if any(
        not isinstance(operation, str) or not operation.strip()
        for operation in discovery_required
    ):
        raise ValueError("Invalid discovery operation")

    return ApplicationDefinition(
        application_id=application_id,
        display_name=display_name,
        category=category,
        metadata={
            "policy_state": "DRAFT",
            "discovery_required": list(discovery_required),
            "notes": notes,
        },
    )
