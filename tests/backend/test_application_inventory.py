
"""Read-only application inventory normalization regression tests."""

from copy import deepcopy
import unittest

from bridge.applications.inventory.normalizer import (
    normalize_application_inventory,
    normalize_application_inventory_evidence,
)


CANDIDATES = {
    "git": ("git",),
    "nginx": ("nginx",),
    "docker": (
        "docker-ce",
        "docker-ce-cli",
        "docker.io",
        "moby-engine",
        "moby-cli",
    ),
    "docker_compose": (
        "docker-compose-plugin",
        "docker-compose-v2",
        "docker-compose",
    ),
}


def inventory(
    application="git",
    installed=None,
    unknown=(),
    binary_present=False,
    owner=None,
    manager="apt",
):
    """Construct synthetic inventory matching the collector schema."""

    installed = installed or {}

    binary_name = (
        "docker" if application in ("docker", "docker_compose")
        else application
    )

    binary_path = (
        f"/usr/bin/{binary_name}" if binary_present else None
    )

    owner_raw = None

    if owner is not None:
        if manager == "apt":
            owner_raw = f"{owner}: {binary_path}"
        else:
            owner_raw = owner

    return {
        "schema_version": "1.0",
        "application_id": application,
        "package_manager": manager,
        "packages": [
            {
                "name": name,
                "state": (
                    "installed" if name in installed
                    else "unknown" if name in unknown
                    else "absent"
                ),
                "version": installed.get(name),
            }
            for name in CANDIDATES[application]
        ],
        "binary": {
            "name": binary_name,
            "path": binary_path,
            "present": binary_present,
            "ownership": {
                "status": (
                    "reported" if owner is not None
                    else "unknown"
                ),
                "raw": owner_raw,
            },
        },
        "read_only": True,
        "execution_allowed": False,
    }


def evidence_wrapper(data, target="ubuntu24"):
    """Construct synthetic accepted evidence for identity tests."""

    return {
        "gate": {"status": "ACCEPTED"},
        "evidence": {
            "request_id": "inventory-test-request",
            "operation": "discovery.application_inventory",
            "target_id": target,
            "parameters": {
                "application_id": data["application_id"],
            },
            "data": deepcopy(data),
        },
        "execution_allowed": False,
    }


