
"""Read-only temporary artifact cleanup planning for DSSA."""

from pathlib import PurePosixPath

from bridge.lifecycle.contracts import ArtifactManifest, OperationContext


def _validate_path(path):
    """Validate an unambiguous absolute POSIX path."""

    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Artifact path must be absolute")

    if "\x00" in path or "\n" in path or "\r" in path:
        raise ValueError("Artifact path contains unsafe characters")

    parts = path.split("/")

    if any(part in (".", "..", "") for part in parts[1:]):
        raise ValueError("Artifact path contains unsafe components")

    normalized = str(PurePosixPath(path))

    if normalized != path or normalized == "/":
        raise ValueError("Artifact path is not canonical")

    return normalized


def plan_artifact_cleanup(
    context: OperationContext,
    manifest: ArtifactManifest,
    *,
    staging_root: str,
):
    """Plan cleanup of DSSA-owned temporary artifacts without deleting them."""

    if not isinstance(context, OperationContext):
        raise ValueError("Invalid lifecycle context")

    if not isinstance(manifest, ArtifactManifest):
        raise ValueError("Invalid artifact manifest")

    if manifest.context != context:
        raise ValueError("Artifact manifest lifecycle mismatch")

    root = _validate_path(staging_root)

    if not root.startswith("/tmp/dssa-"):
        raise ValueError("Staging root is not an approved DSSA path")

    # This is a planning restriction, not proof of filesystem ownership.
    if root.count("/") != 2:
        raise ValueError("Staging root must be a dedicated directory")

    planned = []
    skipped = []
    seen = set()

    for artifact in manifest.artifacts:
        path = _validate_path(artifact.path)

        if artifact.created_by_request_id != context.request_id:
            raise ValueError("Artifact ownership request mismatch")

        if path == root or not path.startswith(root + "/"):
            raise ValueError("Artifact is outside its staging directory")

        if path in seen:
            raise ValueError("Duplicate artifact path")

        seen.add(path)

        if not artifact.temporary or not artifact.cleanup_required:
            skipped.append({
                "artifact_id": artifact.artifact_id,
                "path": path,
                "reason": "NOT_MARKED_FOR_CLEANUP",
            })
            continue

        planned.append({
            "artifact_id": artifact.artifact_id,
            "path": path,
            "kind": artifact.kind,
            "action": "PROPOSE_DELETE",
            "ownership_verified": False,
            "filesystem_verified": False,
            "execution_allowed": False,
        })

    # Detect overlapping paths, including directory/child combinations.
    all_paths = sorted(seen)

    for index, path in enumerate(all_paths):
        for other in all_paths[index + 1:]:
            if other.startswith(path + "/"):
                raise ValueError("Overlapping artifact paths")

    return {
        "schema_version": "1.0",
        "check": "artifact_cleanup_plan",
        "lifecycle_request_id": context.request_id,
        "target_id": context.target_id,
        "staging_root": root,
        "status": "PLANNED" if planned else "NOT_REQUIRED",
        "planned": planned,
        "skipped": skipped,
        "ownership_verified": False,
        "filesystem_verified": False,
        "execution_allowed": False,
    }
