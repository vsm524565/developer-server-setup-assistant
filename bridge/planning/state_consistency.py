
"""
Compare server discovery snapshots for execution-relevant changes.

This module performs no remote commands and does not authorize execution.
"""

from copy import deepcopy


class ServerStateComparator:
    """Compare two normalized server discovery snapshots."""

    REQUIRED_FIELDS = (
        "hostname",
        "os_id",
        "os_version",
        "architecture",
        "panel",
        "components",
        "ports",
    )

    def __init__(self, original_state):
        self.original_state = deepcopy(original_state)

    def compare(self, current_state):
        changes = []
        issues = []

        if not isinstance(current_state, dict):
            return {
                "consistent": False,
                "changes": [],
                "issues": ["Current server state is invalid."],
            }

        for field in self.REQUIRED_FIELDS:
            if (
                field not in self.original_state
                or field not in current_state
                or self.original_state[field] is None
                or current_state[field] is None
            ):
                issues.append(
                    f"Missing or invalid required state: {field}"
                )
                continue

            if self.original_state[field] != current_state[field]:
                changes.append({
                    "field": field,
                    "before": self.original_state[field],
                    "after": current_state[field],
                })
        for field in ("components", "ports"):
            original_value = self.original_state.get(field)
            current_value = current_state.get(field)

            if not isinstance(original_value, dict) or not original_value:
                issues.append(
                    f"Original {field} discovery is empty or invalid."
                )

            if not isinstance(current_value, dict) or not current_value:
                issues.append(
                    f"Current {field} discovery is empty or invalid."
                )

            if (
                isinstance(original_value, dict)
                and isinstance(current_value, dict)
                and original_value
                and current_value
            ):
                for key in set(original_value) | set(current_value):
                    if (
                        key not in original_value
                        or key not in current_value
                        or original_value[key] is None
                        or current_value[key] is None
                    ):
                        issues.append(
                            f"Incomplete {field} discovery for: {key}"
                        )

        return {
            "consistent": not changes and not issues,
            "changes": changes,
            "issues": issues,
        }


def display_consistency_report(result):
    print("\n=== SERVER STATE CONSISTENCY REPORT ===")
    print(f"Consistent : {result['consistent']}")

    for change in result["changes"]:
        print(f"Changed    : {change['field']}")
        print(f"  Before   : {change['before']}")
        print(f"  After    : {change['after']}")

    for issue in result["issues"]:
        print(f"Issue      : {issue}")

    if result["consistent"]:
        print("No changes detected in the compared snapshot.")
    else:
        print("State comparison failed. Replanning is required.")

    print("Read-only comparison. Execution remains disabled.")
