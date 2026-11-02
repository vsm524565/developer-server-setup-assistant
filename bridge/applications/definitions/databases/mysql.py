"""MySQL application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial MySQL application definition."""

    return draft_application(
        application_id="mysql",
        display_name="MySQL Server",
        category="database",
        discovery_required=(
            "discovery.system",
            "discovery.ports",
        ),
        notes="Version, package and service policies remain undefined.",
    )
