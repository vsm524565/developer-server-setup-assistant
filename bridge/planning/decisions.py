class ConfigurationPlanner:
    """
    Evaluate requested server components against discovered server state.

    Produces read-only configuration decisions without executing changes.
    """

    COMPONENT_PORTS = {
        "Nginx": (80, 443),
        "Apache": (80, 443),
        "MySQL": (3306,),
        "MariaDB": (3306,),
        "PostgreSQL": (5432,),
        "Redis": (6379,),
    }

    ALTERNATIVES = {
        "Nginx": "Apache",
        "Apache": "Nginx",
        "MySQL": "MariaDB",
        "MariaDB": "MySQL",
    }

    PANEL_MANAGED = {
        "Nginx", "Apache", "MySQL", "MariaDB",
        "PostgreSQL", "Redis", "PHP",
    }

    def __init__(self, supported, panel, components, ports):
        self.supported = supported
        self.panel = panel
        self.components = components
        self.ports = ports

    def evaluate(self, requested):
        """Classify each requested component and explain the decision."""
        results = {}
        requested_set = set(requested)

        for name in requested:
            if name not in self.components:
                raise ValueError(f"Unknown component: {name}")

            installed = self.components[name]["installed"]
            reasons = []
            status = "READY"

            if not self.supported:
                status = "BLOCKED"
                reasons.append("Unsupported operating system.")

            elif installed:
                status = "ALREADY_PRESENT"
                reasons.append(
                    "Component detected; verify version and configuration."
                )

            else:
                alternative = self.ALTERNATIVES.get(name)

                if alternative and alternative in requested_set:
                    reasons.append(
                    f"{name} and {alternative} are both requested; "
                    "their default configurations may conflict."
                )

                if (
                    alternative
                    and self.components[alternative]["installed"]
                ):
                    reasons.append(
                        f"Existing alternative detected: {alternative}."
                    )

                for port in self.COMPONENT_PORTS.get(name, ()):
                    port_info = self.ports.get(port)

                    if port_info and port_info["listening"]:
                        owners = sorted({
                            listener["process"]
                            for listener in port_info["listeners"]
                        })
                        reasons.append(
                            f"Port {port} is occupied by "
                            f"{', '.join(owners)}."
                        )

                if (
                    self.panel["status"] != "NOT_INSTALLED"
                    and name in self.PANEL_MANAGED
                ):
                    reasons.append(
                        "Control panel indicators detected; "
                        "panel-managed installation requires review."
                    )

                if reasons:
                    status = "REVIEW"
                else:
                    reasons.append(
                        "No conflicts detected by current discovery checks."
                    )

            results[name] = {
                "status": status,
                "reasons": reasons,
            }

        return results


def display_configuration_plan(plan):
    """Present component decisions and their supporting reasons."""
    print("\n=== CONFIGURATION PLAN ===")

    for name, result in plan.items():
        print(f"\n{name}: {result['status']}")

        for reason in result["reasons"]:
            print(f"  - {reason}")

    print("\nPlanning only. No server changes were made.")