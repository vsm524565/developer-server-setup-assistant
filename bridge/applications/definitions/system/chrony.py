"""Chrony time synchronisation application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the chrony definition (component definition: definitions/components/chrony.json)."""

    return draft_application(
        application_id="chrony",
        display_name="Chrony (time sync)",
        category="system",
        discovery_required=(
            "discovery.system",
            "discovery.time_health",
        ),
        notes="Not installed where systemd-timesyncd already keeps time.",
    )
