
"""Common application definition contracts for DSSA.

These contracts describe application requirements and verification rules.
They do not authorize installations or privileged execution.
"""

from dataclasses import dataclass, field
from typing import Any


APPLICATION_CATEGORIES = frozenset({
    "runtime",
    "webserver",
    "database",
    "cache",
    "container",
    "control_panel",
    "system",
    "tool",
    "proxy_ssl",
})

REQUIREMENT_LEVELS = frozenset({
    "REQUIRED",
    "OPTIONAL",
})

CONFLICT_SEVERITIES = frozenset({
    "BLOCKER",
    "WARNING",
})

VERIFICATION_TYPES = frozenset({
    "package",
    "command",
    "service",
    "port",
    "http",
    "configuration",
})


def _require_text(value, name):
    """Validate a non-empty string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class CompatibilityRequirement:
    """Declare supported operating systems and architectures."""

    os_ids: tuple[str, ...] = ()
    os_versions: tuple[str, ...] = ()
    architectures: tuple[str, ...] = ()
    min_memory_bytes: int | None = None
    min_disk_bytes: int | None = None

    def __post_init__(self):
        for name in ("os_ids", "os_versions", "architectures"):
            values = getattr(self, name)

            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a tuple")

            if any(
                not isinstance(value, str) or not value.strip()
                for value in values
            ):
                raise ValueError(f"Invalid {name}")

        for name in ("min_memory_bytes", "min_disk_bytes"):
            value = getattr(self, name)

            if value is not None and (
                type(value) is not int or value < 0
            ):
                raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class PrerequisiteRequirement:
    """Declare a dependency or prerequisite condition."""

    requirement_id: str
    kind: str
    name: str
    level: str = "REQUIRED"

    def __post_init__(self):
        _require_text(self.requirement_id, "requirement_id")
        _require_text(self.kind, "kind")
        _require_text(self.name, "name")

        if self.level not in REQUIREMENT_LEVELS:
            raise ValueError("Invalid prerequisite level")


@dataclass(frozen=True)
class ConflictRule:
    """Declare a condition that may prevent installation."""

    conflict_id: str
    kind: str
    name: str
    severity: str = "BLOCKER"
    reason: str = ""

    def __post_init__(self):
        _require_text(self.conflict_id, "conflict_id")
        _require_text(self.kind, "kind")
        _require_text(self.name, "name")

        if self.severity not in CONFLICT_SEVERITIES:
            raise ValueError("Invalid conflict severity")


@dataclass(frozen=True)
class VerificationRequirement:
    """Declare an application-specific post-installation check."""

    check_id: str
    check_type: str
    target: str
    required: bool = True

    def __post_init__(self):
        _require_text(self.check_id, "check_id")
        _require_text(self.target, "target")

        if self.check_type not in VERIFICATION_TYPES:
            raise ValueError("Invalid verification type")

        if type(self.required) is not bool:
            raise ValueError("required must be boolean")


@dataclass(frozen=True)
class ExpectedChange:
    """Describe an expected change without performing it."""

    change_id: str
    resource_type: str
    resource_name: str
    description: str

    def __post_init__(self):
        for name in (
            "change_id",
            "resource_type",
            "resource_name",
            "description",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True)
class ApplicationDefinition:
    """Reusable metadata and requirements for one application."""

    application_id: str
    display_name: str
    category: str

    compatibility: CompatibilityRequirement = field(
        default_factory=CompatibilityRequirement
    )

    prerequisites: tuple[PrerequisiteRequirement, ...] = ()
    conflicts: tuple[ConflictRule, ...] = ()
    expected_changes: tuple[ExpectedChange, ...] = ()
    verification: tuple[VerificationRequirement, ...] = ()

    metadata: dict[str, Any] = field(default_factory=dict)

    schema_version: str = "1.0"
    execution_allowed: bool = False

    def __post_init__(self):
        _require_text(self.application_id, "application_id")
        _require_text(self.display_name, "display_name")

        if self.category not in APPLICATION_CATEGORIES:
            raise ValueError("Invalid application category")

        if not isinstance(
            self.compatibility, CompatibilityRequirement
        ):
            raise ValueError("Invalid compatibility definition")

        definitions = (
            ("prerequisites", PrerequisiteRequirement, "requirement_id"),
            ("conflicts", ConflictRule, "conflict_id"),
            ("expected_changes", ExpectedChange, "change_id"),
            ("verification", VerificationRequirement, "check_id"),
        )

        for name, expected_type, identity_field in definitions:
            values = getattr(self, name)

            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a tuple")

            if not all(
                isinstance(value, expected_type)
                for value in values
            ):
                raise ValueError(f"Invalid {name}")

            identifiers = [
                getattr(value, identity_field)
                for value in values
            ]

            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Duplicate identifiers in {name}")

        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")

        if self.schema_version != "1.0":
            raise ValueError("Unsupported application schema")

        if self.execution_allowed is not False:
            raise ValueError(
                "Application definitions cannot authorize execution"
            )

