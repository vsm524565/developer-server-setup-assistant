"""Read-only application compatibility assessment."""

from bridge.applications.contracts import (
    ApplicationDefinition,
    CompatibilityRequirement,
)


def evaluate_compatibility(definition, system_info):
    """Evaluate declared OS, architecture and resource requirements."""

    if not isinstance(definition, ApplicationDefinition):
        raise ValueError("Invalid application definition")

    if not isinstance(system_info, dict):
        raise ValueError("Invalid system information")

    requirement = definition.compatibility

    if not isinstance(requirement, CompatibilityRequirement):
        raise ValueError("Invalid compatibility requirement")

    checks = {}
    reasons = []

    # Undeclared support must not be treated as universal support.
    if not requirement.os_ids or not requirement.architectures:
        return {
            "schema_version": "1.0",
            "check": "application_compatibility",
            "application_id": definition.application_id,
            "status": "UNKNOWN",
            "checks": checks,
            "reasons": ["COMPATIBILITY_POLICY_INCOMPLETE"],
            "execution_allowed": False,
        }

    os_id = system_info.get("os_id")
    os_version = system_info.get("os_version")
    architecture = system_info.get("architecture")

    if not all(
        isinstance(value, str) and value.strip()
        for value in (os_id, os_version, architecture)
    ):
        reasons.append("SYSTEM_IDENTITY_INCOMPLETE")
    else:
        checks["os"] = os_id in requirement.os_ids
        checks["architecture"] = (
            architecture in requirement.architectures
        )

        if requirement.os_versions:
            checks["os_version"] = (
                os_version in requirement.os_versions
            )

        if not checks["os"]:
            reasons.append("UNSUPPORTED_OPERATING_SYSTEM")

        if not checks["architecture"]:
            reasons.append("UNSUPPORTED_ARCHITECTURE")

        if checks.get("os_version") is False:
            reasons.append("UNSUPPORTED_OS_VERSION")

    for field, minimum in (
        ("memory_total_bytes", requirement.min_memory_bytes),
        ("disk_available_bytes", requirement.min_disk_bytes),
    ):
        if minimum is None:
            continue

        value = system_info.get(field)

        if type(value) is not int or value < 0:
            reasons.append(f"INVALID_RESOURCE:{field}")
            continue

        sufficient = value >= minimum
        checks[field] = sufficient

        if not sufficient:
            reasons.append(f"INSUFFICIENT_RESOURCE:{field}")

    if any(
        reason.startswith(("SYSTEM_IDENTITY_INCOMPLETE", "INVALID_RESOURCE"))
        for reason in reasons
    ):
        status = "UNKNOWN"
    elif reasons:
        status = "UNSUPPORTED"
    else:
        status = "SUPPORTED"

    return {
        "schema_version": "1.0",
        "check": "application_compatibility",
        "application_id": definition.application_id,
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "execution_allowed": False,
    }
