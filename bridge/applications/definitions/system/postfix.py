"""Postfix (outgoing mail) application definition (components phase)."""

from bridge.applications.definitions.common import draft_application


def get_definition():
    """Return the Postfix (outgoing mail) definition (component definition: definitions/components/postfix.json)."""

    return draft_application(
        application_id="postfix",
        display_name="Postfix (outgoing mail)",
        category="system",
        discovery_required=(
            "discovery.system",
        ),
        notes="Components phase draft; lab verification pending.",
    )
