
"""Read-only application-state classification regression tests."""

from copy import deepcopy
import unittest

from bridge.applications.state.interpreter import (
    classify_application_evidence,
    classify_application_state,
)


def sample_observation(
    application="git",
    package_state="installed",
    binary_present=True,
    service_state="not_applicable",
    plugin_state="not_applicable",
):
    """Build a valid synthetic application-state observation."""

    packages = {
        "git": "git",
        "nginx": "nginx",
        "docker": "docker-ce",
        "docker_compose": "docker-compose-plugin",
    }

    binaries = {
        "git": "/usr/bin/git",
        "nginx": "/usr/sbin/nginx",
        "docker": "/usr/bin/docker",
        "docker_compose": "/usr/bin/docker",
    }

    return {
        "schema_version": "1.0",
        "application_id": application,
        "package_manager": "apt",
        "package": {
            "name": packages[application],
            "state": package_state,
        },
        "binary": {
            "present": binary_present,
            "path": binaries[application] if binary_present else None,
        },
        "service": {
            "state": service_state,
        },
        "plugin": {
            "state": plugin_state,
        },
        "read_only": True,
        "execution_allowed": False,
    }


def accepted_evidence(data, application="git", target="ubuntu24"):
    """Wrap an observation in synthetic accepted controller evidence."""

    return {
        "gate": {"status": "ACCEPTED"},
        "evidence": {
            "request_id": "test-application-state-1",
            "operation": "discovery.application_state",
            "target_id": target,
            "parameters": {"application_id": application},
            "data": deepcopy(data),
        },
        "execution_allowed": False,
    }


class TestApplicationState(unittest.TestCase):

    def classify(self, data, application="git"):
        return classify_application_state(
            data,
            expected_application_id=application,
        )

    def test_managed_git_basic_health(self):
        result = self.classify(sample_observation())

        self.assertEqual(result["state"], "INSTALLED_HEALTHY")
        self.assertTrue(result["planning_only"])
        self.assertFalse(result["execution_allowed"])

    def test_missing_git(self):
        data = sample_observation(
            package_state="absent",
            binary_present=False,
        )

        result = self.classify(data)
        self.assertEqual(result["state"], "MISSING")

    def test_unmanaged_git_binary(self):
        data = sample_observation(package_state="absent")

        result = self.classify(data)
        self.assertEqual(result["state"], "UNMANAGED")

    def test_managed_git_missing_binary(self):
        data = sample_observation(
            package_state="installed",
            binary_present=False,
        )

        result = self.classify(data)
        self.assertEqual(result["state"], "UNHEALTHY")

    def test_nginx_active(self):
        data = sample_observation(
            application="nginx",
            service_state="active",
        )

        result = self.classify(data, "nginx")
        self.assertEqual(result["state"], "INSTALLED_HEALTHY")

    def test_nginx_inactive(self):
        data = sample_observation(
            application="nginx",
            service_state="inactive",
        )

        result = self.classify(data, "nginx")
        self.assertEqual(result["state"], "UNHEALTHY")

    def test_nginx_service_unknown(self):
        data = sample_observation(
            application="nginx",
            service_state="unknown",
        )

        result = self.classify(data, "nginx")
        self.assertEqual(result["state"], "UNKNOWN")

    def test_package_state_unknown(self):
        data = sample_observation(package_state="unknown")

        result = self.classify(data)
        self.assertEqual(result["state"], "UNKNOWN")

    def test_docker_absence_not_proven(self):
        data = sample_observation(
            application="docker",
            package_state="absent",
            binary_present=False,
            service_state="inactive",
        )

        result = self.classify(data, "docker")

        self.assertEqual(result["state"], "UNKNOWN")
        self.assertFalse(result["execution_allowed"])

    def test_docker_alternative_installation_unknown(self):
        data = sample_observation(
            application="docker",
            package_state="absent",
            binary_present=True,
            service_state="active",
        )

        result = self.classify(data, "docker")
        self.assertEqual(result["state"], "UNKNOWN")

    def test_compose_plugin_without_vendor_package(self):
        data = sample_observation(
            application="docker_compose",
            package_state="absent",
            plugin_state="available",
        )

        result = self.classify(data, "docker_compose")
        self.assertEqual(result["state"], "UNKNOWN")

    def test_mismatched_application_rejected(self):
        data = sample_observation(application="nginx")

        with self.assertRaises(ValueError):
            self.classify(data, "git")

    def test_mismatched_package_rejected(self):
        data = sample_observation()
        data["package"]["name"] = "nginx"

        with self.assertRaises(ValueError):
            self.classify(data)

    def test_inconsistent_binary_rejected(self):
        data = sample_observation()
        data["binary"]["present"] = False

        with self.assertRaises(ValueError):
            self.classify(data)

    def test_execution_permission_rejected(self):
        data = sample_observation()
        data["execution_allowed"] = True

        with self.assertRaises(ValueError):
            self.classify(data)

    def test_accepted_matching_evidence(self):
        data = sample_observation()
        evidence = accepted_evidence(data)

        result = classify_application_evidence(
            evidence,
            expected_application_id="git",
            expected_target_id="ubuntu24",
        )

        self.assertEqual(result["state"], "INSTALLED_HEALTHY")
        self.assertEqual(result["target_id"], "ubuntu24")
        self.assertEqual(
            result["evidence_request_id"],
            "test-application-state-1",
        )
        self.assertFalse(result["execution_allowed"])

    def test_rejected_gate(self):
        evidence = accepted_evidence(sample_observation())
        evidence["gate"]["status"] = "REJECTED"

        with self.assertRaises(ValueError):
            classify_application_evidence(
                evidence,
                expected_application_id="git",
                expected_target_id="ubuntu24",
            )

    def test_mismatched_target(self):
        evidence = accepted_evidence(sample_observation())

        with self.assertRaises(ValueError):
            classify_application_evidence(
                evidence,
                expected_application_id="git",
                expected_target_id="debian13",
            )

    def test_mismatched_evidence_parameters(self):
        evidence = accepted_evidence(sample_observation())
        evidence["evidence"]["parameters"] = {
            "application_id": "nginx",
        }

        with self.assertRaises(ValueError):
            classify_application_evidence(
                evidence,
                expected_application_id="git",
                expected_target_id="ubuntu24",
            )


if __name__ == "__main__":
    unittest.main()
