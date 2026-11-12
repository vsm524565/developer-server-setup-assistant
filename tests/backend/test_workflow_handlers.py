
"""Tests for explicit DSSA workflow handler dispatch."""

import unittest
from unittest.mock import patch

from bridge.workflows.handlers import (
    COMPOSE_READINESS_HANDLER,
    DOCKER_READINESS_HANDLER,
    list_implemented_handlers,
    resolve_workflow_handler,
)
from bridge.workflows.registry import build_workflow_registry
from bridge.workflows.coordinator import assess_application_workflow


class TestWorkflowHandlers(unittest.TestCase):
    """Ensure advertised workflows have safe implementations."""


    def test_docker_and_compose_have_implemented_handlers(self):
        """Expose only the two implemented read-only workflows."""
        expected = (
            ("docker", "readiness_assessment", "inventory"),
            ("docker_compose", "readiness_assessment", "inventory"),
        )

        self.assertEqual(list_implemented_handlers(), expected)

        self.assertEqual(
            resolve_workflow_handler(*expected[0]),
            DOCKER_READINESS_HANDLER,
        )

        self.assertEqual(
            resolve_workflow_handler(*expected[1]),
            COMPOSE_READINESS_HANDLER,
        )

        for key in (
            ("nginx", "readiness_assessment", "inventory"),
            ("docker", "installation", "inventory"),
            ("docker_compose", "installation", "inventory"),
            ("docker_compose", "readiness_assessment", "ssh"),
        ):
            with self.subTest(key=key):
                self.assertIsNone(resolve_workflow_handler(*key))

    def test_default_capabilities_have_implemented_handlers(self):
        registry = build_workflow_registry()

        for capability in registry.list_capabilities():
            with self.subTest(capability=capability):
                handler = resolve_workflow_handler(
                    capability["application_id"],
                    capability["workflow"],
                    capability["target_kind"],
                )

                self.assertIsNotNone(handler)
                self.assertIs(
                    capability["planning_only"], True
                )
                self.assertIs(
                    capability["execution_allowed"], False
                )

    def test_unknown_handler_rejected_before_discovery(self):
        request = {
            "schema_version": "1.0",
            "workflow": "readiness_assessment",
            "target": {
                "kind": "inventory",
                "id": "ubuntu24",
            },
            "application_id": "docker",
            "max_age_seconds": 120,
        }

        with (
            patch(
                "bridge.workflows.coordinator."
                "resolve_workflow_handler",
                return_value=None,
            ),
            patch(
                "bridge.workflows.coordinator."
                "assess_daemon_aware_readiness"
            ) as assess,
            patch(
                "bridge.workflows.coordinator."
                "explain_docker_readiness"
            ) as explain,
        ):
            with self.assertRaises(ValueError):
                assess_application_workflow(
                    object(),
                    request,
                )

            assess.assert_not_called()
            explain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
