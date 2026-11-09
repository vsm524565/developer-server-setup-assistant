
"""Explicit registry of DSSA read-only workflow capabilities."""

from copy import deepcopy

from bridge.applications.catalog import build_application_catalog
from bridge.workflows.handlers import list_implemented_handlers

class WorkflowCapabilityRegistry:
    """Register and expose approved read-only workflow capabilities."""

    def __init__(self, application_catalog):
        if not hasattr(application_catalog, "list_applications"):
            raise ValueError("Invalid application catalog.")

        self._application_ids = frozenset(
            application_catalog.list_applications()
        )
        self._capabilities = {}

    def register(self, capability):
        """Register a capability without permitting execution authority."""

        if not isinstance(capability, dict):
            raise ValueError("Capability must be an object.")

        required_fields = {
            "application_id",
            "workflow",
            "target_kind",
            "planning_only",
            "execution_allowed",
        }

        if set(capability) != required_fields:
            raise ValueError("Invalid capability fields.")

        application_id = capability["application_id"]
        workflow = capability["workflow"]
        target_kind = capability["target_kind"]

        if (
            not isinstance(application_id, str)
            or application_id not in self._application_ids
        ):
            raise ValueError("Application is not registered.")

        if workflow != "readiness_assessment":
            raise ValueError("Unsupported workflow capability.")

        if target_kind != "inventory":
            raise ValueError("Unsupported capability target kind.")

        if (
            capability["planning_only"] is not True
            or capability["execution_allowed"] is not False
        ):
            raise ValueError("Execution-enabled capabilities are forbidden.")

        key = (application_id, workflow, target_kind)

        if key in self._capabilities:
            raise ValueError("Workflow capability already registered.")

        self._capabilities[key] = deepcopy(capability)

    def get(self, application_id, workflow, target_kind="inventory"):
        """Return an independently copied registered capability."""

        key = (application_id, workflow, target_kind)

        try:
            return deepcopy(self._capabilities[key])
        except (KeyError, TypeError):
            raise ValueError("Unsupported workflow capability.") from None

    def supports(self, application_id, workflow, target_kind="inventory"):
        """Check support without invoking backend discovery."""

        try:
            self.get(application_id, workflow, target_kind)
        except ValueError:
            return False

        return True

    def list_capabilities(self):
        """Return capabilities in stable order without shared references."""

        return tuple(
            deepcopy(self._capabilities[key])
            for key in sorted(self._capabilities)
        )

def build_workflow_registry():
    """Advertise only workflows with implemented handlers."""

    registry = WorkflowCapabilityRegistry(
        build_application_catalog()
    )

    for application_id, workflow, target_kind in (
        list_implemented_handlers()
    ):
        registry.register({
            "application_id": application_id,
            "workflow": workflow,
            "target_kind": target_kind,
            "planning_only": True,
            "execution_allowed": False,
        })

    return registry


