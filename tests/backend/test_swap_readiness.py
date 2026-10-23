import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/linux/planning/swap_readiness.sh"


def run_bash(command):
    """Execute readiness functions in an isolated Bash process."""
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"\n{command}'],
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )


class SwapReadinessTests(unittest.TestCase):

    def test_operation_classification(self):
        scenarios = [
            (0, 0, "NO_CHANGE"),
            (0, 2147483648, "CREATE"),
            (2147483648, 4294967296, "INCREASE"),
            (4294967296, 2147483648, "REDUCE"),
            (2147483648, 0, "REMOVE"),
        ]

        for current, desired, expected in scenarios:
            with self.subTest(expected=expected):
                result = run_bash(
                    f"classify_swap_operation {current} {desired}"
                )

                self.assertEqual(result.returncode, 0, result.stderr)

                payload = json.loads(result.stdout)
                self.assertEqual(payload["operation"], expected)
                self.assertFalse(payload["execution_allowed"])

    def test_invalid_capacity_rejected(self):
        for current, desired in [
            (-1, 100),
            (100, -1),
            ("abc", 100),
            (100, "abc"),
            (100, 1000000000000000),
        ]:
            with self.subTest(current=current, desired=desired):
                result = run_bash(
                    f"classify_swap_operation {current} {desired}"
                )

                self.assertEqual(result.returncode, 2)

    def test_no_change_readiness(self):
        result = run_bash("""
            plan='{
                "schema_version": "1.0",
                "component": "swap",
                "decision": "NO_CHANGE",
                "action": "NONE",
                "approval_required": false,
                "accounting_consistent": true,
                "persistence_matches": true,
                "current_total_bytes": 2147483648,
                "desired_total_bytes": 2147483648
            }'

            operation="$(classify_swap_operation 2147483648 2147483648)"
            evaluate_swap_readiness "$plan" "$operation"
        """)

        self.assertEqual(result.returncode, 0, result.stderr)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "NOT_REQUIRED")
        self.assertFalse(payload["execution_allowed"])

    def test_increase_remains_unknown(self):
        result = run_bash("""
            plan='{
                "schema_version": "1.0",
                "component": "swap",
                "decision": "BLOCKED",
                "action": "NONE",
                "approval_required": false,
                "accounting_consistent": true,
                "persistence_matches": true,
                "current_total_bytes": 2147483648,
                "desired_total_bytes": 4294967296
            }'

            operation="$(classify_swap_operation 2147483648 4294967296)"
            evaluate_swap_readiness "$plan" "$operation"
        """)

        self.assertEqual(result.returncode, 0, result.stderr)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertFalse(payload["execution_allowed"])

    def test_remove_blocked(self):
        result = run_bash("""
            plan='{
                "schema_version": "1.0",
                "component": "swap",
                "decision": "BLOCKED",
                "action": "NONE",
                "approval_required": false,
                "accounting_consistent": true,
                "persistence_matches": true,
                "current_total_bytes": 2147483648,
                "desired_total_bytes": 0
            }'

            operation="$(classify_swap_operation 2147483648 0)"
            evaluate_swap_readiness "$plan" "$operation"
        """)

        self.assertEqual(result.returncode, 0, result.stderr)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertFalse(payload["execution_allowed"])

    def test_tampered_operation_rejected(self):
        result = run_bash("""
            plan='{
                "schema_version": "1.0",
                "component": "swap",
                "decision": "NO_CHANGE",
                "action": "NONE",
                "approval_required": false,
                "accounting_consistent": true,
                "persistence_matches": true,
                "current_total_bytes": 2147483648,
                "desired_total_bytes": 2147483648
            }'

            operation="$(classify_swap_operation 2147483648 2147483648)"
            operation="$(jq '.operation = "INCREASE"' <<< "$operation")"

            evaluate_swap_readiness "$plan" "$operation"
        """)

        self.assertEqual(result.returncode, 3)

    def test_tampered_capacity_rejected(self):
        result = run_bash("""
            plan='{
                "schema_version": "1.0",
                "component": "swap",
                "decision": "NO_CHANGE",
                "action": "NONE",
                "approval_required": false,
                "accounting_consistent": true,
                "persistence_matches": true,
                "current_total_bytes": 2147483648,
                "desired_total_bytes": 2147483648
            }'

            operation="$(classify_swap_operation 2147483648 2147483648)"
            operation="$(jq '.desired_total_bytes += 1073741824' \
                <<< "$operation")"

            evaluate_swap_readiness "$plan" "$operation"
        """)

        self.assertEqual(result.returncode, 3)

    def test_empty_evidence_rejected(self):
        result = run_bash(
            'evaluate_swap_readiness "" ""'
        )
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()
