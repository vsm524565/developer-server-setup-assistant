"""PHP runtime application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial PHP runtime definition."""

    return draft_application(
        application_id="php",
        display_name="PHP",
        category="runtime",
        discovery_required=(
            "discovery.system",
            "discovery.runtimes",
        ),
        notes="PHP versions, extensions and FPM policies pending.",
    )