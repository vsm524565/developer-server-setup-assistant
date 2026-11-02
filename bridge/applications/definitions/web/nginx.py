"""Nginx application definition."""

from bridge.applications.contracts import (
    ApplicationDefinition,
    ConflictRule,
    VerificationRequirement,
    ExpectedChange,
)


def get_definition():
    """Return the initial Nginx policy definition."""

    return ApplicationDefinition(
        application_id="nginx",
        display_name="Nginx",
        category="webserver",
        conflicts=(
            ConflictRule(
                conflict_id="tcp_port.80.listening",
                kind="port",
                name="TCP port 80",
                severity="WARNING",
                reason="Existing listener requires ownership review.",
            ),
            ConflictRule(
                conflict_id="tcp_port.443.listening",
                kind="port",
                name="TCP port 443",
                severity="WARNING",
                reason="Existing listener requires ownership review.",
            ),
        ),
        expected_changes=(
            ExpectedChange(
                change_id="nginx.package",
                resource_type="package",
                resource_name="nginx",
                description="Nginx package installation or update.",
            ),
        ),
        verification=(
            VerificationRequirement(
                check_id="nginx.service_active",
                check_type="service",
                target="nginx",
            ),
        ),
        metadata={
            "policy_state": "DRAFT",
            "discovery_required": [
                "discovery.system",
                "discovery.web_servers",
                "discovery.ports",
            ],
        },
    )
