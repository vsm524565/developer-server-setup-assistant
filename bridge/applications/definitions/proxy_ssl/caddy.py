"""Caddy web server and reverse proxy application definition."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the initial Caddy application definition."""

    return draft_application(
        application_id="caddy",
        display_name="Caddy",
        category="proxy_ssl",
        discovery_required=(
            "discovery.system",
            "discovery.ports",
        ),
        notes="Repository, ports, TLS and service verification pending.",
    )
