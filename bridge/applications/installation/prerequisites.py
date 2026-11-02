"""Read-only application dependency evaluation.

Dependency observations must originate from validated discovery evidence.
This module evaluates provided observations; it does not authenticate them,
collect evidence or authorize installation.
"""

from bridge.applications.installation.contracts import InstallationRecipe
from bridge.applications.installation.selection import (
    select_installation_recipe,
)


DEPENDENCY_STATES = frozenset({
    "INSTALLED_HEALTHY",
    "MISSING",
    "UNMANAGED",
    "UNHEALTHY",
    "UNKNOWN",
})


def evaluate_recipe_prerequisites(
    recipe,
    observations,
    registry,
    system_info,
):
    """Evaluate required application dependencies for a draft recipe."""

    if not isinstance(recipe, InstallationRecipe):
        raise ValueError("Invalid installation recipe")

    if not isinstance(observations, dict):
        raise ValueError("Dependency observations must be an object")

    if not isinstance(system_info, dict):
        raise ValueError("System information must be an object")

    results = []
    reasons = []

    blocked = False
    review_required = False
    dependency_plan_required = False

    for dependency in recipe.dependencies:
        if not dependency.required:
            continue

        application_id = dependency.application_id
        state = observations.get(application_id, "UNKNOWN")

        if state not in DEPENDENCY_STATES:
            raise ValueError(
                f"Invalid dependency state for {application_id}"
            )

        result = {
            "application_id": application_id,
            "observed_state": state,
            "status": None,
            "selected_recipe_id": None,
        }

        if state == "INSTALLED_HEALTHY":
            result["status"] = "SATISFIED"

        elif state == "MISSING":
            selection = select_installation_recipe(
                registry,
                application_id,
                system_info,
            )

            if selection["status"] == "MATCHED_DRAFT":
                result["status"] = "DEPENDENCY_PLAN_REQUIRED"
                result["selected_recipe_id"] = selection["recipe_id"]
                dependency_plan_required = True

            elif selection["status"] == "AMBIGUOUS_MATCH":
                result["status"] = "REVIEW_REQUIRED"
                review_required = True
                reasons.append(
                    f"AMBIGUOUS_DEPENDENCY_RECIPE:{application_id}"
                )

            else:
                result["status"] = "BLOCKED"
                blocked = True
                reasons.append(
                    f"NO_DEPENDENCY_RECIPE:{application_id}"
                )

        else:
            result["status"] = "REVIEW_REQUIRED"
            review_required = True
            reasons.append(
                f"DEPENDENCY_{state}:{application_id}"
            )

        results.append(result)

    if blocked:
        status = "BLOCKED"

    elif review_required:
        status = "REVIEW_REQUIRED"

    elif dependency_plan_required:
        status = "DEPENDENCY_PLAN_REQUIRED"

    else:
        status = "SATISFIED_DRAFT"

    if recipe.policy_state == "DRAFT":
        reasons.append("RECIPE_POLICY_DRAFT")

    return {
        "schema_version": "1.0",
        "check": "installation_recipe_prerequisites",
        "recipe_id": recipe.recipe_id,
        "application_id": recipe.application_id,
        "status": status,
        "dependencies": results,
        "reasons": reasons,
        "planning_only": True,
        "execution_allowed": False,
    }
