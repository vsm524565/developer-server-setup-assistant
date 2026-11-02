"""Linux swap management definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial swap management definition."""

    return draft_application(
        application_id="swap",
        display_name="Linux Swap",
        category="system",
        discovery_required=(
            "discovery.system",
        ),
        notes="Swap sizing, filesystem validation and creation pending.",
    )