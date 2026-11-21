"""Elasticsearch application definition (components phase, vendor repository)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the Elasticsearch definition (component definition: definitions/components/elasticsearch.json)."""

    return draft_application(
        application_id="elasticsearch",
        display_name="Elasticsearch",
        category="database",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft from a vendor repository; lab verification pending.",
    )
