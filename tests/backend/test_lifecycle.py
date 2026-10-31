
"""Consolidated read-only lifecycle security and contract tests."""

import copy
import unittest

from bridge.lifecycle.contracts import (
    ArtifactManifest,
    ArtifactRecord,
    OperationContext,
    PreOperationAssessment,
    PostOperationAssessment,
)
from bridge.lifecycle.artifact_cleanup import plan_artifact_cleanup
from bridge.lifecycle.baseline_comparison import compare_baselines
from bridge.lifecycle.reporting import build_lifecycle_report
from bridge.lifecycle.post_operation import evaluate_post_operation


class LifecycleSecurityTests(unittest.TestCase):

    def setUp(self):
        self.context = OperationContext(
            request_id="lifecycle-security-001",
            target_id="ubuntu24",
            operation="lifecycle.read_only_verification",
        )

    def test_execution_cannot_be_authorized(self):
        with self.assertRaises(ValueError):
            PreOperationAssessment(
                context=self.context,
                status="READY",
                execution_allowed=True,
            )

        with self.assertRaises(ValueError):
            PostOperationAssessment(
                context=self.context,
                status="READY",
                execution_allowed=True,
            )

    def test_artifact_outside_staging_is_rejected(self):
        manifest = ArtifactManifest(
            context=self.context,
            artifacts=[
                ArtifactRecord(
                    artifact_id="outside",
                    path="/etc/nginx/nginx.conf",
                    kind="file",
                    created_by_request_id=self.context.request_id,
                )
            ],
        )

        with self.assertRaises(ValueError):
            plan_artifact_cleanup(
                self.context,
                manifest,
                staging_root="/tmp/dssa-security001",
            )

    def test_artifact_traversal_is_rejected(self):
        manifest = ArtifactManifest(
            context=self.context,
            artifacts=[
                ArtifactRecord(
                    artifact_id="traversal",
                    path="/tmp/dssa-security001/../important",
                    kind="file",
                    created_by_request_id=self.context.request_id,
                )
            ],
        )

        with self.assertRaises(ValueError):
            plan_artifact_cleanup(
                self.context,
                manifest,
                staging_root="/tmp/dssa-security001",
            )

    def test_artifact_request_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            ArtifactManifest(
                context=self.context,
                artifacts=[
                    ArtifactRecord(
                        artifact_id="foreign",
                        path="/tmp/dssa-security001/script.sh",
                        kind="file",
                        created_by_request_id="another-request",
                    )
                ],
            )

    def test_overlapping_artifacts_are_rejected(self):
        manifest = ArtifactManifest(
            context=self.context,
            artifacts=[
                ArtifactRecord(
                    artifact_id="directory",
                    path="/tmp/dssa-security001/work",
                    kind="directory",
                    created_by_request_id=self.context.request_id,
                ),
                ArtifactRecord(
                    artifact_id="file",
                    path="/tmp/dssa-security001/work/run.sh",
                    kind="file",
                    created_by_request_id=self.context.request_id,
                ),
            ],
        )

        with self.assertRaises(ValueError):
            plan_artifact_cleanup(
                self.context,
                manifest,
                staging_root="/tmp/dssa-security001",
            )

    def test_cleanup_plan_never_authorizes_deletion(self):
        manifest = ArtifactManifest(
            context=self.context,
            artifacts=[
                ArtifactRecord(
                    artifact_id="script",
                    path="/tmp/dssa-security001/run.sh",
                    kind="file",
                    created_by_request_id=self.context.request_id,
                ),
            ],
        )

        plan = plan_artifact_cleanup(
            self.context,
            manifest,
            staging_root="/tmp/dssa-security001",
        )

        self.assertEqual(plan["status"], "PLANNED")
        self.assertIs(plan["execution_allowed"], False)
        self.assertIs(plan["ownership_verified"], False)
        self.assertIs(plan["filesystem_verified"], False)
        self.assertIs(plan["planned"][0]["execution_allowed"], False)

    def test_baseline_comparison_requires_matching_types(self):
        before = {
            "hostname": "ubuntu24",
            "os_id": "ubuntu",
            "os_version": "24.04",
            "architecture": "x86_64",
            "cpu_logical_cores": 2,
            "memory_total_bytes": 2043559936,
            "swap_total_bytes": 0,
            "disk_total_bytes": 25130254336,
            "disk_available_bytes": 20816863232,
        }

        after = copy.deepcopy(before)
        after["memory_total_bytes"] = "2043559936"

        with self.assertRaises(ValueError):
            compare_baselines(before, after)

    def test_mismatched_post_operation_evidence_is_rejected(self):
        health = PreOperationAssessment(
            context=self.context,
            status="READY",
            evidence_request_ids=["different-after-id"],
        )

        binding = {
            "schema_version": "1.0",
            "check": "system_evidence_binding",
            "target_id": "ubuntu24",
            "before_request_id": "before-001",
            "after_request_id": "after-001",
            "comparison": {
                "schema_version": "1.0",
                "check": "baseline_comparison",
                "status": "UNCHANGED",
                "changes": [],
                "execution_allowed": False,
            },
            "execution_allowed": False,
        }

        with self.assertRaises(ValueError):
            evaluate_post_operation(
                self.context,
                health_assessment=health,
                evidence_binding=binding,
            )

    def test_inspection_and_operation_outcomes_are_distinct(self):
        pre = PreOperationAssessment(
            context=self.context,
            status="DEGRADED",
        )
        post = PostOperationAssessment(
            context=self.context,
            status="DEGRADED",
        )

        inspected = build_lifecycle_report(
            self.context,
            pre_operation=pre,
            post_operation=post,
            operation_state="INSPECTED",
        )

        blocked = build_lifecycle_report(
            self.context,
            pre_operation=pre,
            operation_state="NOT_STARTED",
        )

        self.assertEqual(inspected.outcome, "INSPECTED")
        self.assertEqual(blocked.outcome, "BLOCKED")
        self.assertIs(inspected.execution_allowed, False)
    def test_foreign_acceptance_record_is_rejected(self):
        from bridge.lifecycle.evidence_binding import (
            compare_accepted_system_evidence,
        )

        foreign = {
            "schema_version": "1.0",
            "check": "pre_operation_system_acceptance",
            "lifecycle_request_id": "another-request",
            "target_id": self.context.target_id,
            "operation": self.context.operation,
            "evidence_request_id": "before-001",
            "execution_allowed": False,
        }

        with self.assertRaises(ValueError):
            compare_accepted_system_evidence(
                self.context,
                accepted_before=foreign,
                after_evidence={},
            )

    def test_pre_operation_collection_failure_is_blocked(self):
        from bridge.lifecycle.coordinator import run_read_only_lifecycle

        class FailingBackend:
            def discover_with_evidence(self, operation, target):
                raise RuntimeError("Simulated connection failure")

        report = run_read_only_lifecycle(
            self.context,
            FailingBackend(),
        )

        self.assertEqual(report.outcome, "BLOCKED")
        self.assertIsNone(report.post_operation)
        self.assertIs(report.execution_allowed, False)
        self.assertTrue(report.errors)

    def test_post_operation_collection_failure_is_reported(self):
        from unittest.mock import patch
        from bridge.lifecycle import coordinator

        class PostFailureBackend:
            def __init__(self):
                self.calls = 0

            def discover_with_evidence(self, operation, target):
                self.calls += 1

                if self.calls > 3:
                    raise RuntimeError("Simulated post-operation failure")

                return {
                    "operation": operation,
                    "target_id": target,
                }

        valid_pre = PreOperationAssessment(
            context=self.context,
            status="READY",
            evidence_request_ids=[
                "bootstrap-001",
                "health-001",
                "system-001",
            ],
        )

        accepted = {
            "schema_version": "1.0",
            "check": "pre_operation_system_acceptance",
            "lifecycle_request_id": self.context.request_id,
            "target_id": self.context.target_id,
            "operation": self.context.operation,
            "evidence_request_id": "system-001",
            "execution_allowed": False,
        }

        backend = PostFailureBackend()

        with (
            patch.object(
                coordinator,
                "_assess_health",
                return_value=valid_pre,
            ),
            patch.object(
                coordinator,
                "accept_pre_operation_system_evidence",
                return_value=accepted,
            ),
        ):
            report = coordinator.run_read_only_lifecycle(
                self.context,
                backend,
            )

        self.assertEqual(backend.calls, 4)
        self.assertEqual(report.outcome, "VERIFICATION_FAILED")
        self.assertEqual(report.post_operation.status, "UNKNOWN")
        self.assertIs(report.execution_allowed, False)

if __name__ == "__main__":
    unittest.main()
