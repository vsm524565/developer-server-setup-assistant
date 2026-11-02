"""Node.js runtime application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Node.js runtime definition."""

    return draft_application(
        application_id="nodejs",
        display_name="Node.js",
        category="runtime",
        discovery_required=(
            "discovery.system",
            "discovery.runtimes",
        ),
        notes="LTS version, repository and npm policies pending.",
    )
