
"""Read-only common pre-operation assessment for DSSA."""

from bridge.evidence.gate import evaluate_evidence_gate
from bridge.lifecycle.contracts import PreOperationAssessment


REQUIRED_DISCOVERIES = {
    "bootstrap": "discovery.bootstrap",
    "common_health": "discovery.common_health",
    "system": "discovery.system",
}


def evaluate_pre_operation(
    context,
    *,
    bootstrap_evidence,
    health_evidence,
    system_evidence,
    max_age_seconds=30,
):
    """Evaluate common server readiness using existing discovery evidence."""

    evidence_set = {
        "bootstrap": bootstrap_evidence,
        "common_health": health_evidence,
        "system": system_evidence,
    }

    checks = {}
    blockers = []
    warnings = []
    evidence_request_ids = []
    validated = {}

    # First establish that all evidence is fresh and target-bound.
    for name, operation in REQUIRED_DISCOVERIES.items():
        evidence = evidence_set[name]

        gate = evaluate_evidence_gate(
            evidence,
            expected_operation=operation,
            expected_target_id=context.target_id,
            max_age_seconds=max_age_seconds,
        )

        checks[f"{name}_evidence"] = {
            "status": gate["status"],
            "reasons": gate["reasons"],
        }

        if gate["status"] != "ACCEPTED":
            blockers.append(f"{name.upper()}_EVIDENCE_REJECTED")
            continue

        parameters = evidence.get("parameters")

        if parameters != {}:
            blockers.append(f"{name.upper()}_UNEXPECTED_PARAMETERS")
            continue

        request_id = evidence["request_id"]

        if request_id in evidence_request_ids:
            blockers.append("DUPLICATE_EVIDENCE_REQUEST_ID")
            continue

        evidence_request_ids.append(request_id)
        validated[name] = evidence["data"]
    if blockers:
        return PreOperationAssessment(
            context=context,
            status="UNKNOWN",
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            evidence_request_ids=evidence_request_ids,
        )
    # Validate the shape and consistency of accepted discovery data.
    bootstrap = validated["bootstrap"]
    health = validated["common_health"]
    system = validated["system"]

    if (
        bootstrap.get("schema_version") != "1.0"
        or bootstrap.get("check") != "bootstrap_capabilities"
        or bootstrap.get("execution_allowed") is not False
        or not isinstance(bootstrap.get("capabilities"), list)
    ):
        blockers.append("INVALID_BOOTSTRAP_DATA")

    if (
        health.get("schema_version") != "1.0"
        or health.get("check") != "common_health"
        or health.get("execution_allowed") is not False
    ):
        blockers.append("INVALID_COMMON_HEALTH_DATA")
    else:
        identity = health.get("identity")
        privileges = health.get("privileges")
        time_health = health.get("time_health")

        if not all(
            isinstance(item, dict)
            for item in (identity, privileges, time_health)
        ):
            blockers.append("INVALID_HEALTH_SECTIONS")
        else:
            uid = identity.get("uid")

            if (
                type(uid) is not int
                or uid < 0
                or type(identity.get("is_root")) is not bool
                or identity["is_root"] != (uid == 0)
                or privileges.get("uid") != uid
                or privileges.get("execution_allowed") is not False
                or time_health.get("execution_allowed") is not False
            ):
                blockers.append("INCONSISTENT_HEALTH_IDENTITY")

    if (
        not isinstance(system.get("os_id"), str)
        or not system["os_id"]
        or not isinstance(system.get("architecture"), str)
        or not system["architecture"]
        or any(
            type(system.get(field)) is not int
            or system[field] < 0
            for field in (
                "memory_available_bytes",
                "disk_available_bytes",
            )
        )
    ):
        blockers.append("INVALID_SYSTEM_BASELINE")

    if blockers:
        return PreOperationAssessment(
            context=context,
            status="UNKNOWN",
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            evidence_request_ids=evidence_request_ids,
            execution_allowed=False,
        )
    # Examine the common bootstrap capabilities.
    bootstrap = validated["bootstrap"]
    capabilities = bootstrap.get("capabilities")

    if not isinstance(capabilities, list):
        blockers.append("INVALID_BOOTSTRAP_CAPABILITIES")
    else:
        command_status = {
            item["name"]: item["status"]
            for item in capabilities
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item.get("status") in ("AVAILABLE", "MISSING")
        }

        if command_status.get("bash") != "AVAILABLE":
            blockers.append("BASH_UNAVAILABLE")

        missing_commands = sorted(
            name for name, status in command_status.items()
            if status == "MISSING"
        )

        checks["bootstrap"] = {
            "missing_commands": missing_commands,
        }

        for name in missing_commands:
            if name != "bash":
                warnings.append(f"OPTIONAL_COMMAND_MISSING:{name}")

    # Examine common identity, privileges and time health.
    health = validated["common_health"]
    identity = health.get("identity", {})
    privileges = health.get("privileges", {})
    time_health = health.get("time_health", {})

    checks["identity"] = {
        "username": identity.get("username"),
        "uid": identity.get("uid"),
        "is_root": identity.get("is_root"),
    }

    privilege_status = privileges.get("status", "UNKNOWN")

    checks["privileges"] = {
        "status": privilege_status,
    }

    if privilege_status in ("UNKNOWN", "UNAVAILABLE", "DENIED"):
        warnings.append(f"PRIVILEGE_STATUS:{privilege_status}")
    elif privilege_status == "PASSWORD_REQUIRED":
        warnings.append("SUDO_INTERACTIVE_AUTH_REQUIRED")

    synchronization = time_health.get("synchronization", "UNKNOWN")

    checks["time_health"] = {
        "synchronization": synchronization,
        "ntp_enabled": time_health.get("ntp_enabled", "UNKNOWN"),
    }

    if synchronization != "SYNCHRONIZED":
        warnings.append(f"TIME_STATUS:{synchronization}")

    # Record general system information for common baseline assessment.
    system = validated["system"]

    checks["system"] = {
        "os_id": system.get("os_id"),
        "os_version": system.get("os_version"),
        "architecture": system.get("architecture"),
        "memory_available_bytes": system.get("memory_available_bytes"),
        "disk_available_bytes": system.get("disk_available_bytes"),
    }

    if not system.get("os_id") or not system.get("architecture"):
        blockers.append("SYSTEM_IDENTITY_INCOMPLETE")

    # This assessment only determines common planning readiness.
    if blockers:
        status = "BLOCKED"
    elif warnings:
        status = "DEGRADED"
    else:
        status = "READY"

    return PreOperationAssessment(
        context=context,
        status=status,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        evidence_request_ids=evidence_request_ids,
        execution_allowed=False,
    )
