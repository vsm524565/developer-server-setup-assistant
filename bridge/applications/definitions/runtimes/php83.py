"""PHP 8.3 (side by side) application definition (components phase, vendor repository)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the PHP 8.3 (side by side) definition (component definition: definitions/components/php83.json)."""

    return draft_application(
        application_id="php83",
        display_name="PHP 8.3 (side by side)",
        category="runtime",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft from a vendor repository; lab verification pending.",
    )
