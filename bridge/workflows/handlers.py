
"""Explicit catalog of implemented read-only workflow handlers."""

from types import MappingProxyType


DOCKER_READINESS_HANDLER = "docker_readiness_v1"

# Only combinations with an implemented coordinator path belong here.
_IMPLEMENTED_HANDLERS = MappingProxyType({
    (
        "docker",
        "readiness_assessment",
        "inventory",
    ): DOCKER_READINESS_HANDLER,
})


def list_implemented_handlers():
    """Return stable application/workflow/target combinations."""
    return tuple(sorted(_IMPLEMENTED_HANDLERS))


def resolve_workflow_handler(application_id, workflow, target_kind):
    """Resolve an implemented handler without backend discovery."""
    try:
        key = (application_id, workflow, target_kind)
        return _IMPLEMENTED_HANDLERS.get(key)
    except TypeError:
        return None
