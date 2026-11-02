"""Python runtime application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Python runtime definition."""

    return draft_application(
        application_id="python",
        display_name="Python 3",
        category="runtime",
        discovery_required=(
            "discovery.system",
            "discovery.runtimes",
        ),
        notes="Interpreter, pip and venv requirements pending.",
    )
