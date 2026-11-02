
"""Read-only evaluation of application conflicts."""

from bridge.applications.contracts import ApplicationDefinition


def evaluate_conflicts(definition, observations):
    """Identify active conflicts declared by an application."""

    if not isinstance(definition, ApplicationDefinition):
        raise ValueError("Invalid application definition")

    if not isinstance(observations, dict):
        raise ValueError("Conflict observations must be a dictionary")

    results = []
    blockers = []
    warnings = []

    for rule in definition.conflicts:
        # True = conflict detected; False = conflict absent.
        # None or missing = detection inconclusive.
        observed = observations.get(rule.conflict_id)

        if type(observed) is not bool and observed is not None:
            raise ValueError(
                f"Invalid conflict observation: {rule.conflict_id}"
            )

        if observed is True:
            status = "DETECTED"

            if rule.severity == "BLOCKER":
                blockers.append(f"CONFLICT_DETECTED:{rule.conflict_id}")
            else:
                warnings.append(f"CONFLICT_WARNING:{rule.conflict_id}")

        elif observed is False:
            status = "NOT_DETECTED"

        else:
            status = "UNKNOWN"

            if rule.severity == "BLOCKER":
                blockers.append(
                    f"BLOCKING_CONFLICT_NOT_ASSESSED:{rule.conflict_id}"
                )
            else:
                warnings.append(
                    f"WARNING_CONFLICT_NOT_ASSESSED:{rule.conflict_id}"
                )

        results.append({
            "conflict_id": rule.conflict_id,
            "kind": rule.kind,
            "name": rule.name,
            "severity": rule.severity,
            "status": status,
            "reason": rule.reason,
        })

    if blockers:
        overall_status = "BLOCKED"
    elif warnings:
        overall_status = "DEGRADED"
    else:
        overall_status = "CLEAR"

    return {
        "schema_version": "1.0",
        "check": "application_conflicts",
        "application_id": definition.application_id,
        "status": overall_status,
        "conflicts": results,
        "blockers": blockers,
        "warnings": warnings,
        "execution_allowed": False,
    }
