"""Java runtime application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Java runtime definition."""

    return draft_application(
        application_id="java",
        display_name="Java Runtime / JDK",
        category="runtime",
        discovery_required=(
            "discovery.system",
            "discovery.runtimes",
        ),
        notes="JDK distribution and version policies pending.",
    )