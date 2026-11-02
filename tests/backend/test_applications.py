"""Consolidated read-only application architecture tests."""

import unittest
from unittest.mock import Mock

from bridge.applications.catalog import build_application_catalog
from bridge.applications.contracts import (
    ApplicationDefinition,
    CompatibilityRequirement,
    ConflictRule,
    VerificationRequirement,
)
from bridge.applications.component_mapping import (
    resolve_legacy_dependencies,
    to_legacy_component_name,
)
from bridge.applications.compatibility import evaluate_compatibility
from bridge.applications.conflicts import evaluate_conflicts
from bridge.applications.evidence_mapping import (
    map_panel_observations,
    map_port_observations,
)
from bridge.applications.evidence_coordinator import (
    collect_application_evidence,
)
from bridge.applications.plan_binding import evaluate_application_plan
from bridge.applications.planner_adapter import (
    to_legacy_planner_decision,
)
from bridge.applications.planning import assess_application


class TestApplicationCatalog(unittest.TestCase):

    def test_catalog_contains_23_draft_applications(self):
        registry = build_application_catalog()

        self.assertEqual(len(registry), 23)
        self.assertTrue(all(
            item.metadata["policy_state"] == "DRAFT"
            for item in registry.definitions()
        ))

    def test_duplicate_registration_rejected(self):
        registry = build_application_catalog()

        with self.assertRaises(ValueError):
            registry.register(registry.get("nginx"))

    def test_registry_returns_defensive_copy(self):
        registry = build_application_catalog()
        definition = registry.get("nginx")
        definition.metadata["policy_state"] = "COMPLETE"

        self.assertEqual(
            registry.get("nginx").metadata["policy_state"],
            "DRAFT",
        )


