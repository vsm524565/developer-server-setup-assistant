"""Certbot application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Certbot application definition."""

    return draft_application(
        application_id="certbot",
        display_name="Certbot",
        category="proxy_ssl",
        discovery_required=(
            "discovery.system",
        ),
        notes="ACME challenge, plugin and renewal policies pending.",
    )
