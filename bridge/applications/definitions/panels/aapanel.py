"""aaPanel application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial aaPanel definition."""

    return draft_application(
        application_id="aapanel",
        display_name="aaPanel",
        category="control_panel",
        discovery_required=(
            "discovery.system",
            "discovery.panels",
            "discovery.ports",
            "discovery.web_servers",
        ),
        notes=(
            "OS compatibility, web stack selection, installation "
            "and verification policies pending."
        ),
    )
