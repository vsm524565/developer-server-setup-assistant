"""Contracts for application-specific installation recipes.

Recipes describe an installation method and its requirements.
They do not execute commands or authorize installation.
"""

from dataclasses import dataclass, field
from typing import Tuple


PACKAGE_SOURCES = (
    "os_repository",
    "vendor_repository",
)

PACKAGE_MANAGERS = (
    "apt",
    "dnf",
)

EXPECTED_ACTIONS = (
    "package_install",
    "repository_configuration",
    "service_start",
    "service_enable",
    "configuration_change",
    "filesystem_change",
)

VERIFICATION_TYPES = (
    "package",
    "binary",
    "service",
    "configuration",
    "version",
)


@dataclass(frozen=True)
class PlatformRequirement:
    """Declare the exact platforms supported by a recipe."""

    os_ids: Tuple[str, ...]
    os_versions: Tuple[str, ...]
    architectures: Tuple[str, ...]
    package_manager: str

    def __post_init__(self):
        if not self.os_ids:
            raise ValueError("OS IDs are required")

        if not self.os_versions:
            raise ValueError("OS versions are required")

        if not self.architectures:
            raise ValueError("Architectures are required")

        if self.package_manager not in PACKAGE_MANAGERS:
            raise ValueError("Unsupported package manager")


@dataclass(frozen=True)
class PackageSource:
    """Describe the declared source of installation packages."""

    source_type: str
    packages: Tuple[str, ...]
    repository_id: str = ""

    def __post_init__(self):
        if self.source_type not in PACKAGE_SOURCES:
            raise ValueError("Unsupported package source")

        if not self.packages:
            raise ValueError("At least one package is required")

        if any(
            not isinstance(package, str) or not package.strip()
            for package in self.packages
        ):
            raise ValueError("Invalid package name")

        if self.source_type == "vendor_repository":
            if not self.repository_id:
                raise ValueError(
                    "Vendor repository ID is required"
                )


@dataclass(frozen=True)
class DependencyRequirement:
    """Reference another registered application dependency."""

    application_id: str
    required: bool = True

    def __post_init__(self):
        if not isinstance(self.application_id, str):
            raise ValueError("Invalid dependency application ID")

        if not self.application_id.strip():
            raise ValueError("Empty dependency application ID")


@dataclass(frozen=True)
class ExpectedOperation:
    """Declare a potential change without implementing it."""

    action: str
    target: str
    description: str

    def __post_init__(self):
        if self.action not in EXPECTED_ACTIONS:
            raise ValueError("Unsupported expected action")

        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("Invalid operation target")

        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Operation description is required")


@dataclass(frozen=True)
class VerificationStep:
    """Describe a verification requirement for later implementation."""

    check_type: str
    target: str
    description: str

    def __post_init__(self):
        if self.check_type not in VERIFICATION_TYPES:
            raise ValueError("Unsupported verification type")

        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("Invalid verification target")

        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Verification description is required")


@dataclass(frozen=True)
class InstallationRecipe:
    """Immutable description of one installation approach."""

    recipe_id: str
    application_id: str
    platform: PlatformRequirement
    package_source: PackageSource

    dependencies: Tuple[DependencyRequirement, ...] = ()
    expected_operations: Tuple[ExpectedOperation, ...] = ()
    verification_steps: Tuple[VerificationStep, ...] = ()

    policy_state: str = "DRAFT"
    planning_only: bool = True
    execution_allowed: bool = False

    def __post_init__(self):
        if not isinstance(self.recipe_id, str) or not self.recipe_id.strip():
            raise ValueError("Recipe ID is required")

        if (
            not isinstance(self.application_id, str)
            or not self.application_id.strip()
        ):
            raise ValueError("Application ID is required")

        if self.policy_state != "DRAFT":
            raise ValueError(
                "Milestone 4.12 only supports DRAFT recipes"
            )

        if self.planning_only is not True:
            raise ValueError("Recipes must remain planning-only")

        if self.execution_allowed is not False:
            raise ValueError(
                "Installation execution is not authorized"
            )

        if not self.verification_steps:
            raise ValueError(
                "At least one verification requirement is needed"
            )
