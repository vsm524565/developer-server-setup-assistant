"""UFW firewall application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial UFW definition."""

    return draft_application(
        application_id="ufw",
        display_name="UFW Firewall",
        category="system",
        discovery_required=(
            "discovery.system",
        ),
        notes="Firewall compatibility, rules and SSH safety pending.",
    )