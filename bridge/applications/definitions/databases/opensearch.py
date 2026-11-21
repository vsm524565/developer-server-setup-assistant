"""OpenSearch application definition (components phase, vendor repository)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the OpenSearch definition (component definition: definitions/components/opensearch.json)."""

    return draft_application(
        application_id="opensearch",
        display_name="OpenSearch",
        category="database",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft from a vendor repository; lab verification pending.",
    )
