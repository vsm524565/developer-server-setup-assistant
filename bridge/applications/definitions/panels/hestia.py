"""HestiaCP application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial HestiaCP definition."""

    return draft_application(
        application_id="hestia",
        display_name="Hestia Control Panel",
        category="control_panel",
        discovery_required=(
            "discovery.system",
            "discovery.panels",
            "discovery.ports",
            "discovery.web_servers",
        ),
        notes=(
            "OS compatibility, managed services, installation "
            "and verification policies pending."
        ),
    )
