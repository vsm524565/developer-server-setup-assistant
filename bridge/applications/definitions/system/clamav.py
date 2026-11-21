"""ClamAV application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the ClamAV definition (component definition: definitions/components/clamav.json)."""

    return draft_application(
        application_id="clamav",
        display_name="ClamAV",
        category="system",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft; lab verification pending.",
    )
