"""Docker Compose application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Docker Compose application definition."""

    return draft_application(
        application_id="docker_compose",
        display_name="Docker Compose",
        category="container",
        discovery_required=(
            "discovery.system",
        ),
        notes=(
            "Requires Docker Engine. Plugin installation, version "
            "compatibility and verification policies pending."
        ),
    )
