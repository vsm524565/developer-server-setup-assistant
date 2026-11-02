"""Apache HTTP Server application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Apache application definition."""

    return draft_application(
        application_id="apache",
        display_name="Apache HTTP Server",
        category="webserver",
        discovery_required=(
            "discovery.system",
            "discovery.web_servers",
            "discovery.ports",
        ),
        notes="Compatibility, installation and verification policies remain undefined.",
    )
