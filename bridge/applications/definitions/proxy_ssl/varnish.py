"""Varnish Cache application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the Varnish Cache definition (component definition: definitions/components/varnish.json)."""

    return draft_application(
        application_id="varnish",
        display_name="Varnish Cache",
        category="proxy_ssl",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft; lab verification pending.",
    )