class TestApplicationInventory(unittest.TestCase):

    def normalize(self, data):
        return normalize_application_inventory(
            data,
            expected_application_id=data["application_id"],
        )

    def test_git_identified(self):
        data = inventory(
            installed={"git": "1:2.53.0-1ubuntu1.1"},
            binary_present=True,
            owner="git",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "IDENTIFIED")
        self.assertEqual(
            result["installation_method"],
            "distribution_package",
        )
        self.assertTrue(result["binary"]["ownership_verified"])
        self.assertFalse(result["execution_allowed"])

    def test_nginx_absent(self):
        result = self.normalize(inventory("nginx"))

        self.assertEqual(result["inventory_status"], "ABSENT")
        self.assertIsNone(result["installation_method"])

    def test_docker_io_identified(self):
        data = inventory(
            "docker",
            installed={"docker.io": "29.1.3-0ubuntu4.1"},
            binary_present=True,
            owner="docker.io",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "IDENTIFIED")
        self.assertEqual(
            result["installation_method"],
            "distribution_package",
        )
        self.assertEqual(
            result["binary"]["owner_package"],
            "docker.io",
        )

    def test_docker_vendor_identified(self):
        data = inventory(
            "docker",
            installed={
                "docker-ce": "29.0.0",
                "docker-ce-cli": "29.0.0",
            },
            binary_present=True,
            owner="docker-ce-cli",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "IDENTIFIED")
        self.assertEqual(
            result["installation_method"],
            "vendor_repository",
        )

    def test_multiple_docker_families_require_review(self):
        data = inventory(
            "docker",
            installed={
                "docker.io": "29.1.3",
                "docker-ce": "29.0.0",
            },
            binary_present=True,
            owner="docker.io",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")
        self.assertIn(
            "MULTIPLE_DOCKER_PACKAGE_FAMILIES",
            result["conflicts"],
        )

    def test_unexpected_binary_owner_requires_review(self):
        data = inventory(
            "git",
            installed={"git": "2.53"},
            binary_present=True,
            owner="unexpected-package",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")
        self.assertIn("BINARY_OWNER_MISMATCH", result["reasons"])

    def test_unknown_binary_owner_requires_review(self):
        data = inventory(
            "git",
            installed={"git": "2.53"},
            binary_present=True,
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")

    def test_binary_without_recognized_package(self):
        data = inventory(
            "git",
            binary_present=True,
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")

    def test_installed_package_without_binary(self):
        data = inventory(
            "git",
            installed={"git": "2.53"},
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")

    def test_unknown_package_prevents_absent(self):
        data = inventory(
            "docker",
            unknown=("docker.io",),
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "UNKNOWN")
        self.assertIn("docker.io", result["unknown_packages"])

    def test_installed_package_unknown_version_requires_review(self):
        data = inventory(
            "git",
            installed={"git": None},
            binary_present=True,
            owner="git",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")
        self.assertIn(
            "INSTALLED_PACKAGE_VERSION_UNKNOWN",
            result["reasons"],
        )

    def test_compose_absence_remains_unknown(self):
        result = self.normalize(inventory("docker_compose"))

        self.assertEqual(result["inventory_status"], "UNKNOWN")
        self.assertIn(
            "COMPOSE_AVAILABILITY_NOT_VERIFIED",
            result["reasons"],
        )

    def test_compose_package_requires_plugin_verification(self):
        data = inventory(
            "docker_compose",
            installed={"docker-compose-v2": "2.39.0"},
            binary_present=True,
            owner="docker.io",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "REVIEW_REQUIRED")
        self.assertIsNone(result["installation_method"])

    def test_mismatched_application_identity_rejected(self):
        data = inventory("nginx")

        with self.assertRaises(ValueError):
            normalize_application_inventory(
                data,
                expected_application_id="git",
            )

    def test_incomplete_package_candidates_rejected(self):
        data = inventory("docker")
        data["packages"].pop()

        with self.assertRaises(ValueError):
            self.normalize(data)

    def test_inconsistent_binary_path_rejected(self):
        data = inventory("git")
        data["binary"]["path"] = "/usr/bin/git"

        with self.assertRaises(ValueError):
            self.normalize(data)

    def test_dnf_ownership_identified(self):
        data = inventory(
            "git",
            installed={"git": "2.47.0-1.el10"},
            binary_present=True,
            owner="git",
            manager="dnf",
        )

        result = self.normalize(data)

        self.assertEqual(result["inventory_status"], "IDENTIFIED")
        self.assertEqual(result["binary"]["owner_package"], "git")

    def test_matching_accepted_evidence(self):
        data = inventory(
            "git",
            installed={"git": "2.53"},
            binary_present=True,
            owner="git",
        )

        result = normalize_application_inventory_evidence(
            evidence_wrapper(data),
            expected_application_id="git",
            expected_target_id="ubuntu24",
        )

        self.assertEqual(result["inventory_status"], "IDENTIFIED")
        self.assertEqual(result["target_id"], "ubuntu24")
        self.assertFalse(result["execution_allowed"])

    def test_rejected_evidence_gate(self):
        wrapped = evidence_wrapper(inventory("git"))
        wrapped["gate"]["status"] = "REJECTED"

        with self.assertRaises(ValueError):
            normalize_application_inventory_evidence(
                wrapped,
                expected_application_id="git",
                expected_target_id="ubuntu24",
            )

    def test_mismatched_target_rejected(self):
        wrapped = evidence_wrapper(inventory("git"))

        with self.assertRaises(ValueError):
            normalize_application_inventory_evidence(
                wrapped,
                expected_application_id="git",
                expected_target_id="debian13",
            )

    def test_mismatched_application_parameter_rejected(self):
        wrapped = evidence_wrapper(inventory("git"))
        wrapped["evidence"]["parameters"] = {
            "application_id": "nginx",
        }

        with self.assertRaises(ValueError):
            normalize_application_inventory_evidence(
                wrapped,
                expected_application_id="git",
                expected_target_id="ubuntu24",
            )


if __name__ == "__main__":
    unittest.main()
