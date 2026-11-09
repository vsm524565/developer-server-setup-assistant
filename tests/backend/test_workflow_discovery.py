
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


if __name__ == "__main__":
    unittest.main()
