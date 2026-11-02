
"""Read-only application assessment and planning decisions for DSSA.

Combines validated application observations and policy evaluations.
This module does not approve or execute installations.
"""

from bridge.applications.contracts import ApplicationDefinition
from bridge.applications.compatibility import evaluate_compatibility
from bridge.applications.prerequisites import evaluate_prerequisites
from bridge.applications.conflicts import evaluate_conflicts


def evaluate_application_plan(
    definition,
    *,
    system_info,
    prerequisite_observations,
    conflict_observations,
):
    """Combine application requirements into one planning-only decision."""

    if not isinstance(definition, ApplicationDefinition):
        raise ValueError("Invalid application definition")

    compatibility = evaluate_compatibility(
        definition,
        system_info,
    )

    prerequisites = evaluate_prerequisites(
        definition,
        prerequisite_observations,
    )

    conflicts = evaluate_conflicts(
        definition,
        conflict_observations,
    )

    reasons = []
    warnings = []

    # An undeclared compatibility policy cannot establish support.
    policy_state = definition.metadata.get("policy_state")

    policy_incomplete = (
        policy_state != "COMPLETE"
        or not definition.compatibility.os_ids
        or not definition.compatibility.architectures
        or not definition.verification
    )

    if policy_incomplete:
        reasons.append("APPLICATION_POLICY_INCOMPLETE")

    # A confirmed unsupported platform or detected blocking conflict
    # prevents installation planning.
    confirmed_conflicts = [
        reason
        for reason in conflicts["blockers"]
        if reason.startswith("CONFLICT_DETECTED:")
    ]

    missing_prerequisites = [
        reason
        for reason in prerequisites["blockers"]
        if reason.startswith("REQUIRED_PREREQUISITE_MISSING:")
    ]

    unknown_requirements = [
        reason
        for reason in prerequisites["blockers"]
        if reason.startswith("REQUIRED_PREREQUISITE_UNKNOWN:")
    ]

    unknown_conflicts = [
        reason
        for reason in conflicts["blockers"]
        if reason.startswith("BLOCKING_CONFLICT_NOT_ASSESSED:")
    ]

    reasons.extend(compatibility["reasons"])
    reasons.extend(prerequisites["blockers"])
    reasons.extend(conflicts["blockers"])

    warnings.extend(prerequisites["warnings"])
    warnings.extend(conflicts["warnings"])

    if (
        compatibility["status"] == "UNSUPPORTED"
        or confirmed_conflicts
    ):
        status = "BLOCKED"

    elif (
        compatibility["status"] == "UNKNOWN"
        or policy_incomplete
        or unknown_requirements
        or unknown_conflicts
    ):
        status = "REVIEW_REQUIRED"

    elif missing_prerequisites:
        status = "PREREQUISITES_MISSING"

    elif warnings:
        status = "REVIEW_REQUIRED"

    else:
        status = "PLAN_READY"

    return {
        "schema_version": "1.0",
        "check": "application_plan_assessment",
        "application_id": definition.application_id,
        "status": status,
        "compatibility": compatibility,
        "prerequisites": prerequisites,
        "conflicts": conflicts,
        "expected_changes": [
            {
                "change_id": change.change_id,
                "resource_type": change.resource_type,
                "resource_name": change.resource_name,
                "description": change.description,
            }
            for change in definition.expected_changes
        ],
        "verification_checks": [
            {
                "check_id": check.check_id,
                "check_type": check.check_type,
                "target": check.target,
                "required": check.required,
            }
            for check in definition.verification
        ],
        "reasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
        "planning_only": True,
        "execution_allowed": False,
    }
