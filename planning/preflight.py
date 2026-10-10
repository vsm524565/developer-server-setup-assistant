
"""
Read-only preflight validation for installation plans.

This validator checks whether a plan is structurally eligible
for future execution. It does not authorize or execute changes.
"""


class PreflightValidator:
    """Validate server compatibility and installation plan structure."""

    ALLOWED_ACTIONS = {
        "INSTALL",
        "VERIFY_EXISTING",
    }

    def __init__(self, server_state, installation_plan):
        self.server_state = server_state
        self.installation_plan = installation_plan

    def validate(self):
        """Return a structured preflight validation result."""
        issues = []

        if not self.server_state.get("supported", False):
            issues.append("Target operating system is unsupported.")

        if not self.installation_plan:
            issues.append("Installation plan is empty.")

        seen = set()

        for step in self.installation_plan:
            component = step.get("component")
            action = step.get("action")
            dependencies = step.get("dependencies", [])

            if not component:
                issues.append("Plan contains an unnamed component.")
                continue

            if component in seen:
                issues.append(
                    f"Duplicate component in plan: {component}"
                )

            if action not in self.ALLOWED_ACTIONS:
                issues.append(
                    f"{component} has an unsafe or unresolved "
                    f"action: {action}"
                )

            for dependency in dependencies:
                if dependency not in seen:
                    issues.append(
                        f"{component} has an unresolved or "
                        f"incorrectly ordered dependency: {dependency}"
                    )

            seen.add(component)

        return {
            "passed": not issues,
            "issues": issues,
            "checked_components": len(self.installation_plan),
        }


def display_preflight_report(result):
    """Display the preflight validation outcome."""

    print("\n=== PREFLIGHT SAFETY REPORT ===")
    print(f"Passed             : {result['passed']}")
    print(
        f"Checked components : {result['checked_components']}"
    )

    if result["issues"]:
        for issue in result["issues"]:
            print(f"  - {issue}")
    else:
        print("No structural preflight issues detected.")

    print(
        "\nPreflight is read-only. "
        "Installation execution remains disabled."
    )
