"""HAProxy application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the HAProxy definition (component definition: definitions/components/haproxy.json)."""

    return draft_application(
        application_id="haproxy",
        display_name="HAProxy",
        category="proxy_ssl",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft; lab verification pending.",
    )
