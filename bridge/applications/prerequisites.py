
"""Read-only assessment of application prerequisites."""

from bridge.applications.contracts import ApplicationDefinition


def evaluate_prerequisites(definition, observations):
    """Evaluate declared prerequisites against observed conditions."""

    if not isinstance(definition, ApplicationDefinition):
        raise ValueError("Invalid application definition")

    if not isinstance(observations, dict):
        raise ValueError("Prerequisite observations must be a dictionary")

    results = []
    blockers = []
    warnings = []

    for requirement in definition.prerequisites:
        # Observations are keyed by requirement ID.
        # True = satisfied; False = missing; None/absent = unknown.
        observed = observations.get(requirement.requirement_id)

        if type(observed) is not bool and observed is not None:
            raise ValueError(
                f"Invalid prerequisite observation: "
                f"{requirement.requirement_id}"
            )

        if observed is True:
            status = "SATISFIED"

        elif observed is False:
            status = "MISSING"

            if requirement.level == "REQUIRED":
                blockers.append(
                    f"REQUIRED_PREREQUISITE_MISSING:"
                    f"{requirement.requirement_id}"
                )
            else:
                warnings.append(
                    f"OPTIONAL_PREREQUISITE_MISSING:"
                    f"{requirement.requirement_id}"
                )

        else:
            status = "UNKNOWN"

            if requirement.level == "REQUIRED":
                blockers.append(
                    f"REQUIRED_PREREQUISITE_UNKNOWN:"
                    f"{requirement.requirement_id}"
                )
            else:
                warnings.append(
                    f"OPTIONAL_PREREQUISITE_UNKNOWN:"
                    f"{requirement.requirement_id}"
                )

        results.append({
            "requirement_id": requirement.requirement_id,
            "kind": requirement.kind,
            "name": requirement.name,
            "level": requirement.level,
            "status": status,
        })

    if blockers:
        overall_status = "BLOCKED"
    elif warnings:
        overall_status = "DEGRADED"
    else:
        overall_status = "SATISFIED"

    return {
        "schema_version": "1.0",
        "check": "application_prerequisites",
        "application_id": definition.application_id,
        "status": overall_status,
        "requirements": results,
        "blockers": blockers,
        "warnings": warnings,
        "execution_allowed": False,
    }
