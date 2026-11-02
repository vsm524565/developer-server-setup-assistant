"""OpenLiteSpeed webserver application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial OpenLiteSpeed definition."""

    return draft_application(
        application_id="openlitespeed",
        display_name="OpenLiteSpeed",
        category="webserver",
        discovery_required=(
            "discovery.system",
            "discovery.web_servers",
            "discovery.ports",
        ),
        notes="Edition, package, repository and port policies pending.",
    )