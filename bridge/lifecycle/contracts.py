
"""Common lifecycle contracts for DSSA.

Contracts describe server lifecycle assessments and operation context.
They do not authorize installation, remediation, cleanup or execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "1.0"

READINESS_STATUSES = frozenset({
    "READY", "BLOCKED", "DEGRADED", "UNKNOWN", "UNSUPPORTED",
})

OPERATION_OUTCOMES = frozenset({
    "SUCCESS", "PARTIAL", "FAILED", "BLOCKED",
    "VERIFICATION_FAILED", "CANCELLED", "INSPECTED",
})

ARTIFACT_KINDS = frozenset({"file", "directory"})


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class OperationContext:
    request_id: str
    target_id: str
    operation: str
    approval_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for name in ("request_id", "target_id", "operation"):
            _nonempty(getattr(self, name), name)

        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Unsupported lifecycle schema version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreOperationAssessment:
    context: OperationContext
    status: str
    checks: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_request_ids: list[str] = field(default_factory=list)
    execution_allowed: bool = False

    def __post_init__(self):
        if self.status not in READINESS_STATUSES:
            raise ValueError("Invalid readiness status")

        if self.execution_allowed is not False:
            raise ValueError("Pre-operation assessment cannot authorize execution")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    path: str
    kind: str
    created_by_request_id: str
    temporary: bool = True
    cleanup_required: bool = True

    def __post_init__(self):
        _nonempty(self.artifact_id, "artifact_id")
        _nonempty(self.path, "path")
        _nonempty(self.created_by_request_id, "created_by_request_id")

        if self.kind not in ARTIFACT_KINDS:
            raise ValueError("Invalid artifact kind")

        if not self.path.startswith("/") or self.path == "/":
            raise ValueError("Artifact path must be absolute and non-root")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    context: OperationContext
    artifacts: list[ArtifactRecord] = field(default_factory=list)

    def __post_init__(self):
        identifiers = set()
        paths = set()

        for artifact in self.artifacts:
            if artifact.created_by_request_id != self.context.request_id:
                raise ValueError("Artifact request identity mismatch")

            if artifact.artifact_id in identifiers:
                raise ValueError("Duplicate artifact identifier")

            if artifact.path in paths:
                raise ValueError("Duplicate artifact path")

            identifiers.add(artifact.artifact_id)
            paths.add(artifact.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostOperationAssessment:
    context: OperationContext
    status: str
    health_checks: dict[str, Any] = field(default_factory=dict)
    observed_changes: list[dict[str, Any]] = field(default_factory=list)
    cleanup_status: str = "NOT_STARTED"
    cleanup_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_allowed: bool = False

    def __post_init__(self):
        if self.status not in READINESS_STATUSES:
            raise ValueError("Invalid post-operation assessment status")

        if self.cleanup_status not in {
            "NOT_STARTED", "PENDING", "COMPLETED",
            "PARTIAL", "FAILED",
        }:
            raise ValueError("Invalid cleanup status")

        if self.execution_allowed is not False:
            raise ValueError("Assessment cannot authorize execution")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleReport:
    context: OperationContext
    outcome: str
    pre_operation: PreOperationAssessment | None = None
    post_operation: PostOperationAssessment | None = None
    errors: list[str] = field(default_factory=list)
    recovery_required: bool = False
    execution_allowed: bool = False

    def __post_init__(self):
        if self.outcome not in OPERATION_OUTCOMES:
            raise ValueError("Invalid lifecycle outcome")

        if self.execution_allowed is not False:
            raise ValueError("Lifecycle report cannot authorize execution")

        for assessment in (self.pre_operation, self.post_operation):
            if assessment is not None and assessment.context != self.context:
                raise ValueError("Lifecycle context mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
