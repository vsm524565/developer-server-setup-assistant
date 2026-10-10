class InstallationPlanner:
    """
    Build a read-only installation plan from dependency-ordered
    components and configuration evaluation results.
    """

    def __init__(self, components, decisions):
        self.components = components
        self.decisions = decisions

    def build(self, ordered_components):
        """
        Generate dependency-aware installation actions.

        Components depending on blocked or unresolved prerequisites
        are marked BLOCKED_DEPENDENCY.
        """
        from planning.dependencies import DependencyResolver

        plan = []
        actions = {}

        for name in ordered_components:
            decision = self.decisions[name]
            status = decision["status"]

            dependencies = DependencyResolver.DEPENDENCIES.get(name, ())

            blocked_dependencies = [
                dependency
                for dependency in dependencies
                if actions.get(dependency) in (
                    "BLOCK",
                    "MANUAL_REVIEW",
                    "BLOCKED_DEPENDENCY",
                )
            ]

            if status == "BLOCKED":
                action = "BLOCK"
            elif blocked_dependencies:
                action = "BLOCKED_DEPENDENCY"
            elif status == "REVIEW":
                action = "MANUAL_REVIEW"
            elif status == "ALREADY_PRESENT":
                action = "VERIFY_EXISTING"
            elif status == "READY":
                action = "INSTALL"
            else:
                raise ValueError(
                    f"Unknown configuration status: {status}"
                )

            reasons = list(decision["reasons"])

            if blocked_dependencies:
                reasons.append(
                    "Unresolved dependencies: "
                    + ", ".join(blocked_dependencies)
                )

            actions[name] = action

            plan.append({
                "component": name,
                "action": action,
                "status": status,
                "dependencies": list(dependencies),
                "reasons": reasons,
            })

        return plan


def display_installation_plan(plan):
    """Display the proposed component actions in dependency order."""
    print("\n=== INSTALLATION PLAN (DRY RUN) ===")

    for index, step in enumerate(plan, start=1):
        print(
            f"{index}. {step['component']} "
            f"-> {step['action']}"
        )

        for reason in step["reasons"]:
            print(f"   - {reason}")

    print("\nDry run only. No server changes were made.")