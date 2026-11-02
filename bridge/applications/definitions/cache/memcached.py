"""Memcached application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Memcached application definition."""

    return draft_application(
        application_id="memcached",
        display_name="Memcached",
        category="cache",
        discovery_required=(
            "discovery.system",
        ),
        notes="Package, network exposure and service verification pending.",
    )
