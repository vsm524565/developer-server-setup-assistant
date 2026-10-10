
"""
Installation plan eligibility and explicit approval controls.

Stage 3.3 validates proposed installation plans and collects
operator approval without executing remote commands.
"""


class PlanApproval:
    """Validate installation plans and request operator approval."""

    BLOCKING_ACTIONS = {
        "BLOCK",
        "MANUAL_REVIEW",
        "BLOCKED_DEPENDENCY",
    }

    def __init__(self, installation_plan):
        self.installation_plan = installation_plan

    def validate(self):
        """
        Evaluate whether the plan is eligible for approval.

        Returns eligibility, whether changes are proposed,
        and any blocking issues.
        """
        issues = []

        for step in self.installation_plan:
            if step["action"] in self.BLOCKING_ACTIONS:
                issues.append(
                    f"{step['component']} requires attention: "
                    f"{step['action']}"
                )

        has_changes = any(
            step["action"] == "INSTALL"
            for step in self.installation_plan
        )

        return {
            "eligible": not issues and bool(self.installation_plan),
            "has_changes": has_changes,
            "issues": issues,
        }

    def request_approval(self):
        """
        Request explicit approval for an eligible installation plan.

        Returns True only when the operator enters APPROVE.
        """
        validation = self.validate()

        # Plans requiring no installation do not need approval.
        if validation["eligible"] and not validation["has_changes"]:
            print("\n=== VERIFICATION-ONLY PLAN ===")
            print("All selected components are already present.")
            print("No installation approval is required.")
            return False

        # Block plans containing unresolved conflicts or dependencies.
        if not validation["eligible"]:
            print("\n=== PLAN NOT ELIGIBLE ===")

            for issue in validation["issues"]:
                print(f"- {issue}")

            if not self.installation_plan:
                print("- No installation actions selected.")

            return False

        print("\n=== INSTALLATION APPROVAL ===")
        print("Proposed actions:")

        for step in self.installation_plan:
            print(
                f"  {step['component']}: {step['action']}"
            )

        print("\nNo commands will be executed in Stage 3.3.")

        confirmation = input(
            "Type APPROVE to confirm this plan: "
        ).strip()

        if confirmation != "APPROVE":
            print("Approval declined. No changes made.")
            return False

        print(
            "Plan approved for demonstration only. "
            "Execution remains disabled."
        )

        return True
