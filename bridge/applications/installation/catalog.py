"""Build the explicit read-only installation recipe catalog."""

from bridge.applications.installation.registry import (
    InstallationRecipeRegistry,
)
from bridge.applications.installation.recipes import RECIPE_BUILDERS


def build_installation_recipe_catalog():
    """Register all approved-for-catalog draft recipe definitions."""

    registry = InstallationRecipeRegistry()

    for builder in RECIPE_BUILDERS:
        registry.register(builder())

    return registry
