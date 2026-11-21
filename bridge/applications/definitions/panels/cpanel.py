"""cPanel & WHM control panel application definition (panels, 4.48)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the cPanel & WHM definition (component definition: definitions/components/cpanel.json)."""

    return draft_application(
        application_id="cpanel",
        display_name="cPanel & WHM",
        category="control_panel",
        discovery_required=(
            "discovery.system",
            "discovery.panels",
            "discovery.ports",
            "discovery.web_servers",
        ),
        notes="Installed by the vendor installer on a fresh server only (docs/design/panels.md).",
    )
