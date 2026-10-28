import copy
import unittest
from datetime import datetime, timedelta, timezone

from bridge.evidence.collection import EvidenceCollector
from bridge.evidence.freshness import evaluate_freshness
from bridge.evidence.gate import evaluate_evidence_gate


class EvidenceTests(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)

        self.evidence = {
            "schema_version": "1.0",
            "request_id": "test-request-001",
            "operation": "discovery.swap_filesystem",
            "target_id": "ubuntu24",
            "parameters": {"target_path": "/swap.img"},
            "collection": {
                "started_at_utc": (
                    self.now - timedelta(seconds=2)
                ).isoformat(),
                "completed_at_utc": (
                    self.now - timedelta(seconds=1)
                ).isoformat(),
                "duration_ms": 1000,
                "clock_source": "controller",
            },
            "data": {
                "target_path": "/swap.img",
                "filesystem": {
                    "mountpoint": "/",
                    "source": "/dev/sda2",
                    "type": "ext4",
                    "available_bytes": 20830932992,
                },
            },
            "execution_allowed": False,
        }

    def freshness(self, evidence=None):
        return evaluate_freshness(
            self.evidence if evidence is None else evidence,
            now_utc=self.now,
        )

    def gate(self, evidence=None):
        return evaluate_evidence_gate(
            self.evidence if evidence is None else evidence,
            expected_operation="discovery.swap_filesystem",
            expected_target_id="ubuntu24",
            expected_target_path="/swap.img",
        )

    def test_fresh_evidence(self):
        result = self.freshness()
        self.assertEqual(result["status"], "FRESH")
        self.assertFalse(result["execution_allowed"])

    def test_stale_evidence(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["collection"]["started_at_utc"] = (
            self.now - timedelta(seconds=62)
        ).isoformat()
        evidence["collection"]["completed_at_utc"] = (
            self.now - timedelta(seconds=61)
        ).isoformat()

        result = self.freshness(evidence)
        self.assertEqual(result["status"], "STALE")

    def test_future_timestamp_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["collection"]["completed_at_utc"] = (
            self.now + timedelta(seconds=10)
        ).isoformat()

        result = self.freshness(evidence)
        self.assertEqual(result["status"], "INVALID")

    def test_inconsistent_duration_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["collection"]["duration_ms"] = 10000

        result = self.freshness(evidence)
        self.assertEqual(result["status"], "INVALID")

    def test_missing_request_identity_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        del evidence["request_id"]

        result = self.freshness(evidence)
        self.assertEqual(result["status"], "INVALID")

    def test_operation_mismatch_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["operation"] = "discovery.system"

        result = self.gate(evidence)
        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(result["planning_allowed"])

    def test_target_mismatch_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["target_id"] = "ubuntu22"

        result = self.gate(evidence)
        self.assertEqual(result["status"], "REJECTED")

    def test_target_path_mismatch_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["data"]["target_path"] = "/different.img"

        result = self.gate(evidence)
        self.assertEqual(result["status"], "REJECTED")

    def test_execution_authorization_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["execution_allowed"] = True

        result = self.freshness(evidence)
        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["execution_allowed"])

    def test_collector_output(self):
        collector = EvidenceCollector()

        result = collector.complete(
            request_id="collector-test",
            operation="discovery.system",
            target_id="ubuntu24",
            data={"hostname": "test-vm"},
        )

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["collection"]["clock_source"], "controller")
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
