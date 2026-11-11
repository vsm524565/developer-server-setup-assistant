
"""Tests for offline workflow capability discovery."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from bridge.workflows.discovery import (
    discover_workflow_capabilities,
    validate_capability_report,
)


def valid_report():
    """Return an expected Docker-only capability report."""
    return {
        "schema_version": "1.0",
        "check": "workflow_capabilities",
        "capabilities": [
            {
                "application_id": "docker",
                "workflow": "readiness_assessment",
                "target_kind": "inventory",
                "planning_only": True,
                "execution_allowed": False,
            }
        ],
    }


class TestWorkflowDiscovery(unittest.TestCase):
    """Validate offline discovery contracts and safety."""

    def test_default_discovery(self):
        self.assertEqual(
            discover_workflow_capabilities(),
            valid_report(),
        )

    def test_report_validation(self):
        report = valid_report()

        self.assertEqual(
            validate_capability_report(report),
            report,
        )

    def test_validation_returns_independent_copy(self):
        report = valid_report()

        result = validate_capability_report(report)
        result["capabilities"][0]["execution_allowed"] = True

        self.assertFalse(
            report["capabilities"][0]["execution_allowed"]
        )

    def test_offline_discovery_does_not_create_backend(self):
        with patch(
            "bridge.adapters.linux_backend.LinuxBackend",
            side_effect=AssertionError(
                "Backend must not be instantiated."
            ),
        ):
            self.assertEqual(
                discover_workflow_capabilities(),
                valid_report(),
            )

    def test_invalid_schema_rejected(self):
        report = valid_report()
        report["schema_version"] = "2.0"

        with self.assertRaises(ValueError):
            validate_capability_report(report)

    def test_unexpected_fields_rejected(self):
        report = valid_report()
        report["ssh_password"] = "UNAUTHORIZED"

        with self.assertRaises(ValueError):
            validate_capability_report(report)

    def test_invalid_capability_fields_rejected(self):
        report = valid_report()
        report["capabilities"][0]["install"] = True

        with self.assertRaises(ValueError):
            validate_capability_report(report)

    def test_execution_authorization_rejected(self):
        for field, value in (
            ("execution_allowed", True),
            ("execution_allowed", 1),
            ("planning_only", False),
            ("planning_only", 1),
        ):
            with self.subTest(field=field, value=value):
                report = valid_report()
                report["capabilities"][0][field] = value

                with self.assertRaises(ValueError):
                    validate_capability_report(report)

    def test_unknown_handler_rejected(self):
        report = valid_report()
        report["capabilities"][0]["application_id"] = "nginx"

        with self.assertRaises(ValueError):
            validate_capability_report(report)

    def test_duplicate_capability_rejected(self):
        report = valid_report()

        report["capabilities"].append(
            deepcopy(report["capabilities"][0])
        )

        with self.assertRaises(ValueError):
            validate_capability_report(report)

    def test_malformed_report_rejected(self):
        for report in (None, [], "docker", 42, {}):
            with self.subTest(report=report):
                with self.assertRaises(ValueError):
                    validate_capability_report(report)

    def test_empty_capability_list_is_valid(self):
        report = valid_report()
        report["capabilities"] = []

        self.assertEqual(
            validate_capability_report(report),
            report,
        )

    def test_unsorted_capabilities_rejected(self):
        """Reject unsorted reports regardless of set iteration order."""
        report = valid_report()

        report["capabilities"].append({
            "application_id": "apache",
            "workflow": "readiness_assessment",
            "target_kind": "inventory",
            "planning_only": True,
            "execution_allowed": False,
        })

        with patch(
            "bridge.workflows.discovery.resolve_workflow_handler",
            return_value="test_readonly_handler",
        ):
            with self.assertRaisesRegex(ValueError, "not sorted"):
                validate_capability_report(report)

    def test_sorted_capabilities_accepted(self):
        """Accept ordered reports with independently verified handlers."""
        report = valid_report()

        report["capabilities"].insert(0, {
            "application_id": "apache",
            "workflow": "readiness_assessment",
            "target_kind": "inventory",
            "planning_only": True,
            "execution_allowed": False,
        })

        with patch(
            "bridge.workflows.discovery.resolve_workflow_handler",
            return_value="test_readonly_handler",
        ):
            result = validate_capability_report(report)

        self.assertEqual(result, report)

    def test_discovery_normalizes_registry_order(self):
        """Sort advertised capabilities before validating the report."""
        docker = valid_report()["capabilities"][0]

        apache = {
            "application_id": "apache",
            "workflow": "readiness_assessment",
            "target_kind": "inventory",
            "planning_only": True,
            "execution_allowed": False,
        }

        with (
            patch(
                "bridge.workflows.discovery.build_workflow_registry"
            ) as builder,
            patch(
                "bridge.workflows.discovery.resolve_workflow_handler",
                return_value="test_readonly_handler",
            ),
        ):
            builder.return_value.list_capabilities.return_value = (
                docker,
                apache,
            )

            report = discover_workflow_capabilities()

        self.assertEqual(
            [item["application_id"] for item in report["capabilities"]],
            ["apache", "docker"],
        )

    def test_repeated_discovery_is_deterministic(self):
        """Produce equal reports across consecutive offline calls."""
        first = discover_workflow_capabilities()
        second = discover_workflow_capabilities()

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(
            first["capabilities"][0],
            second["capabilities"][0],
        )


if __name__ == "__main__":
    unittest.main()
