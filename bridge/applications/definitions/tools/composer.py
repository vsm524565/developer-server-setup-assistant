"""Composer (PHP) application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the Composer (PHP) definition (component definition: definitions/components/composer.json)."""

    return draft_application(
        application_id="composer",
        display_name="Composer (PHP)",
        category="tool",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft; lab verification pending.",
    )
