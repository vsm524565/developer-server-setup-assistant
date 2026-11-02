"""PostgreSQL application definition."""

from bridge.applications.contracts import ApplicationDefinition


def get_definition():
    """Return the initial PostgreSQL policy definition."""

    return ApplicationDefinition(
        application_id="postgresql",
        display_name="PostgreSQL",
        category="database",
        metadata={
            "policy_state": "DRAFT",
            "discovery_required": [
                "discovery.system",
                "discovery.ports",
            ],
            "notes": (
                "Version selection, repository policy, "
                "cluster configuration and verification "
                "are not yet defined."
            ),
        },
    )
