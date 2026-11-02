"""CyberPanel application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial CyberPanel definition."""

    return draft_application(
        application_id="cyberpanel",
        display_name="CyberPanel",
        category="control_panel",
        discovery_required=(
            "discovery.system",
            "discovery.panels",
            "discovery.ports",
            "discovery.web_servers",
        ),
        notes=(
            "OS compatibility, OpenLiteSpeed integration, "
            "installation and verification policies pending."
        ),
    )
