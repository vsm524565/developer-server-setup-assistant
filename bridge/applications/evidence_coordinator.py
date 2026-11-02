"""Collect and validate application evidence through the Linux backend.

The existing backend owns SSH discovery and evidence gating.
This module does not execute installation operations.
"""

from bridge.applications.evidence_mapping import (
    map_web_server_observations,
    map_port_observations,
    map_panel_observations,
)
from bridge.evidence.gate import evaluate_evidence_gate

APPLICATION_DISCOVERY_OPERATIONS = frozenset({
    "discovery.system",
    "discovery.package_manager",
    "discovery.web_servers",
    "discovery.ports",
    "discovery.panels",
})


def collect_application_evidence(
    backend,
    target_id,
    *,
    operations,
    max_age_seconds=30,
):
    """Collect target-bound, accepted read-only discovery evidence."""

    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("Invalid application target")

    if (
        type(max_age_seconds) not in (int, float)
        or isinstance(max_age_seconds, bool)
        or not 0 < max_age_seconds <= 300
    ):
        raise ValueError("Invalid evidence freshness window")

    if not isinstance(operations, (list, tuple)) or not operations:
        raise ValueError("Discovery operations are required")

    if (
        any(
            not isinstance(operation, str)
            or operation not in APPLICATION_DISCOVERY_OPERATIONS
            for operation in operations
        )
        or len(operations) != len(set(operations))
    ):
        raise ValueError("Invalid or duplicate discovery operation")

    evidence_by_operation = {}
    request_ids = set()

    for operation in operations:
        result = backend.discover_for_planning(
            operation,
            target_id,
            max_age_seconds=max_age_seconds,
        )

        if not isinstance(result, dict):
            raise RuntimeError("Invalid backend planning result")

        gate = result.get("gate")
        evidence = result.get("evidence")

        if (
            not isinstance(gate, dict)
            or gate.get("schema_version") != "1.0"
            or gate.get("check") != "evidence_gate"
            or gate.get("status") != "ACCEPTED"
            or gate.get("planning_allowed") is not True
            or gate.get("execution_allowed") is not False
        ):
            raise RuntimeError(
                f"Application evidence rejected: {operation}"
            )

        if not isinstance(evidence, dict):
            raise RuntimeError("Invalid application evidence envelope")

        request_id = evidence.get("request_id")
        collection = evidence.get("collection")

        if (
            evidence.get("schema_version") != "1.0"
            or evidence.get("operation") != operation
            or evidence.get("target_id") != target_id
            or evidence.get("parameters") != {}
            or evidence.get("execution_allowed") is not False
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(evidence.get("data"), dict)
            or not isinstance(collection, dict)
            or collection.get("clock_source") != "controller"
        ):
            raise RuntimeError(
                f"Invalid application evidence contract: {operation}"
            )
        # Independently validate freshness, operation and target.
        # Do not rely solely on the backend-supplied gate result.
        independent_gate = evaluate_evidence_gate(
            evidence,
            expected_operation=operation,
            expected_target_id=target_id,
            max_age_seconds=max_age_seconds,
        )

        if independent_gate["status"] != "ACCEPTED":
            raise RuntimeError(
                f"Independent evidence gate rejected: {operation}"
            )
        if request_id in request_ids:
            raise RuntimeError("Duplicate discovery request ID")

        request_ids.add(request_id)
        evidence_by_operation[operation] = evidence

    return {
        "schema_version": "1.0",
        "check": "application_evidence_collection",
        "target_id": target_id,
        "status": "ACCEPTED",
        "evidence": evidence_by_operation,
        "planning_only": True,
        "execution_allowed": False,
    }


def map_application_evidence(collection, *, ports=()):
    """Map collected evidence to supported application observations."""

    if not isinstance(collection, dict):
        raise ValueError("Invalid application evidence collection")

    if (
        collection.get("schema_version") != "1.0"
        or collection.get("check") != "application_evidence_collection"
        or collection.get("status") != "ACCEPTED"
        or collection.get("planning_only") is not True
        or collection.get("execution_allowed") is not False
        or not isinstance(collection.get("evidence"), dict)
    ):
        raise ValueError("Unaccepted application evidence collection")

    evidence = collection["evidence"]
    observations = {}

    mappings = {
        "discovery.web_servers": map_web_server_observations,
        "discovery.panels": map_panel_observations,
    }

    for operation, mapper in mappings.items():
        record = evidence.get(operation)

        if record is not None:
            observations.update(mapper(record["data"]))

    port_record = evidence.get("discovery.ports")

    if port_record is not None:
        observations.update(
            map_port_observations(port_record["data"], ports)
        )

    return {
        "schema_version": "1.0",
        "check": "application_observation_mapping",
        "target_id": collection["target_id"],
        "observations": observations,
        "planning_only": True,
        "execution_allowed": False,
    }
