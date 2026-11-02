"""Fail2ban application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Fail2ban definition."""

    return draft_application(
        application_id="fail2ban",
        display_name="Fail2ban",
        category="system",
        discovery_required=(
            "discovery.system",
        ),
        notes="Jail configuration, services and allowlist policies pending.",
    )