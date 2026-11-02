"""Assemble read-only installation plans from draft recipes.

A generated plan is informational. It is never an authorization
token and cannot be passed directly to a privileged installer.
"""

from dataclasses import asdict

from bridge.applications.installation.catalog import (
    build_installation_recipe_catalog,
)
from bridge.applications.installation.evidence_binding import (
    collect_installation_evidence,
)
from bridge.applications.installation.prerequisites import (
    evaluate_recipe_prerequisites,
)
from bridge.applications.installation.selection import (
    select_installation_recipe,
)


def build_draft_installation_plan(
    backend,
    target_id,
    application_id,
    *,
    max_age_seconds=30,
):
    """Create a target-bound, non-executable application plan."""

    registry = build_installation_recipe_catalog()

    evidence = collect_installation_evidence(
        backend,
        target_id,
        application_id,
        max_age_seconds=max_age_seconds,
    )

    result = {
        "schema_version": "1.0",
        "check": "draft_installation_plan",
        "target_id": target_id,
        "application_id": application_id,
        "status": "REVIEW_REQUIRED",
        "recipe_id": None,
        "package_source": None,
        "dependencies": [],
        "expected_operations": [],
        "verification_steps": [],
        "observations": dict(evidence["observations"]),
        "evidence_request_ids": dict(
            evidence["evidence_request_ids"]
        ),
        "reasons": [],
        "planning_only": True,
        "execution_allowed": False,
    }

    if evidence["status"] != "OBSERVED":
        result["reasons"].append(
            "PLATFORM_EVIDENCE_INCOMPLETE"
        )
        return result

    system = evidence["platform"]

    selection = select_installation_recipe(
        registry,
        application_id,
        system,
    )

    if selection["status"] == "UNSUPPORTED_PLATFORM":
        result["status"] = "BLOCKED"
        result["reasons"].append("NO_MATCHING_RECIPE")
        return result

    if selection["status"] != "MATCHED_DRAFT":
        result["reasons"].append(
            "AMBIGUOUS_OR_UNAVAILABLE_RECIPE"
        )
        return result

    recipe = registry.get(selection["recipe_id"])

    dependencies = evaluate_recipe_prerequisites(
        recipe,
        evidence["dependency_states"],
        registry,
        system,
    )

    result["recipe_id"] = recipe.recipe_id
    result["package_source"] = asdict(recipe.package_source)
    result["dependencies"] = dependencies["dependencies"]
    result["expected_operations"] = [
        asdict(operation)
        for operation in recipe.expected_operations
    ]
    result["verification_steps"] = [
        asdict(step)
        for step in recipe.verification_steps
    ]

    # Dependency evaluation already reports draft policy state.
    result["reasons"].extend(dependencies["reasons"])

    if dependencies["status"] == "BLOCKED":
        result["status"] = "BLOCKED"

    elif dependencies["status"] == "REVIEW_REQUIRED":
        result["status"] = "REVIEW_REQUIRED"

    elif dependencies["status"] == "DEPENDENCY_PLAN_REQUIRED":
        result["status"] = "REVIEW_REQUIRED"
        result["reasons"].append(
            "DEPENDENCY_INSTALLATION_NOT_AUTHORIZED"
        )

    # The existing Nginx installer treats occupied ports as blockers.
    # Keep this planning policy consistent with that backend behavior.
    if application_id == "nginx":
        observations = evidence["observations"]

        occupied = any(
            observations.get(f"tcp_port.{port}.listening") is True
            for port in (80, 443)
        )

        if occupied:
            result["status"] = "BLOCKED"
            result["reasons"].append(
                "NGINX_REQUIRED_PORT_OCCUPIED"
            )

        # Panel paths are indicators, not proof of health or ownership.
        # Even so, the existing backend refuses new Nginx installs
        # when known panel indicators are present.
        panel_found = any(
            value is True
            for key, value in observations.items()
            if key.endswith(".path_indicator_present")
        )

        if panel_found:
            result["status"] = "BLOCKED"
            result["reasons"].append(
                "PANEL_INSTALLATION_REQUIRES_REVIEW"
            )

    # No draft recipe can become executable.
    result["execution_allowed"] = False

    return result
