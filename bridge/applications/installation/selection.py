"""Select draft recipes by package manager and platform policy."""


def _validate_system_info(system_info):
    """Require normalized and complete system observations."""

    if not isinstance(system_info, dict):
        raise ValueError("System information must be an object")

    required = (
        "os_id",
        "os_version",
        "architecture",
        "package_manager",
    )

    for field in required:
        value = system_info.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Missing or invalid system field: {field}"
            )


def _matches_platform(platform, system_info):
    """Match package manager first, then declared platform constraints."""

    if system_info["package_manager"] != platform.package_manager:
        return False

    if system_info["os_id"] not in platform.os_ids:
        return False

    if system_info["architecture"] not in platform.architectures:
        return False

    return (
        "*" in platform.os_versions
        or system_info["os_version"] in platform.os_versions
    )


def select_installation_recipe(
    registry,
    application_id,
    system_info,
):
    """Select exactly one draft recipe without authorizing execution."""

    _validate_system_info(system_info)

    if not isinstance(application_id, str) or not application_id.strip():
        raise ValueError("Invalid application ID")

    candidates = registry.for_application(application_id)

    matching = [
        recipe
        for recipe in candidates
        if _matches_platform(recipe.platform, system_info)
    ]

    result = {
        "schema_version": "1.0",
        "check": "installation_recipe_selection",
        "application_id": application_id,
        "status": None,
        "recipe_id": None,
        "reasons": [],
        "planning_only": True,
        "execution_allowed": False,
    }

    if not matching:
        result["status"] = "UNSUPPORTED_PLATFORM"
        result["reasons"].append("NO_MATCHING_RECIPE")

    elif len(matching) > 1:
        result["status"] = "AMBIGUOUS_MATCH"
        result["reasons"].append("MULTIPLE_MATCHING_RECIPES")

    else:
        result["status"] = "MATCHED_DRAFT"
        result["recipe_id"] = matching[0].recipe_id
        result["reasons"].append("RECIPE_POLICY_DRAFT")

    return result
