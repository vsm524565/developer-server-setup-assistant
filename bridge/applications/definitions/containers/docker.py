"""Docker Engine application definition."""

from bridge.applications.contracts import ApplicationDefinition


def get_definition():
    """Return the initial Docker Engine policy definition."""

    return ApplicationDefinition(
        application_id="docker",
        display_name="Docker Engine",
        category="container",
        metadata={
            "policy_state": "DRAFT",
            "discovery_required": [
                "discovery.system",
            ],
            "notes": (
                "Package, service, repository and Compose "
                "requirements will be defined separately."
            ),
        },
    )
