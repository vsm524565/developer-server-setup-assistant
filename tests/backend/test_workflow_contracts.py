
"""Tests for DSSA read-only workflow request contracts."""

import unittest
from copy import deepcopy

from bridge.workflows.contracts import (
    validate_workflow_request,
)


def valid_request():
    """Create a supported inventory-managed Docker request."""
    return {
        "schema_version": "1.0",
        "workflow": "readiness_assessment",
        "target": {
            "kind": "inventory",
            "id": "ubuntu24",
        },
        "application_id": "docker",
        "max_age_seconds": 120,
    }


class TestWorkflowContracts(unittest.TestCase):
    """Validate request identity, operation and safety restrictions."""

    def test_valid_request(self):
        request = valid_request()

        result = validate_workflow_request(request)

        self.assertEqual(result, request)

    def test_request_is_not_modified(self):
        request = valid_request()
        original = deepcopy(request)

        result = validate_workflow_request(request)

        self.assertEqual(request, original)
        self.assertIsNot(result, request)
        self.assertIsNot(result["target"], request["target"])

    def test_valid_freshness_boundaries(self):
        for value in (1, 30, 120, 300, 0.5):
            with self.subTest(value=value):
                request = valid_request()
                request["max_age_seconds"] = value

                result = validate_workflow_request(request)

                self.assertEqual(result["max_age_seconds"], value)

    def test_reject_invalid_freshness(self):
        values = (
            0, -1, 301, True, False,
            None, "120", [], {},
        )

        for value in values:
            with self.subTest(value=value):
                request = valid_request()
                request["max_age_seconds"] = value

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_unsupported_workflows(self):
        values = (
            "installation",
            "setup",
            "execution",
            "",
            None,
            [],
            {},
        )

        for value in values:
            with self.subTest(value=value):
                request = valid_request()
                request["workflow"] = value

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_unsupported_applications(self):
        values = (
            "nginx",
            "docker_compose",
            "",
            None,
            [],
            {},
        )

        for value in values:
            with self.subTest(value=value):
                request = valid_request()
                request["application_id"] = value

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_invalid_target_kinds(self):
        for kind in ("ssh", "hostname", "raw_ip", None):
            with self.subTest(kind=kind):
                request = valid_request()
                request["target"]["kind"] = kind

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_invalid_target_identifiers(self):
        values = (
            "",
            "../ubuntu24",
            "ubuntu 24",
            "192.168.56.111",
            "/etc/passwd",
            None,
            123,
            "a" * 129,
        )

        for value in values:
            with self.subTest(value=value):
                request = valid_request()
                request["target"]["id"] = value

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_unauthorized_fields(self):
        for field in (
            "password",
            "ssh_key",
            "execute",
            "approval_token",
            "install",
        ):
            with self.subTest(field=field):
                request = valid_request()
                request[field] = "UNAUTHORIZED"

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_missing_fields(self):
        for field in valid_request():
            with self.subTest(field=field):
                request = valid_request()
                del request[field]

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_malformed_target(self):
        for target in (None, [], "ubuntu24", {}):
            with self.subTest(target=target):
                request = valid_request()
                request["target"] = target

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_invalid_schema(self):
        for schema in ("2.0", "", None):
            with self.subTest(schema=schema):
                request = valid_request()
                request["schema_version"] = schema

                with self.assertRaises(ValueError):
                    validate_workflow_request(request)

    def test_reject_non_dictionary_requests(self):
        for value in (None, [], "docker", 42):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_workflow_request(value)


if __name__ == "__main__":
    unittest.main()
