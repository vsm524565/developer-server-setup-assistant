
"""Regression tests for read-only application readiness decisions."""

from copy import deepcopy
import unittest

from bridge.applications.readiness.evaluator import (
    evaluate_application_readiness,
)


def assessments(
    *,
    application="git",
    state_status="MISSING",
    inventory_status="ABSENT",
    plan_status="REVIEW_REQUIRED",
    recipe_id="draft-recipe",
    dependencies=None,
    reasons=None,
):
    """Create synthetic assessments for decision-policy testing."""

    common = {
        "target_id": "ubuntu24",
        "application_id": application,
        "planning_only": True,
        "execution_allowed": False,
    }

    state = {
        **common,
        "state": state_status,
    }

    inventory = {
        **common,
        "inventory_status": inventory_status,
    }

    plan = {
        **common,
        "status": plan_status,
        "recipe_id": recipe_id,
        "dependencies": (
            [] if dependencies is None else dependencies
        ),
        "reasons": (
            ["RECIPE_POLICY_DRAFT"]
            if reasons is None else reasons
        ),
    }

    return state, inventory, plan


def decide(state, inventory, plan):
    """Evaluate a synthetic application readiness request."""

    return evaluate_application_readiness(
        target_id="ubuntu24",
        application_id=state["application_id"],
        state=state,
        inventory=inventory,
        draft_plan=plan,
    )


class TestApplicationReadiness(unittest.TestCase):

    def test_installed_application_is_ready(self):
        values = assessments(
            state_status="INSTALLED_HEALTHY",
            inventory_status="IDENTIFIED",
        )

        result = decide(*values)

        self.assertEqual(result["status"], "READY_INSTALLED")
        self.assertFalse(result["execution_allowed"])

    def test_missing_application_with_draft_recipe(self):
        result = decide(*assessments())

        self.assertEqual(
            result["status"],
            "INSTALL_CANDIDATE_DRAFT",
        )
        self.assertTrue(result["planning_only"])
        self.assertFalse(result["execution_allowed"])

    def test_missing_application_with_blocked_plan(self):
        result = decide(*assessments(
            plan_status="BLOCKED",
            recipe_id=None,
        ))

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_recipe_requires_review(self):
        result = decide(*assessments(recipe_id=None))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_state_inventory_contradiction_requires_review(self):
        result = decide(*assessments(
            state_status="MISSING",
            inventory_status="IDENTIFIED",
        ))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertIn(
            "STATE_INVENTORY_RECONCILIATION_REQUIRED",
            result["reasons"],
        )

    def test_unhealthy_application_requires_review(self):
        result = decide(*assessments(
            state_status="UNHEALTHY",
            inventory_status="IDENTIFIED",
        ))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_unknown_inventory_requires_review(self):
        result = decide(*assessments(
            state_status="UNKNOWN",
            inventory_status="UNKNOWN",
        ))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_dependency_plan_requires_review(self):
        dependencies = [{
            "application_id": "docker",
            "observed_state": "MISSING",
            "status": "DEPENDENCY_PLAN_REQUIRED",
            "selected_recipe_id": "docker-draft",
        }]

        result = decide(*assessments(
            application="docker_compose",
            dependencies=dependencies,
        ))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_blocked_dependency_blocks_candidate(self):
        dependencies = [{
            "application_id": "docker",
            "observed_state": "MISSING",
            "status": "BLOCKED",
            "selected_recipe_id": None,
        }]

        result = decide(*assessments(
            application="docker_compose",
            dependencies=dependencies,
        ))

        self.assertEqual(result["status"], "BLOCKED")

    def test_plan_reason_prevents_draft_candidate(self):
        result = decide(*assessments(
            reasons=[
                "RECIPE_POLICY_DRAFT",
                "NGINX_REQUIRED_PORT_OCCUPIED",
            ],
        ))

        self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_mismatched_target_is_rejected(self):
        state, inventory, plan = assessments()
        inventory["target_id"] = "alma10"

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)

    def test_mismatched_application_is_rejected(self):
        state, inventory, plan = assessments()
        plan["application_id"] = "nginx"

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)

    def test_execution_permission_is_rejected(self):
        state, inventory, plan = assessments()
        state["execution_allowed"] = True

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)

    def test_invalid_state_is_rejected(self):
        state, inventory, plan = assessments()
        state["state"] = "UNKNOWN_STATE"

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)

    def test_inputs_remain_unchanged(self):
        values = assessments()
        original = deepcopy(values)

        decide(*values)

        self.assertEqual(values, original)

    def test_installed_application_rejects_invalid_plan_reasons(self):
        state, inventory, plan = assessments(
            state_status="INSTALLED_HEALTHY",
            inventory_status="IDENTIFIED",
        )

        plan["reasons"] = "not-a-list"

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)

    def test_installed_application_rejects_invalid_dependencies(self):
        state, inventory, plan = assessments(
            state_status="INSTALLED_HEALTHY",
            inventory_status="IDENTIFIED",
        )

        plan["dependencies"] = [
            {"status": "INVALID_DEPENDENCY_STATUS"}
        ]

        with self.assertRaises(ValueError):
            decide(state, inventory, plan)


if __name__ == "__main__":
    unittest.main()
