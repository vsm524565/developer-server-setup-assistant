
"""Evaluate application readiness using existing read-only assessments."""

APPLICATIONS = frozenset({
    "git",
    "nginx",
    "docker",
    "docker_compose",
})

STATES = frozenset({
    "INSTALLED_HEALTHY",
    "MISSING",
    "UNMANAGED",
    "UNHEALTHY",
    "UNKNOWN",
})

INVENTORY_STATUSES = frozenset({
    "IDENTIFIED",
    "ABSENT",
    "REVIEW_REQUIRED",
    "UNKNOWN",
})

PLAN_STATUSES = frozenset({
    "REVIEW_REQUIRED",
    "BLOCKED",
})


def evaluate_application_readiness(
    *,
    target_id,
    application_id,
    state,
    inventory,
    draft_plan,
):
    """Combine compatible read-only assessments into one decision."""

    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("Invalid target ID.")

    if application_id not in APPLICATIONS:
        raise ValueError("Unsupported application ID.")

    for name, value in (
        ("state", state),
        ("inventory", inventory),
        ("draft_plan", draft_plan),
    ):
        if not isinstance(value, dict):
            raise ValueError(f"Invalid {name} assessment.")

        if value.get("application_id") != application_id:
            raise ValueError(f"{name} application mismatch.")

        if value.get("execution_allowed") is not False:
            raise ValueError(f"{name} permits execution.")

        if value.get("planning_only") is not True:
            raise ValueError(f"{name} is not planning-only.")

        if value.get("target_id") != target_id:
            raise ValueError(f"{name} target mismatch.")

    state_status = state.get("state")
    inventory_status = inventory.get("inventory_status")
    plan_status = draft_plan.get("status")

    if state_status not in STATES:
        raise ValueError("Invalid application state.")

    if inventory_status not in INVENTORY_STATUSES:
        raise ValueError("Invalid inventory status.")

    if plan_status not in PLAN_STATUSES:
        raise ValueError("Invalid draft plan status.")

    # Validate the draft plan consistently for every readiness outcome.
    recipe_id = draft_plan.get("recipe_id")
    dependencies = draft_plan.get("dependencies")
    plan_reasons = draft_plan.get("reasons")

    if recipe_id is not None and (
        not isinstance(recipe_id, str) or not recipe_id.strip()
    ):
        raise ValueError("Invalid installation recipe identity.")

    if not isinstance(dependencies, list):
        raise ValueError("Invalid installation dependencies.")

    if not isinstance(plan_reasons, list) or any(
        not isinstance(reason, str) or not reason
        for reason in plan_reasons
    ):
        raise ValueError("Invalid installation plan reasons.")

    allowed_dependency_statuses = {
        "SATISFIED",
        "DEPENDENCY_PLAN_REQUIRED",
        "REVIEW_REQUIRED",
        "BLOCKED",
    }

    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise ValueError("Invalid installation dependency.")

        if dependency.get("status") not in allowed_dependency_statuses:
            raise ValueError("Invalid dependency readiness status.")

    reasons = []
    status = "REVIEW_REQUIRED"

    # Existing package and binary evidence must agree with runtime state.
    if (
        state_status == "INSTALLED_HEALTHY"
        and inventory_status == "IDENTIFIED"
    ):
        status = "READY_INSTALLED"
        reasons.append("STATE_AND_INVENTORY_AGREE")

    elif (
        state_status == "MISSING"
        and inventory_status == "ABSENT"
    ):
        # Only consider a draft installation when absence is consistent.
        if plan_status == "BLOCKED":
            status = "BLOCKED"
            reasons.append("INSTALLATION_PLAN_BLOCKED")

        elif draft_plan.get("recipe_id") is None:
            reasons.append("NO_SELECTED_INSTALLATION_RECIPE")

        else:
            dependencies = draft_plan.get("dependencies")

            if not isinstance(dependencies, list):
                raise ValueError("Invalid recipe dependency assessments.")

            dependency_statuses = []

            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise ValueError("Invalid dependency entry.")

                dependency_status = dependency.get("status")

                if dependency_status not in (
                    "SATISFIED",
                    "DEPENDENCY_PLAN_REQUIRED",
                    "REVIEW_REQUIRED",
                    "BLOCKED",
                ):
                    raise ValueError("Invalid dependency status.")

                dependency_statuses.append(dependency_status)

            if "BLOCKED" in dependency_statuses:
                status = "BLOCKED"
                reasons.append("REQUIRED_DEPENDENCY_BLOCKED")

            elif any(
                value != "SATISFIED"
                for value in dependency_statuses
            ):
                reasons.append("DEPENDENCY_REVIEW_REQUIRED")

            else:
                plan_reasons = draft_plan.get("reasons")

                if not isinstance(plan_reasons, list):
                    raise ValueError("Invalid draft plan reasons.")

                # A draft recipe is not authorization to install.
                # Additional plan-level blockers must not be ignored.
                permitted_reasons = {"RECIPE_POLICY_DRAFT"}

                if (
                    any(
                        not isinstance(reason, str)
                        for reason in plan_reasons
                    )
                    or not set(plan_reasons).issubset(
                        permitted_reasons
                    )
                ):
                    reasons.append("INSTALLATION_PLAN_REQUIRES_REVIEW")

                else:
                    status = "INSTALL_CANDIDATE_DRAFT"
                    reasons.append("DRAFT_RECIPE_AVAILABLE")

    else:
        reasons.append("STATE_INVENTORY_RECONCILIATION_REQUIRED")

        if state_status == "UNHEALTHY":
            reasons.append("APPLICATION_HEALTH_UNCONFIRMED")

        if state_status == "UNMANAGED":
            reasons.append("UNMANAGED_INSTALLATION_DETECTED")

        if inventory_status == "REVIEW_REQUIRED":
            reasons.append("PACKAGE_INVENTORY_REQUIRES_REVIEW")

        if inventory_status == "UNKNOWN":
            reasons.append("PACKAGE_INVENTORY_INCOMPLETE")

    return {
        "schema_version": "1.0",
        "check": "application_readiness",
        "target_id": target_id,
        "application_id": application_id,
        "status": status,
        "application_state": state_status,
        "inventory_status": inventory_status,
        "draft_plan_status": plan_status,
        "recipe_id": draft_plan.get("recipe_id"),
        "reasons": reasons,
        "planning_only": True,
        "execution_allowed": False,
    }
