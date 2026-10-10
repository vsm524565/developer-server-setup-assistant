
from copy import deepcopy

from planning.approval_binding import ApprovalBinding
from planning.approval_session import ApprovalSession


target = {
    "hostname": "192.168.56.109",
    "port": 22,
    "username": "vivek",
}

host_key = (
    "SHA256:hZbh1ffGVeysP7fwLlGxq5iSgpoWD4JHIgGFtlh58+s"
)

snapshot = {
    "hostname": "ubuntutest",
    "os_id": "ubuntu",
    "os_version": "24.04",
    "architecture": "x86_64",
    "panel": {"name": "None", "status": "NOT_INSTALLED"},
    "components": {
        "Docker": {"installed": False, "service_active": None}
    },
    "ports": {"80": "FREE"},
}

plan = [{
    "component": "Docker",
    "action": "INSTALL",
    "status": "READY",
    "dependencies": [],
    "reasons": ["No conflicts detected."],
}]

session = ApprovalSession()

print("\nTEST 1: APPROVAL NOT GRANTED")
assert not session.is_valid(target, host_key, snapshot, plan)
print("PASSED")

fingerprint = ApprovalBinding.create(
    target, host_key, snapshot, plan
)

session.approve(fingerprint)

print("\nTEST 2: APPROVED CONTEXT")
assert session.is_valid(target, host_key, snapshot, plan)
print("PASSED")

print("\nTEST 3: DIFFERENT SERVER")
changed_target = deepcopy(target)
changed_target["hostname"] = "192.168.56.103"
assert not session.is_valid(
    changed_target, host_key, snapshot, plan
)
print("PASSED")

print("\nTEST 4: DIFFERENT HOST KEY")
assert not session.is_valid(
    target, "SHA256:different", snapshot, plan
)
print("PASSED")

print("\nTEST 5: CHANGED INSTALLATION PLAN")
changed_plan = deepcopy(plan)
changed_plan[0]["action"] = "VERIFY_EXISTING"
assert not session.is_valid(
    target, host_key, snapshot, changed_plan
)
print("PASSED")

print("\nTEST 6: CHANGED SERVER SNAPSHOT")
changed_snapshot = deepcopy(snapshot)
changed_snapshot["ports"]["80"] = "LISTENING"
assert not session.is_valid(
    target, host_key, changed_snapshot, plan
)
print("PASSED")

print("\nTEST 7: EXPLICIT INVALIDATION")
session.invalidate()
assert not session.is_valid(target, host_key, snapshot, plan)
print("PASSED")

print("\nAll seven approval session tests passed.")
