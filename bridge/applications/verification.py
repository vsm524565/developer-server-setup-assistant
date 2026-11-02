
"""Read-only assessment of application verification results."""

from bridge.applications.contracts import ApplicationDefinition


VERIFICATION_STATUSES = frozenset({
    "PASS",
    "FAIL",
    "UNKNOWN",
})


def evaluate_verification(definition, observations):
    """Evaluate completed application verification observations."""

    if not isinstance(definition, ApplicationDefinition):
        raise ValueError("Invalid application definition")

    if not isinstance(observations, dict):
        raise ValueError("Verification observations must be a dictionary")

    results = []
    failures = []
    warnings = []

    for requirement in definition.verification:
        observed = observations.get(requirement.check_id, "UNKNOWN")

        if observed not in VERIFICATION_STATUSES:
            raise ValueError(
                f"Invalid verification observation: "
                f"{requirement.check_id}"
            )

        results.append({
            "check_id": requirement.check_id,
            "check_type": requirement.check_type,
            "target": requirement.target,
            "required": requirement.required,
            "status": observed,
        })

        if observed == "PASS":
            continue

        if requirement.required:
            failures.append(
                f"REQUIRED_VERIFICATION_{observed}:"
                f"{requirement.check_id}"
            )
        else:
            warnings.append(
                f"OPTIONAL_VERIFICATION_{observed}:"
                f"{requirement.check_id}"
            )

    if failures:
        overall_status = "FAILED"
    elif warnings:
        overall_status = "DEGRADED"
    else:
        overall_status = "PASSED"

    return {
        "schema_version": "1.0",
        "check": "application_verification",
        "application_id": definition.application_id,
        "status": overall_status,
        "results": results,
        "failures": failures,
        "warnings": warnings,
        "execution_allowed": False,
    }
