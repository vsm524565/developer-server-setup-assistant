
"""Coordinate read-only application readiness assessments."""

from bridge.applications.installation.plan import (
    build_draft_installation_plan,
)
from bridge.applications.state.interpreter import (
    classify_application_evidence,
)
from bridge.applications.inventory.normalizer import (
    normalize_application_inventory_evidence,
)
from bridge.applications.readiness.evaluator import (
    evaluate_application_readiness,
)
from bridge.evidence.gate import evaluate_evidence_gate


def _validated_application_evidence(
    backend,
    operation,
    target_id,
    application_id,
    max_age_seconds,
):
    """Collect and independently verify target-bound application evidence."""

    result = backend.discover_for_planning(
        operation,
        target_id,
        parameters={"application_id": application_id},
        max_age_seconds=max_age_seconds,
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid application evidence result.")

    gate = result.get("gate")
    evidence = result.get("evidence")

    if (
        not isinstance(gate, dict)
        or gate.get("status") != "ACCEPTED"
        or gate.get("planning_allowed") is not True
        or gate.get("execution_allowed") is not False
        or result.get("execution_allowed") is not False
        or not isinstance(evidence, dict)
        or evidence.get("operation") != operation
        or evidence.get("target_id") != target_id
        or evidence.get("parameters") != {
            "application_id": application_id,
        }
        or evidence.get("execution_allowed") is not False
    ):
        raise RuntimeError("Application evidence rejected.")

    independent_gate = evaluate_evidence_gate(
        evidence,
        expected_operation=operation,
        expected_target_id=target_id,
        max_age_seconds=max_age_seconds,
    )

    if independent_gate.get("status") != "ACCEPTED":
        raise RuntimeError("Independent evidence verification failed.")

    request_id = evidence.get("request_id")

    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError("Missing discovery request identity.")

    return result


def assess_application_readiness(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Build a read-only readiness decision from live evidence."""

    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("Invalid target ID.")

    if application_id not in (
        "git", "nginx", "docker", "docker_compose"
    ):
        raise ValueError("Unsupported application ID.")

    if (
        type(max_age_seconds) not in (int, float)
        or not 0 < max_age_seconds <= 300
    ):
        raise ValueError("Invalid evidence freshness window.")

    plan = build_draft_installation_plan(
        backend,
        target_id,
        application_id,
        max_age_seconds=max_age_seconds,
    )

    state_evidence = _validated_application_evidence(
        backend,
        "discovery.application_state",
        target_id,
        application_id,
        max_age_seconds,
    )

    inventory_evidence = _validated_application_evidence(
        backend,
        "discovery.application_inventory",
        target_id,
        application_id,
        max_age_seconds,
    )

    state = classify_application_evidence(
        state_evidence,
        expected_application_id=application_id,
        expected_target_id=target_id,
    )

    inventory = normalize_application_inventory_evidence(
        inventory_evidence,
        expected_application_id=application_id,
        expected_target_id=target_id,
    )

    # The three sources must have unique discovery request IDs.
    plan_request_ids = plan.get("evidence_request_ids")

    if (
        not isinstance(plan_request_ids, dict)
        or not plan_request_ids
        or any(
            not isinstance(value, str) or not value
            for value in plan_request_ids.values()
        )
    ):
        raise RuntimeError("Invalid installation-plan evidence identities.")

    all_request_ids = [
        *plan_request_ids.values(),
        state["evidence_request_id"],
        inventory["evidence_request_id"],
    ]

    if len(all_request_ids) != len(set(all_request_ids)):
        raise RuntimeError("Duplicate readiness evidence request identity.")

    decision = evaluate_application_readiness(
        target_id=target_id,
        application_id=application_id,
        state=state,
        inventory=inventory,
        draft_plan=plan,
    )

    decision["evidence_request_ids"] = {
        **plan_request_ids,
        "discovery.application_state": state["evidence_request_id"],
        "discovery.application_inventory": (
            inventory["evidence_request_id"]
        ),
    }

    decision["state_reasons"] = list(state["reasons"])
    decision["inventory_reasons"] = list(inventory["reasons"])
    decision["draft_plan_reasons"] = list(plan["reasons"])

    return decision
