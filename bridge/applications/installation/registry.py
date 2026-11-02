"""Registry for read-only application installation recipes."""

from dataclasses import fields
from typing import get_type_hints

from bridge.applications.catalog import build_application_catalog
from bridge.applications.installation.contracts import (
    DependencyRequirement,
    ExpectedOperation,
    InstallationRecipe,
    PackageSource,
    PlatformRequirement,
    VerificationStep,
)


def _validate_string_tuple(values, field_name):
    """Reject mutable, empty or malformed requirement collections."""

    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{field_name} must be a nonempty tuple")

    if any(
        not isinstance(value, str) or not value.strip()
        for value in values
    ):
        raise ValueError(f"Invalid {field_name}")

    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate values in {field_name}")


def validate_recipe(recipe, application_ids):
    """Validate a recipe and its nested declarations before registration."""

    if not isinstance(recipe, InstallationRecipe):
        raise ValueError("Invalid installation recipe")

    if recipe.application_id not in application_ids:
        raise ValueError(
            f"Unknown application: {recipe.application_id}"
        )

    if not isinstance(recipe.platform, PlatformRequirement):
        raise ValueError("Invalid platform requirement")

    if not isinstance(recipe.package_source, PackageSource):
        raise ValueError("Invalid package source")

    platform = recipe.platform
    source = recipe.package_source

    for name in ("os_ids", "os_versions", "architectures"):
        _validate_string_tuple(getattr(platform, name), name)

    _validate_string_tuple(source.packages, "packages")

    if not isinstance(recipe.dependencies, tuple):
        raise ValueError("Dependencies must be a tuple")

    if not isinstance(recipe.expected_operations, tuple):
        raise ValueError("Expected operations must be a tuple")

    if not isinstance(recipe.verification_steps, tuple):
        raise ValueError("Verification steps must be a tuple")

    if not recipe.verification_steps:
        raise ValueError("Verification steps are required")

    for dependency in recipe.dependencies:
        if not isinstance(dependency, DependencyRequirement):
            raise ValueError("Invalid dependency")

        if dependency.application_id not in application_ids:
            raise ValueError(
                f"Unknown dependency: {dependency.application_id}"
            )

        if dependency.application_id == recipe.application_id:
            raise ValueError("Self-dependency is prohibited")

    for operation in recipe.expected_operations:
        if not isinstance(operation, ExpectedOperation):
            raise ValueError("Invalid expected operation")

    for verification in recipe.verification_steps:
        if not isinstance(verification, VerificationStep):
            raise ValueError("Invalid verification step")

    if recipe.policy_state != "DRAFT":
        raise ValueError("Only DRAFT recipes are supported")

    if recipe.planning_only is not True:
        raise ValueError("Recipe must be planning-only")

    if recipe.execution_allowed is not False:
        raise ValueError("Execution cannot be authorized")

    return recipe


class InstallationRecipeRegistry:
    """Store validated installation recipes keyed by recipe ID."""

    def __init__(self, application_catalog=None):
        self._application_catalog = (
            application_catalog
            if application_catalog is not None
            else build_application_catalog()
        )
        self._recipes = {}

    def register(self, recipe):
        """Register one validated recipe without replacing another."""

        application_ids = {
            definition.application_id
            for definition in self._application_catalog.definitions()
        }

        validate_recipe(recipe, application_ids)

        if recipe.recipe_id in self._recipes:
            raise ValueError(
                f"Duplicate recipe ID: {recipe.recipe_id}"
            )

        self._recipes[recipe.recipe_id] = recipe

    def get(self, recipe_id):
        """Return a registered recipe or raise for unknown IDs."""

        if not isinstance(recipe_id, str):
            raise ValueError("Invalid recipe ID")

        try:
            return self._recipes[recipe_id]
        except KeyError as error:
            raise ValueError(
                f"Unknown recipe: {recipe_id}"
            ) from error

    def for_application(self, application_id):
        """Return all recipes registered for one application."""

        return tuple(
            recipe
            for recipe in self._recipes.values()
            if recipe.application_id == application_id
        )

    def definitions(self):
        """Return all registered recipes."""

        return tuple(self._recipes.values())

    def __len__(self):
        return len(self._recipes)
