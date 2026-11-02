"""Bind validated discovery evidence to draft installation planning.

This module never performs installations. Missing package ownership
or service-health evidence remains UNKNOWN.
"""

from bridge.applications.evidence_coordinator import (
    collect_application_evidence,
    map_application_evidence,
)


INSTALLATION_DISCOVERY_OPERATIONS = (
    "discovery.system",
    "discovery.package_manager",
    "discovery.web_servers",
    "discovery.ports",
    "discovery.panels",
)

SUPPORTED_INSTALLATION_APPLICATIONS = frozenset({
    "git",
    "nginx",
    "docker",
    "docker_compose",
})


def normalize_installation_platform(system_data, manager_data):
    """Normalize observed OS facts and package-manager availability."""

    if not isinstance(system_data, dict):
        raise ValueError("Invalid system evidence")

    if not isinstance(manager_data, dict):
        raise ValueError("Invalid package-manager evidence")

    platform = {}

    for source_key in ("os_id", "os_version", "architecture"):
        value = system_data.get(source_key)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid system field: {source_key}")

        platform[source_key] = value

    detected = manager_data.get("package_manager_detected")
    tools = manager_data.get("available_package_tools")

    if type(detected) is not bool:
        raise ValueError("Invalid package-manager detection flag")

    if not isinstance(tools, list):
        raise ValueError("Invalid package-tool collection")

    if any(
        not isinstance(tool, str) or not tool.strip()
        for tool in tools
    ):
        raise ValueError("Invalid package tool")

    if len(tools) != len(set(tools)):
        raise ValueError("Duplicate package tools")

    tool_set = set(tools)
    os_id = platform["os_id"]

    manager = None

    if detected:
        if (
            os_id in ("ubuntu", "debian")
            and {"apt-get", "dpkg"}.issubset(tool_set)
        ):
            manager = "apt"

        elif (
            os_id in ("almalinux", "rocky")
            and {"dnf", "rpm"}.issubset(tool_set)
        ):
            manager = "dnf"

    platform["package_manager"] = manager

    return {
        "platform": platform,
        "status": (
            "OBSERVED" if manager is not None
            else "INCOMPLETE"
        ),
        "planning_only": True,
        "execution_allowed": False,
    }


def collect_installation_evidence(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Collect and bind accepted target-specific discovery evidence."""

    if application_id not in SUPPORTED_INSTALLATION_APPLICATIONS:
        raise ValueError(
            f"Unsupported installation application: {application_id}"
        )

    collection = collect_application_evidence(
        backend,
        target_id,
        operations=INSTALLATION_DISCOVERY_OPERATIONS,
        max_age_seconds=max_age_seconds,
    )

    mapped = map_application_evidence(
        collection,
        ports=(80, 443),
    )

    records = collection["evidence"]

    system_data = records["discovery.system"]["data"]
    manager_data = records["discovery.package_manager"]["data"]

    normalized = normalize_installation_platform(
        system_data,
        manager_data,
    )

    observations = dict(mapped["observations"])

    # None of the currently accepted discovery operations proves
    # Docker package ownership and daemon health.
    dependency_states = {
        "docker": "UNKNOWN",
    }

    request_ids = {
        operation: record["request_id"]
        for operation, record in records.items()
    }

    return {
        "schema_version": "1.0",
        "check": "installation_evidence_binding",
        "application_id": application_id,
        "target_id": target_id,
        "status": normalized["status"],
        "platform": normalized["platform"],
        "system_data": dict(system_data),
        "package_manager_data": dict(manager_data),
        "observations": observations,
        "dependency_states": dependency_states,
        "evidence_request_ids": request_ids,
        "planning_only": True,
        "execution_allowed": False,
    }