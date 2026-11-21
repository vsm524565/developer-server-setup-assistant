"""PHP 8.4 (side by side) application definition (components phase, vendor repository)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the PHP 8.4 (side by side) definition (component definition: definitions/components/php84.json)."""

    return draft_application(
        application_id="php84",
        display_name="PHP 8.4 (side by side)",
        category="runtime",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft from a vendor repository; lab verification pending.",
    )
