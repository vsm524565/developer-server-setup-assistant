"""Firewall application definition: ufw on apt, firewalld on dnf (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the firewall definition (component definition: definitions/components/firewall.json)."""

    return draft_application(
        application_id="firewall",
        display_name="Firewall (ufw / firewalld)",
        category="system",
        discovery_required=(
            "discovery.system",
            "discovery.ports",
        ),
        notes="Enabled only after SSH (and HTTP/HTTPS) are allowed.",
    )