class TestApplicationPolicies(unittest.TestCase):

    def setUp(self):
        self.definition = ApplicationDefinition(
            application_id="test_app",
            display_name="Test Application",
            category="webserver",
            compatibility=CompatibilityRequirement(
                os_ids=("ubuntu",),
                os_versions=("24.04",),
                architectures=("x86_64",),
            ),
            conflicts=(
                ConflictRule(
                    conflict_id="existing_service",
                    kind="service",
                    name="Existing service",
                ),
            ),
            verification=(
                VerificationRequirement(
                    check_id="service_health",
                    check_type="service",
                    target="test_app",
                ),
            ),
            metadata={"policy_state": "DRAFT"},
        )
        self.system = {
            "os_id": "ubuntu",
            "os_version": "24.04",
            "architecture": "x86_64",
        }

    def test_supported_system(self):
        result = evaluate_compatibility(
            self.definition, self.system
        )
        self.assertEqual(result["status"], "SUPPORTED")

    def test_unsupported_system(self):
        system = dict(self.system, os_version="26.04")
        result = evaluate_compatibility(
            self.definition, system
        )
        self.assertEqual(result["status"], "UNSUPPORTED")

    def test_unknown_conflict_blocks(self):
        result = evaluate_conflicts(self.definition, {})
        self.assertEqual(result["status"], "BLOCKED")

    def test_confirmed_conflict_blocks_plan(self):
        result = evaluate_application_plan(
            self.definition,
            system_info=self.system,
            prerequisite_observations={},
            conflict_observations={"existing_service": True},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])

    def test_draft_cannot_be_plan_ready(self):
        result = evaluate_application_plan(
            self.definition,
            system_info=self.system,
            prerequisite_observations={},
            conflict_observations={"existing_service": False},
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_legacy_adapter_never_emits_install_ready(self):
        for status in (
            "BLOCKED",
            "REVIEW_REQUIRED",
            "PREREQUISITES_MISSING",
            "PLAN_READY",
        ):
            assessment = {
                "schema_version": "1.0",
                "check": "application_plan_assessment",
                "application_id": "test_app",
                "status": status,
                "reasons": [],
                "planning_only": True,
                "execution_allowed": False,
            }
            decision = to_legacy_planner_decision(assessment)
            self.assertNotEqual(decision["status"], "READY")


class TestApplicationMapping(unittest.TestCase):

    def test_docker_compose_dependency(self):
        result = resolve_legacy_dependencies(
            ["docker_compose"]
        )
        self.assertEqual(
            result["ordered_applications"],
            ["docker", "docker_compose"],
        )
        self.assertFalse(result["execution_allowed"])

    def test_unmapped_component_rejected(self):
        with self.assertRaises(ValueError):
            to_legacy_component_name("hestia")

    def test_listening_port_observations(self):
        result = map_port_observations(
            {"listening_tcp_ports": [22, 80]},
            (80, 443),
        )
        self.assertTrue(result["tcp_port.80.listening"])
        self.assertFalse(result["tcp_port.443.listening"])

    def test_invalid_port_rejected(self):
        with self.assertRaises(ValueError):
            map_port_observations(
                {"listening_tcp_ports": [70000]},
                (80,),
            )

    def test_panel_path_is_only_an_indicator(self):
        result = map_panel_observations(
            {"panels": ["hestia"]}
        )
        self.assertTrue(
            result["hestia.path_indicator_present"]
        )
        self.assertNotIn("hestia.service_active", result)


class TestApplicationEvidence(unittest.TestCase):

    def test_rejected_gate_prevents_collection(self):
        backend = Mock()
        backend.discover_for_planning.return_value = {
            "gate": {
                "schema_version": "1.0",
                "check": "evidence_gate",
                "status": "REJECTED",
                "planning_allowed": False,
                "execution_allowed": False,
            },
            "evidence": {},
        }

        with self.assertRaises(RuntimeError):
            collect_application_evidence(
                backend,
                "ubuntu24",
                operations=("discovery.system",),
            )

    def test_wrong_target_evidence_rejected(self):
        backend = Mock()
        backend.discover_for_planning.return_value = {
            "gate": {
                "schema_version": "1.0",
                "check": "evidence_gate",
                "status": "ACCEPTED",
                "planning_allowed": True,
                "execution_allowed": False,
            },
            "evidence": {
                "schema_version": "1.0",
                "request_id": "request-1",
                "operation": "discovery.system",
                "target_id": "another-server",
                "parameters": {},
                "execution_allowed": False,
                "data": {},
                "collection": {
                    "clock_source": "controller",
                },
            },
        }

        with self.assertRaises(RuntimeError):
            collect_application_evidence(
                backend,
                "ubuntu24",
                operations=("discovery.system",),
            )

    def test_draft_assessment_never_executes(self):
        backend = Mock()
        backend.discover_for_planning.side_effect = RuntimeError(
            "Evidence unavailable"
        )

        with self.assertRaises(RuntimeError):
            assess_application(
                backend, "ubuntu24", "nginx"
            )

        backend.setup_components.assert_not_called()
    def test_fake_accepted_gate_does_not_bypass_freshness(self):
        backend = Mock()

        backend.discover_for_planning.return_value = {
            "gate": {
                "schema_version": "1.0",
                "check": "evidence_gate",
                "status": "ACCEPTED",
                "planning_allowed": True,
                "execution_allowed": False,
            },
            "evidence": {
                "schema_version": "1.0",
                "request_id": "stale-request",
                "operation": "discovery.system",
                "target_id": "ubuntu24",
                "parameters": {},
                "execution_allowed": False,
                "data": {},
                "collection": {
                    "started_at_utc": "2020-01-01T00:00:00+00:00",
                    "completed_at_utc": "2020-01-01T00:00:01+00:00",
                    "duration_ms": 1000,
                    "clock_source": "controller",
                },
            },
        }

        with self.assertRaises(RuntimeError):
            collect_application_evidence(
                backend,
                "ubuntu24",
                operations=("discovery.system",),
            )

if __name__ == "__main__":
    unittest.main()
