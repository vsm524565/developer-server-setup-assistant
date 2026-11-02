"""MariaDB Server application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial MariaDB application definition."""

    return draft_application(
        application_id="mariadb",
        display_name="MariaDB Server",
        category="database",
        discovery_required=(
            "discovery.system",
            "discovery.ports",
        ),
        notes="Version, package, configuration and verification policies pending.",
    )
