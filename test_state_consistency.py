
from copy import deepcopy

from planning.state_consistency import (
    ServerStateComparator,
    display_consistency_report,
)


original = {
    "hostname": "ubuntutest",
    "os_id": "ubuntu",
    "os_version": "24.04",
    "architecture": "x86_64",
    "panel": "NOT_INSTALLED",
    "components": {
        "Nginx": False,
        "Docker": False,
    },
    "ports": {
        "80": "FREE",
        "443": "FREE",
    },
}

comparator = ServerStateComparator(original)

print("\nTEST 1: UNCHANGED SERVER")
unchanged = deepcopy(original)
result = comparator.compare(unchanged)
display_consistency_report(result)
assert result["consistent"] is True

print("\nTEST 2: PORT OCCUPIED")
port_changed = deepcopy(original)
port_changed["ports"]["80"] = "OCCUPIED"
result = comparator.compare(port_changed)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 3: COMPONENT INSTALLED")
component_changed = deepcopy(original)
component_changed["components"]["Docker"] = True
result = comparator.compare(component_changed)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 4: OS VERSION CHANGED")
os_changed = deepcopy(original)
os_changed["os_version"] = "26.04"
result = comparator.compare(os_changed)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 5: INCOMPLETE DISCOVERY")
incomplete = deepcopy(original)
del incomplete["ports"]
result = comparator.compare(incomplete)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 6: EMPTY COMPONENT INVENTORY")
empty_components = deepcopy(original)
empty_components["components"] = {}
result = comparator.compare(empty_components)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 7: MISSING COMPONENT ENTRY")
missing_component = deepcopy(original)
del missing_component["components"]["Docker"]
result = comparator.compare(missing_component)
display_consistency_report(result)
assert result["consistent"] is False

print("\nTEST 8: UNKNOWN PORT STATE")
unknown_port = deepcopy(original)
unknown_port["ports"]["80"] = None
result = comparator.compare(unknown_port)
display_consistency_report(result)
assert result["consistent"] is False

print("\nAll eight state consistency tests passed.")
