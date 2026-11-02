"""Git application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Git application definition."""

    return draft_application(
        application_id="git",
        display_name="Git",
        category="tool",
        discovery_required=(
            "discovery.system",
            "discovery.package_manager",
        ),
        notes="Package discovery and version verification pending.",
    )