
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT / "backend/linux/planning/swap.sh"


class SwapPlanningTests(unittest.TestCase):

    def setUp(self):
        self.facts = {
            "swap_total_bytes": 2147483648,
            "swap_used_bytes": 0,
            "swap_devices": [{
                "name": "/swap.img",
                "type": "file",
                "size_bytes": 2147483648,
                "used_bytes": 0,
                "priority": -2
            }],
            "swap_persistence": {
                "readable": True,
                "entries": [{
                    "source": "/swap.img",
                    "mountpoint": "none",
                    "filesystem_type": "swap",
                    "options": "sw"
                }]
            }
        }

    def run_planner(self, facts, desired):
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; plan_swap "$2" "$3"',
             "bash", str(PLANNER), json.dumps(facts), str(desired)],
            text=True,
            capture_output=True,
            timeout=10
        )
        return result

    def test_matching_configuration_no_change(self):
        result = self.run_planner(self.facts, 2147483648)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "NO_CHANGE")
        self.assertTrue(plan["persistence_matches"])

    def test_mismatched_persistence_blocked(self):
        facts = json.loads(json.dumps(self.facts))
        facts["swap_persistence"]["entries"][0]["source"] = "/other.img"

        result = self.run_planner(facts, 2147483648)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "BLOCKED")
        self.assertFalse(plan["persistence_matches"])

    def test_resize_request_blocked(self):
        result = self.run_planner(self.facts, 4294967296)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "BLOCKED")
        self.assertFalse(plan["approval_required"])

    def test_invalid_size_rejected(self):
        result = self.run_planner(self.facts, "-1")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_missing_discovery_rejected(self):
        result = self.run_planner({}, 2147483648)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")

    def test_consistent_accounting(self):
        result = self.run_planner(self.facts, 2147483648)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertTrue(plan["accounting_consistent"])
        self.assertEqual(plan["device_total_bytes"], 2147483648)
        self.assertEqual(plan["device_used_bytes"], 0)

    def test_inconsistent_accounting_blocked(self):
        facts = json.loads(json.dumps(self.facts))

        # Report 2 GiB of system swap but discover no active devices.
        facts["swap_devices"] = []

        result = self.run_planner(facts, 2147483648)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "BLOCKED")
        self.assertFalse(plan["accounting_consistent"])
    def test_active_device_with_zero_system_swap_blocked(self):
        facts = json.loads(json.dumps(self.facts))

        facts["swap_total_bytes"] = 0
        facts["swap_used_bytes"] = 0

        result = self.run_planner(facts, 0)
        self.assertEqual(result.returncode, 0, result.stderr)

        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "BLOCKED")
        self.assertFalse(plan["accounting_consistent"])
