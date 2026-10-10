
from copy import deepcopy

from discovery.components import ComponentDiscovery
from discovery.ports import PortDiscovery
from planning.snapshot import ServerSnapshotBuilder, SnapshotError
from planning.state_consistency import ServerStateComparator


system_info = {
    "hostname": "ubuntutest",
    "os_id": "ubuntu",
    "os_version": "24.04",
    "architecture": "x86_64",
}

panel_info = {
    "panel": "None",
    "status": "NOT_INSTALLED",
    "installation_path": None,
}

components = {
    name: {
        "installed": name in ("Python", "Git"),
        "service_active": None,
    }
    for name in ComponentDiscovery.COMPONENTS
}

ports = {
    port: {
        "service": service,
        "listening": False,
        "listeners": [],
    }
    for port, service in PortDiscovery.PORTS.items()
}


print("\nTEST 1: VALID SNAPSHOT")

snapshot = ServerSnapshotBuilder.build(
    system_info, panel_info, components, ports
)

assert snapshot["hostname"] == "ubuntutest"
assert snapshot["components"]["Python"]["installed"] is True
assert snapshot["ports"]["80"] == "FREE"

print("PASSED: Snapshot normalized successfully.")


print("\nTEST 2: UNCHANGED SNAPSHOT")

result = ServerStateComparator(snapshot).compare(
    deepcopy(snapshot)
)

assert result["consistent"] is True
print("PASSED: Identical snapshots are consistent.")


print("\nTEST 3: COMPONENT STATE CHANGED")

changed = deepcopy(snapshot)
changed["components"]["Docker"]["installed"] = True

result = ServerStateComparator(snapshot).compare(changed)

assert result["consistent"] is False
print("PASSED: Component change detected.")


print("\nTEST 4: PORT STATE CHANGED")

changed = deepcopy(snapshot)
changed["ports"]["80"] = "LISTENING"

result = ServerStateComparator(snapshot).compare(changed)

assert result["consistent"] is False
print("PASSED: Port change detected.")


print("\nTEST 5: MISSING COMPONENT")

incomplete = deepcopy(components)
del incomplete["Docker"]

try:
    ServerSnapshotBuilder.build(
        system_info, panel_info, incomplete, ports
    )
except SnapshotError:
    print("PASSED: Missing component rejected.")
else:
    raise AssertionError("Missing component was accepted.")


print("\nTEST 6: INVALID PORT STATE")

invalid_ports = deepcopy(ports)
invalid_ports[80]["listening"] = None

try:
    ServerSnapshotBuilder.build(
        system_info, panel_info, components, invalid_ports
    )
except SnapshotError:
    print("PASSED: Invalid port state rejected.")
else:
    raise AssertionError("Invalid port state was accepted.")


print("\nAll six snapshot adapter tests passed.")

