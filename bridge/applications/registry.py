"""Registry of DSSA application definitions.

Stores application declarations without installing or executing software.
"""

from bridge.applications.contracts import ApplicationDefinition
from copy import deepcopy

class ApplicationRegistry:
    """Maintain an explicit catalog of registered applications."""

    def __init__(self):
        self._definitions = {}

    def register(self, definition):
        """Register an application without replacing an existing entry."""

        if not isinstance(definition, ApplicationDefinition):
            raise ValueError("Invalid application definition")

        application_id = definition.application_id

        if application_id in self._definitions:
            raise ValueError(
                f"Application already registered: {application_id}"
            )
        self._definitions[application_id] = deepcopy(definition)

    def get(self, application_id):
        """Retrieve one registered application."""

        if not isinstance(application_id, str) or not application_id:
            raise ValueError("Invalid application ID")

        if application_id not in self._definitions:
            raise ValueError(
                f"Unknown application: {application_id}"
            )

        return deepcopy(self._definitions[application_id])

    def list_applications(self):
        """Return registered application identifiers."""

        return tuple(sorted(self._definitions))

    def definitions(self):
        """Return application definitions in stable ID order."""

        return tuple(
            deepcopy(self._definitions[application_id])
            for application_id in self.list_applications()
        )

    def __contains__(self, application_id):
        return application_id in self._definitions

    def __len__(self):
        return len(self._definitions)
