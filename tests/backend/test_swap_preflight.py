import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/linux/planning/swap_preflight.sh"


class SwapPreflightTests(unittest.TestCase):

    def setUp(self):
        self.plan = {
            "schema_version": "1.0",
            "component": "swap",
            "decision": "BLOCKED",
            "action": "NONE",
            "approval_required": False,
            "current_total_bytes": 0,
            "desired_total_bytes": 1073741824,
            "accounting_consistent": True,
            "persistence_matches": True,
        }

        self.operation = {
            "schema_version": "1.0",
            "component": "swap",
            "operation": "CREATE",
            "current_total_bytes": 0,
            "desired_total_bytes": 1073741824,
            "execution_allowed": False,
        }

        self.readiness = {
            "schema_version": "1.0",
            "component": "swap",
            "operation": "CREATE",
            "status": "UNKNOWN",
            "execution_allowed": False,
        }

        self.filesystem = {
            "schema_version": "1.0",
            "target_path": "/swap.img",
            "filesystem": {
                "mountpoint": "/",
                "source": "/dev/mapper/ubuntu--vg-ubuntu--lv",
                "type": "ext4",
                "available_bytes": 16440213504,
            },
        }

    def run_preflight(self, *args):
        command = [
            "bash",
            "-c",
            'source "$1"; shift; evaluate_swap_preflight "$@"',
            "bash",
            str(SCRIPT),
            *args,
        ]

        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

    def evidence(self):
        return [
            json.dumps(self.plan),
            json.dumps(self.operation),
            json.dumps(self.readiness),
            json.dumps(self.filesystem),
            "/swap.img",
        ]
    def test_consistent_evidence_reaches_safety_gate(self):
        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)

        data = json.loads(result.stdout)

        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["component"], "swap")
        self.assertEqual(data["check"], "execution_preflight")
        self.assertEqual(data["operation"], "CREATE")
        self.assertEqual(data["target_path"], "/swap.img")

        self.assertEqual(data["status"], "UNKNOWN")
        self.assertEqual(data["capacity"]["status"], "PASS")

        self.assertFalse(data["execution_allowed"])
        self.assertFalse(data["capacity"]["execution_allowed"])

        self.assertIn(
            "FILESYSTEM_COMPATIBILITY",
            data["outstanding_checks"],
        )
        self.assertIn(
            "ROLLBACK_READINESS",
            data["outstanding_checks"],
        )

    def test_target_mismatch_rejected(self):
        evidence = self.evidence()
        evidence[4] = "/different.img"

        result = self.run_preflight(*evidence)

        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "Invalid or inconsistent",
            result.stderr,
        )

    def test_malformed_json_rejected(self):
        evidence = self.evidence()
        evidence[1] = '{"invalid":'

        result = self.run_preflight(*evidence)

        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "Invalid or inconsistent",
            result.stderr,
        )

    def test_missing_arguments_rejected(self):
        result = self.run_preflight()

        self.assertEqual(result.returncode, 2)

    def test_relative_target_rejected(self):
        evidence = self.evidence()
        evidence[4] = "swap.img"

        result = self.run_preflight(*evidence)

        self.assertEqual(result.returncode, 2)

    def test_execution_authorization_rejected(self):
        self.operation["execution_allowed"] = True

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "Invalid or inconsistent",
            result.stderr,
        )
    def test_create_with_existing_swap_rejected(self):
        self.operation["current_total_bytes"] = 536870912
        self.plan["current_total_bytes"] = 536870912

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_invalid_operation_rejected(self):
        self.operation["operation"] = "OVERWRITE"
        self.readiness["operation"] = "OVERWRITE"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_unsupported_readiness_status_rejected(self):
        self.readiness["status"] = "READY"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_fractional_capacity_rejected(self):
        self.operation["desired_total_bytes"] = 1073741824.5
        self.plan["desired_total_bytes"] = 1073741824.5

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_fractional_filesystem_capacity_rejected(self):
        self.filesystem["filesystem"]["available_bytes"] = 1024.5

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_negative_filesystem_capacity_rejected(self):
        self.filesystem["filesystem"]["available_bytes"] = -1

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_empty_filesystem_type_rejected(self):
        self.filesystem["filesystem"]["type"] = ""

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)

    def test_missing_filesystem_source_rejected(self):
        del self.filesystem["filesystem"]["source"]

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 3)
        self.assertIn("Invalid or inconsistent", result.stderr)
    def run_capacity(self, available, requested):
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; evaluate_swap_capacity "$2" "$3"',
                "bash",
                str(SCRIPT),
                str(available),
                str(requested),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    def test_capacity_sufficient(self):
        result = self.run_capacity(17179869184, 2147483648)

        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "PASS")
        self.assertFalse(data["execution_allowed"])

    def test_capacity_below_minimum_reserve(self):
        result = self.run_capacity(4294967296, 3758096384)

        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_capacity_below_percentage_reserve(self):
        result = self.run_capacity(21474836480, 20401094656)

        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "BLOCKED")

    def test_capacity_exact_minimum_reserve(self):
        result = self.run_capacity(4294967296, 3221225472)

        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["remaining_bytes"], 1073741824)
        self.assertEqual(data["status"], "PASS")

    def test_capacity_invalid_negative_input(self):
        result = self.run_capacity(-1, 1073741824)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid capacity inputs", result.stderr)
    def test_create_insufficient_capacity_blocked(self):
        self.filesystem["filesystem"]["available_bytes"] = 1073741824

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertEqual(data["capacity"]["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_no_change_not_required(self):
        self.plan["decision"] = "NO_CHANGE"
        self.plan["current_total_bytes"] = 1073741824

        self.operation["operation"] = "NO_CHANGE"
        self.operation["current_total_bytes"] = 1073741824

        self.readiness["operation"] = "NO_CHANGE"
        self.readiness["status"] = "NOT_REQUIRED"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "NOT_REQUIRED")
        self.assertIsNone(data["capacity"])
        self.assertFalse(data["execution_allowed"])

    def test_increase_blocked(self):
        self.plan["current_total_bytes"] = 1073741824
        self.plan["desired_total_bytes"] = 2147483648

        self.operation["operation"] = "INCREASE"
        self.operation["current_total_bytes"] = 1073741824
        self.operation["desired_total_bytes"] = 2147483648

        self.readiness["operation"] = "INCREASE"
        self.readiness["status"] = "BLOCKED"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertIsNone(data["capacity"])
        self.assertFalse(data["execution_allowed"])

    def test_reduce_blocked(self):
        self.plan["current_total_bytes"] = 2147483648
        self.plan["desired_total_bytes"] = 1073741824

        self.operation["operation"] = "REDUCE"
        self.operation["current_total_bytes"] = 2147483648
        self.operation["desired_total_bytes"] = 1073741824

        self.readiness["operation"] = "REDUCE"
        self.readiness["status"] = "BLOCKED"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertIsNone(data["capacity"])
        self.assertFalse(data["execution_allowed"])

    def test_remove_blocked(self):
        self.plan["current_total_bytes"] = 1073741824
        self.plan["desired_total_bytes"] = 0

        self.operation["operation"] = "REMOVE"
        self.operation["current_total_bytes"] = 1073741824
        self.operation["desired_total_bytes"] = 0

        self.readiness["operation"] = "REMOVE"
        self.readiness["status"] = "BLOCKED"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertIsNone(data["capacity"])
        self.assertFalse(data["execution_allowed"])

    def run_filesystem_policy(self, filesystem_type):
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; evaluate_swap_filesystem_policy "$2"',
                "bash",
                str(SCRIPT),
                filesystem_type,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_filesystem_ext4_candidate(self):
        result = self.run_filesystem_policy("ext4")

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "CANDIDATE")
        self.assertEqual(data["filesystem_type"], "ext4")
        self.assertFalse(data["execution_allowed"])

    def test_filesystem_btrfs_blocked(self):
        result = self.run_filesystem_policy("btrfs")

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_filesystem_xfs_blocked(self):
        result = self.run_filesystem_policy("xfs")

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_filesystem_empty_type_rejected(self):
        result = self.run_filesystem_policy("")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Empty filesystem type", result.stderr)

    def test_create_ext4_candidate_remains_unknown(self):
        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "UNKNOWN")
        self.assertEqual(data["capacity"]["status"], "PASS")
        self.assertEqual(
            data["filesystem_policy"]["status"], "CANDIDATE"
        )
        self.assertFalse(data["execution_allowed"])

    def test_create_btrfs_blocked_by_filesystem(self):
        self.filesystem["filesystem"]["type"] = "btrfs"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["capacity"]["status"], "PASS")
        self.assertEqual(
            data["filesystem_policy"]["status"], "BLOCKED"
        )
        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_create_xfs_blocked_by_filesystem(self):
        self.filesystem["filesystem"]["type"] = "xfs"

        result = self.run_preflight(*self.evidence())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["capacity"]["status"], "PASS")
        self.assertEqual(
            data["filesystem_policy"]["status"], "BLOCKED"
        )
        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])
    def run_target_policy(self, evidence, target="/swap.img"):
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; evaluate_swap_target_policy "$2" "$3"',
                "bash",
                str(SCRIPT),
                json.dumps(evidence),
                target,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_target_policy_absent_candidate(self):
        evidence = {
            "schema_version": "1.0",
            "component": "swap",
            "check": "target_identity",
            "target_path": "/swap.img",
            "target_type": "ABSENT",
            "exists": False,
            "is_symlink": False,
            "execution_allowed": False,
        }

        result = self.run_target_policy(evidence)

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "CANDIDATE")
        self.assertFalse(data["execution_allowed"])

    def test_target_policy_existing_file_blocked(self):
        evidence = {
            "schema_version": "1.0",
            "component": "swap",
            "check": "target_identity",
            "target_path": "/swap.img",
            "target_type": "REGULAR_FILE",
            "exists": True,
            "is_symlink": False,
            "execution_allowed": False,
        }

        result = self.run_target_policy(evidence)

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["status"], "BLOCKED")
        self.assertFalse(data["execution_allowed"])

    def test_target_policy_contradictory_evidence_rejected(self):
        evidence = {
            "schema_version": "1.0",
            "component": "swap",
            "check": "target_identity",
            "target_path": "/swap.img",
            "target_type": "ABSENT",
            "exists": True,
            "is_symlink": False,
            "execution_allowed": False,
        }

        result = self.run_target_policy(evidence)

        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "Invalid swap target identity evidence",
            result.stderr,
        )
if __name__ == "__main__":
    unittest.main()
