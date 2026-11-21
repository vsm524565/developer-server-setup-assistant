"""MongoDB application definition (components phase, vendor repository)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the MongoDB definition (component definition: definitions/components/mongodb.json)."""

    return draft_application(
        application_id="mongodb",
        display_name="MongoDB",
        category="database",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft from a vendor repository; lab verification pending.",
    )
