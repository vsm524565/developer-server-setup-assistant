
from copy import deepcopy

from planning.approval_binding import ApprovalBinding


target = {
    "hostname": "192.168.56.109",
    "port": 22,
    "username": "vivek",
}

host_key = "SHA256:example-test-fingerprint"

snapshot = {
    "hostname": "ubuntutest",
    "os_id": "ubuntu",
    "os_version": "24.04",
    "architecture": "x86_64",
    "panel": {
        "name": "None",
        "status": "NOT_INSTALLED",
    },
    "components": {
        "Docker": {
            "installed": False,
            "service_active": None,
        },
    },
    "ports": {
        "80": "FREE",
    },
}

plan = [
    {
        "component": "Docker",
        "action": "INSTALL",
        "status": "READY",
        "dependencies": [],
        "reasons": ["No conflicts detected."],
    },
]

original = ApprovalBinding.create(
    target, host_key, snapshot, plan
)

print("\nTEST 1: DETERMINISTIC FINGERPRINT")

assert original == ApprovalBinding.create(
    target, host_key, snapshot, plan
)

print("PASSED")


print("\nTEST 2: SAME CONTEXT MATCHES")

assert ApprovalBinding.matches(
    original, target, host_key, snapshot, plan
)

print("PASSED")


print("\nTEST 3: DIFFERENT SERVER REJECTED")

changed_target = deepcopy(target)
changed_target["hostname"] = "192.168.56.103"

assert not ApprovalBinding.matches(
    original, changed_target, host_key, snapshot, plan
)

print("PASSED")


print("\nTEST 4: DIFFERENT SSH HOST KEY REJECTED")

assert not ApprovalBinding.matches(
    original,
    target,
    "SHA256:different-test-fingerprint",
    snapshot,
    plan,
)

print("PASSED")


print("\nTEST 5: CHANGED INSTALLATION ACTION REJECTED")

changed_plan = deepcopy(plan)
changed_plan[0]["action"] = "VERIFY_EXISTING"

assert not ApprovalBinding.matches(
    original, target, host_key, snapshot, changed_plan
)

print("PASSED")


print("\nTEST 6: CHANGED SERVER STATE REJECTED")

changed_snapshot = deepcopy(snapshot)
changed_snapshot["ports"]["80"] = "LISTENING"

assert not ApprovalBinding.matches(
    original, target, host_key, changed_snapshot, plan
)

print("PASSED")


print("\nTEST 7: CHANGED DEPENDENCY REJECTED")

changed_plan = deepcopy(plan)
changed_plan[0]["dependencies"] = ["Python"]

assert not ApprovalBinding.matches(
    original, target, host_key, snapshot, changed_plan
)

print("PASSED")


print("\nTEST 8: CHANGED PLAN ORDER REJECTED")

two_step_plan = deepcopy(plan)
two_step_plan.append({
    "component": "Git",
    "action": "VERIFY_EXISTING",
    "status": "ALREADY_PRESENT",
    "dependencies": [],
    "reasons": ["Already installed."],
})

ordered = ApprovalBinding.create(
    target, host_key, snapshot, two_step_plan
)

reversed_plan = list(reversed(two_step_plan))

assert not ApprovalBinding.matches(
    ordered, target, host_key, snapshot, reversed_plan
)

print("PASSED")


print("\nAll eight approval binding tests passed.")
