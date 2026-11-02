
"""Read-only installation recipe and planning regression tests."""

import unittest
from unittest.mock import Mock, patch

from bridge.applications.installation.catalog import (
    build_installation_recipe_catalog,
)
from bridge.applications.installation.evidence_binding import (
    normalize_installation_platform,
)
from bridge.applications.installation.plan import (
    build_draft_installation_plan,
)
from bridge.applications.installation.prerequisites import (
    evaluate_recipe_prerequisites,
)
from bridge.applications.installation.selection import (
    select_installation_recipe,
)


PLATFORM = {
    "os_id": "ubuntu",
    "os_version": "24.04",
    "architecture": "x86_64",
    "package_manager": "apt",
}


def fake_evidence(
    application,
    observations=None,
    docker_state="UNKNOWN",
    platform=None,
):
    """Build synthetic normalized planning evidence for unit tests."""

    return {
        "status": "OBSERVED",
        "application_id": application,
        "target_id": "ubuntu24",
        "platform": dict(platform if platform is not None else PLATFORM),
        "observations": dict(observations or {}),
        "dependency_states": {"docker": docker_state},
        "evidence_request_ids": {
            "discovery.system": "test-request-1",
        },
        "planning_only": True,
        "execution_allowed": False,
    }


class TestInstallationRecipes(unittest.TestCase):

    def setUp(self):
        self.registry = build_installation_recipe_catalog()

    def test_six_draft_recipes_registered(self):
        self.assertEqual(len(self.registry), 6)

        applications = {
            recipe.application_id
            for recipe in self.registry.definitions()
        }

        self.assertEqual(
            applications,
            {"git", "nginx", "docker", "docker_compose"},
        )

        self.assertTrue(all(
            recipe.policy_state == "DRAFT"
            and recipe.planning_only is True
            and recipe.execution_allowed is False
            for recipe in self.registry.definitions()
        ))

    def test_exact_platform_selection(self):
        """All four applications match our Ubuntu 24.04 lab."""

        for application in (
            "git",
            "nginx",
            "docker",
            "docker_compose",
        ):
            result = select_installation_recipe(
                self.registry,
                application,
                PLATFORM,
            )

            self.assertEqual(result["status"], "MATCHED_DRAFT")
            self.assertIsNotNone(result["recipe_id"])
            self.assertFalse(result["execution_allowed"])

    def test_git_supports_declared_apt_platform_releases(self):
        """Git uses an APT recipe across declared Ubuntu releases."""

        for version in ("22.04", "24.04", "26.04"):
            result = select_installation_recipe(
                self.registry,
                "git",
                {**PLATFORM, "os_version": version},
            )

            self.assertEqual(result["status"], "MATCHED_DRAFT")
            self.assertEqual(
                result["recipe_id"],
                "git-apt-os-repository",
            )
            self.assertFalse(result["execution_allowed"])

    def test_nginx_supports_declared_apt_platform_releases(self):
        """Nginx uses an APT recipe across declared Ubuntu releases."""

        for version in ("22.04", "24.04", "26.04"):
            result = select_installation_recipe(
                self.registry,
                "nginx",
                {**PLATFORM, "os_version": version},
            )

            self.assertEqual(result["status"], "MATCHED_DRAFT")
            self.assertEqual(
                result["recipe_id"],
                "nginx-apt-os-repository",
            )

    def test_debian_apt_recipe_selection(self):
        """Debian uses the declared APT recipes."""

        system = {
            "os_id": "debian",
            "os_version": "13",
            "architecture": "x86_64",
            "package_manager": "apt",
        }

        for application in ("git", "nginx"):
            result = select_installation_recipe(
                self.registry,
                application,
                system,
            )

            self.assertEqual(result["status"], "MATCHED_DRAFT")
            self.assertFalse(result["execution_allowed"])

    def test_dnf_recipe_selection(self):
        """AlmaLinux and Rocky use DNF draft recipes."""

        for os_id in ("almalinux", "rocky"):
            system = {
                "os_id": os_id,
                "os_version": "10.2",
                "architecture": "x86_64",
                "package_manager": "dnf",
            }

            for application in ("git", "nginx"):
                result = select_installation_recipe(
                    self.registry,
                    application,
                    system,
                )

                self.assertEqual(
                    result["status"],
                    "MATCHED_DRAFT",
                )
                self.assertFalse(result["execution_allowed"])

    def test_docker_remains_version_restricted(self):
        """Vendor-repository Docker is still narrowly scoped."""

        for application in ("docker", "docker_compose"):
            result = select_installation_recipe(
                self.registry,
                application,
                {**PLATFORM, "os_version": "26.04"},
            )

            self.assertEqual(
                result["status"],
                "UNSUPPORTED_PLATFORM",
            )
            self.assertFalse(result["execution_allowed"])

    def test_docker_vendor_recipe_not_selected_on_dnf(self):
        """An APT vendor recipe must never match a DNF server."""

        system = {
            "os_id": "almalinux",
            "os_version": "10.2",
            "architecture": "x86_64",
            "package_manager": "dnf",
        }

        for application in ("docker", "docker_compose"):
            result = select_installation_recipe(
                self.registry,
                application,
                system,
            )

            self.assertEqual(
                result["status"],
                "UNSUPPORTED_PLATFORM",
            )

    def test_package_manager_mismatch_rejected(self):
        """Matching OS alone cannot select the wrong manager."""

        result = select_installation_recipe(
            self.registry,
            "git",
            {**PLATFORM, "package_manager": "dnf"},
        )

        self.assertEqual(
            result["status"],
            "UNSUPPORTED_PLATFORM",
        )

    def test_package_manager_tools_normalized(self):
        result = normalize_installation_platform(
            {
                "os_id": "ubuntu",
                "os_version": "24.04",
                "architecture": "x86_64",
            },
            {
                "package_manager_detected": True,
                "available_package_tools": ["apt-get", "dpkg"],
            },
        )

        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["platform"], PLATFORM)

    def test_dnf_package_manager_tools_normalized(self):
        result = normalize_installation_platform(
            {
                "os_id": "almalinux",
                "os_version": "10.2",
                "architecture": "x86_64",
            },
            {
                "package_manager_detected": True,
                "available_package_tools": ["dnf", "rpm"],
            },
        )

        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(
            result["platform"]["package_manager"],
            "dnf",
        )

    def test_inconsistent_package_manager_incomplete(self):
        result = normalize_installation_platform(
            {
                "os_id": "ubuntu",
                "os_version": "24.04",
                "architecture": "x86_64",
            },
            {
                "package_manager_detected": True,
                "available_package_tools": ["dnf", "rpm"],
            },
        )

        self.assertEqual(result["status"], "INCOMPLETE")

    def test_unknown_docker_requires_review(self):
        recipe = self.registry.for_application("docker_compose")[0]

        result = evaluate_recipe_prerequisites(
            recipe,
            {"docker": "UNKNOWN"},
            self.registry,
            PLATFORM,
        )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_missing_docker_requires_dependency_plan(self):
        recipe = self.registry.for_application("docker_compose")[0]

        result = evaluate_recipe_prerequisites(
            recipe,
            {"docker": "MISSING"},
            self.registry,
            PLATFORM,
        )

        self.assertEqual(
            result["status"],
            "DEPENDENCY_PLAN_REQUIRED",
        )
        self.assertFalse(result["execution_allowed"])

    def test_unmanaged_docker_requires_review(self):
        recipe = self.registry.for_application("docker_compose")[0]

        result = evaluate_recipe_prerequisites(
            recipe,
            {"docker": "UNMANAGED"},
            self.registry,
            PLATFORM,
        )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_unhealthy_docker_requires_review(self):
        recipe = self.registry.for_application("docker_compose")[0]

        result = evaluate_recipe_prerequisites(
            recipe,
            {"docker": "UNHEALTHY"},
            self.registry,
            PLATFORM,
        )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_healthy_docker_dependency_satisfied(self):
        """A synthetic verified state satisfies dependency policy."""

        recipe = self.registry.for_application("docker_compose")[0]

        result = evaluate_recipe_prerequisites(
            recipe,
            {"docker": "INSTALLED_HEALTHY"},
            self.registry,
            PLATFORM,
        )

        self.assertEqual(result["status"], "SATISFIED_DRAFT")
        self.assertEqual(
            result["dependencies"][0]["status"],
            "SATISFIED",
        )
        self.assertFalse(result["execution_allowed"])

    def test_plan_draft_reason_not_duplicated(self):
        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=fake_evidence("git"),
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "git",
            )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(
            result["reasons"].count("RECIPE_POLICY_DRAFT"),
            1,
        )
        self.assertFalse(result["execution_allowed"])

    def test_nginx_occupied_port_blocks(self):
        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=fake_evidence(
                "nginx",
                {"tcp_port.80.listening": True},
            ),
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "nginx",
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])

    def test_nginx_panel_indicator_blocks(self):
        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=fake_evidence(
                "nginx",
                {"hestia.path_indicator_present": True},
            ),
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "nginx",
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])

    def test_compose_unknown_dependency_not_satisfied(self):
        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=fake_evidence("docker_compose"),
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "docker_compose",
            )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(
            result["dependencies"][0]["observed_state"],
            "UNKNOWN",
        )
        self.assertFalse(result["execution_allowed"])

    def test_unsupported_system_plan_blocked(self):
        """Docker is blocked when no matching vendor recipe exists."""

        evidence = fake_evidence("docker")
        evidence["platform"]["os_version"] = "26.04"

        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=evidence,
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "docker",
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])

    def test_git_plan_matches_newer_ubuntu(self):
        """A generic Git APT recipe may be selected for review."""

        system = {
            **PLATFORM,
            "os_version": "26.04",
        }

        with patch(
            "bridge.applications.installation.plan.collect_installation_evidence",
            return_value=fake_evidence(
                "git",
                platform=system,
            ),
        ):
            result = build_draft_installation_plan(
                Mock(),
                "ubuntu24",
                "git",
            )

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(
            result["recipe_id"],
            "git-apt-os-repository",
        )
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
