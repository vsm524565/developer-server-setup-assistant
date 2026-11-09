
"""Permanent tests for DSSA workflow capability registration."""

import unittest
from copy import deepcopy

from bridge.applications.catalog import build_application_catalog
from bridge.workflows.registry import (
    WorkflowCapabilityRegistry,
    build_workflow_registry,
)


def docker_capability():
    """Create an approved read-only Docker readiness declaration."""
    return {
        "application_id": "docker",
        "workflow": "readiness_assessment",
        "target_kind": "inventory",
        "planning_only": True,
        "execution_allowed": False,
    }


class TestWorkflowRegistry(unittest.TestCase):
    """Validate capability registration and discovery boundaries."""

    def setUp(self):
        self.catalog = build_application_catalog()
        self.registry = WorkflowCapabilityRegistry(self.catalog)

    def test_default_registry_contains_only_docker(self):
        registry = build_workflow_registry()
        capabilities = registry.list_capabilities()

        self.assertEqual(len(capabilities), 1)
        self.assertEqual(capabilities[0], docker_capability())

    def test_register_and_retrieve_capability(self):
        self.registry.register(docker_capability())

        result = self.registry.get(
            "docker", "readiness_assessment"
        )

        self.assertEqual(result, docker_capability())
        self.assertTrue(
            self.registry.supports(
                "docker", "readiness_assessment"
            )
        )

    def test_unregistered_applications_are_not_enabled(self):
        registry = build_workflow_registry()

        for application in (
            "nginx", "postgresql", "redis", "mysql"
        ):
            with self.subTest(application=application):
                self.assertFalse(
                    registry.supports(
                        application, "readiness_assessment"
                    )
                )

    def test_duplicate_registration_rejected(self):
        self.registry.register(docker_capability())

        with self.assertRaises(ValueError):
            self.registry.register(docker_capability())

    def test_unknown_application_rejected(self):
        capability = docker_capability()
        capability["application_id"] = "not_registered"

        with self.assertRaises(ValueError):
            self.registry.register(capability)

    def test_unsupported_workflows_rejected(self):
        for workflow in (
            "installation", "execution", "setup", None, []
        ):
            with self.subTest(workflow=workflow):
                capability = docker_capability()
                capability["workflow"] = workflow

                with self.assertRaises(ValueError):
                    self.registry.register(capability)

    def test_invalid_target_kind_rejected(self):
        for target_kind in ("ssh", "hostname", None):
            with self.subTest(target_kind=target_kind):
                capability = docker_capability()
                capability["target_kind"] = target_kind

                with self.assertRaises(ValueError):
                    self.registry.register(capability)

    def test_execution_authorization_rejected(self):
        modifications = (
            ("execution_allowed", True),
            ("execution_allowed", 1),
            ("planning_only", False),
            ("planning_only", 1),
        )

        for field, value in modifications:
            with self.subTest(field=field, value=value):
                capability = docker_capability()
                capability[field] = value

                with self.assertRaises(ValueError):
                    self.registry.register(capability)

    def test_missing_or_extra_fields_rejected(self):
        for field in docker_capability():
            with self.subTest(missing=field):
                capability = docker_capability()
                del capability[field]

                with self.assertRaises(ValueError):
                    self.registry.register(capability)

        capability = docker_capability()
        capability["password"] = "UNAUTHORIZED"

        with self.assertRaises(ValueError):
            self.registry.register(capability)

    def test_non_dictionary_capability_rejected(self):
        for value in (None, [], "docker", 42):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.registry.register(value)

    def test_registration_is_immutable_from_caller(self):
        capability = docker_capability()
        self.registry.register(capability)

        capability["execution_allowed"] = True

        stored = self.registry.get(
            "docker", "readiness_assessment"
        )

        self.assertFalse(stored["execution_allowed"])

    def test_get_returns_independent_copy(self):
        self.registry.register(docker_capability())

        first = self.registry.get(
            "docker", "readiness_assessment"
        )
        first["execution_allowed"] = True

        second = self.registry.get(
            "docker", "readiness_assessment"
        )

        self.assertFalse(second["execution_allowed"])

    def test_list_returns_independent_copies(self):
        self.registry.register(docker_capability())

        first = self.registry.list_capabilities()
        first[0]["execution_allowed"] = True

        second = self.registry.list_capabilities()

        self.assertFalse(second[0]["execution_allowed"])

    def test_unsupported_capability_lookup_rejected(self):
        self.registry.register(docker_capability())

        with self.assertRaises(ValueError):
            self.registry.get("nginx", "readiness_assessment")

        self.assertFalse(
            self.registry.supports("docker", "installation")
        )

    def test_registration_does_not_modify_application_catalog(self):
        before = deepcopy(self.catalog.list_applications())

        self.registry.register(docker_capability())

        self.assertEqual(
            self.catalog.list_applications(), before
        )


if __name__ == "__main__":
    unittest.main()
