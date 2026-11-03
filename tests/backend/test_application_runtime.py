
"""Regression tests for read-only Docker runtime interpretation."""

from copy import deepcopy
import unittest

from bridge.applications.runtime.interpreter import (
    classify_application_runtime,
)


def runtime_data(
    application="docker",
    *,
    cli_status="absent",
    cli_path=None,
    version=None,
    service="inactive",
    socket="inactive",
    plugin=None,
):
    """Build synthetic runtime discovery observations."""

    if application == "docker_compose":
        service = "not_applicable"
        socket = "not_applicable"
        if plugin is None:
            plugin = "unknown"
    elif plugin is None:
        plugin = "not_applicable"

    return {
        "schema_version": "1.0",
        "application_id": application,
        "cli": {
            "path": cli_path,
            "status": cli_status,
            "version_output": version,
        },
        "service": {"state": service},
        "socket": {"state": socket},
        "plugin": {"state": plugin},
        "daemon_responsive": None,
        "read_only": True,
        "execution_allowed": False,
    }


def classify(data):
    """Classify a synthetic runtime observation."""

    return classify_application_runtime(
        data,
        expected_application_id=data["application_id"],
    )


class TestApplicationRuntime(unittest.TestCase):

    def test_docker_cli_absent(self):
        result = classify(runtime_data())

        self.assertEqual(result["status"], "CLI_ABSENT")
        self.assertIsNone(result["daemon_responsive"])
        self.assertFalse(result["execution_allowed"])

    def test_docker_cli_available_service_active(self):
        data = runtime_data(
            cli_status="available",
            cli_path="/usr/bin/docker",
            version="Docker version 29.1.3",
            service="active",
            socket="active",
        )

        result = classify(data)

        self.assertEqual(result["status"], "CLI_AVAILABLE")
        self.assertIn("DOCKER_SERVICE_ACTIVE", result["reasons"])
        self.assertIn("DOCKER_DAEMON_NOT_PROBED", result["reasons"])
        self.assertIsNone(result["daemon_responsive"])

    def test_failed_service_does_not_imply_healthy(self):
        data = runtime_data(
            cli_status="available",
            cli_path="/usr/bin/docker",
            version="Docker version 29.1.3",
            service="failed",
        )

        result = classify(data)

        self.assertEqual(result["status"], "CLI_AVAILABLE")
        self.assertIn("DOCKER_SERVICE_FAILED", result["reasons"])
        self.assertIsNone(result["daemon_responsive"])

    def test_docker_cli_unverified(self):
        data = runtime_data(
            cli_status="unknown",
            cli_path="/usr/local/bin/docker",
        )

        result = classify(data)

        self.assertEqual(result["status"], "UNVERIFIED")

    def test_compose_plugin_available(self):
        data = runtime_data(
            "docker_compose",
            cli_status="available",
            cli_path="/usr/bin/docker",
            version="Docker Compose version v5.6.1",
            plugin="available",
        )

        result = classify(data)

        self.assertEqual(result["status"], "PLUGIN_AVAILABLE")
        self.assertIsNone(result["daemon_responsive"])

    def test_compose_plugin_unknown(self):
        result = classify(runtime_data("docker_compose"))

        self.assertEqual(result["status"], "UNVERIFIED")

    def test_compose_cli_present_plugin_unverified(self):
        data = runtime_data(
            "docker_compose",
            cli_status="unknown",
            cli_path="/usr/bin/docker",
        )

        result = classify(data)

        self.assertEqual(result["status"], "UNVERIFIED")

    def test_compose_plugin_cli_mismatch_rejected(self):
        data = runtime_data(
            "docker_compose",
            cli_status="unknown",
            cli_path="/usr/bin/docker",
            plugin="available",
        )

        with self.assertRaises(ValueError):
            classify(data)

    def test_runtime_application_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            classify_application_runtime(
                runtime_data("docker"),
                expected_application_id="docker_compose",
            )

    def test_execution_permission_rejected(self):
        data = runtime_data()
        data["execution_allowed"] = True

        with self.assertRaises(ValueError):
            classify(data)

    def test_daemon_responsive_value_rejected(self):
        data = runtime_data()
        data["daemon_responsive"] = True

        with self.assertRaises(ValueError):
            classify(data)

    def test_invalid_cli_path_rejected(self):
        data = runtime_data(
            cli_status="available",
            cli_path="docker",
            version="Docker version 29.1.3",
        )

        with self.assertRaises(ValueError):
            classify(data)

    def test_version_without_verified_cli_rejected(self):
        data = runtime_data(
            cli_status="unknown",
            cli_path="/usr/bin/docker",
            version="Docker version 29.1.3",
        )

        with self.assertRaises(ValueError):
            classify(data)

    def test_unexpected_runtime_field_rejected(self):
        data = runtime_data()
        data["unexpected"] = True

        with self.assertRaises(ValueError):
            classify(data)

    def test_inputs_remain_unchanged(self):
        data = runtime_data(
            cli_status="available",
            cli_path="/usr/bin/docker",
            version="Docker version 29.1.3",
            service="active",
        )
        original = deepcopy(data)

        classify(data)

        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
