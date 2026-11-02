"""Redis application definition."""

from bridge.applications.contracts import ApplicationDefinition


def get_definition():
    """Return the initial Redis policy definition."""

    return ApplicationDefinition(
        application_id="redis",
        display_name="Redis",
        category="database",
        metadata={
            "policy_state": "DRAFT",
            "discovery_required": [
                "discovery.system",
                "discovery.ports",
            ],
            "notes": (
                "Package selection, configuration, security "
                "and service verification are not yet defined."
            ),
        },
    )
