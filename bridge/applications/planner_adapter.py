"""Translate application assessments for the existing DSSA planner.

Milestone 4.11 is planning-only. This adapter never produces READY
or any other status that could generate an installation action.
"""


def to_legacy_planner_decision(assessment):
    """Translate an application assessment into a safe planner decision."""

    if not isinstance(assessment, dict):
        raise ValueError("Invalid application assessment")

    if (
        assessment.get("schema_version") != "1.0"
        or assessment.get("check") != "application_plan_assessment"
        or assessment.get("planning_only") is not True
        or assessment.get("execution_allowed") is not False
    ):
        raise ValueError("Invalid application assessment contract")

    application_id = assessment.get("application_id")
    status = assessment.get("status")
    reasons = assessment.get("reasons")

    if (
        not isinstance(application_id, str)
        or not application_id.strip()
        or not isinstance(reasons, list)
        or any(not isinstance(reason, str) for reason in reasons)
    ):
        raise ValueError("Invalid assessment fields")

    status_mapping = {
        "BLOCKED": "BLOCKED",
        "REVIEW_REQUIRED": "REVIEW",
        "PREREQUISITES_MISSING": "REVIEW",
        "PLAN_READY": "REVIEW",
    }

    if status not in status_mapping:
        raise ValueError(f"Unsupported application status: {status}")

    mapped_status = status_mapping[status]
    mapped_reasons = list(reasons)

    if status == "PLAN_READY":
        mapped_reasons.append(
            "Application execution integration is not enabled."
        )

    if status == "PREREQUISITES_MISSING":
        mapped_reasons.append(
            "Prerequisite installation planning is not enabled."
        )

    return {
        "status": mapped_status,
        "reasons": mapped_reasons,
    }
