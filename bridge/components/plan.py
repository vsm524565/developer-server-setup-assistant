"""Validate multi-component plan documents (Milestone 4.31).

The Bash backend builds plans; Python checks that a plan is internally
consistent before displaying it or binding an approval to it (4.32).
Python never re-decides a plan.
"""

import hashlib
import os
import re
from pathlib import Path

from bridge.components.loader import DEFAULT_DEFINITIONS_DIR


PLAN_KEYS = frozenset({
    "schema_version", "plan_id", "created_at", "target", "allow_draft", "package_manager",
    "selection", "definitions", "steps", "runnable", "held",
    "planning_only", "execution_allowed", "operation", "all_or_nothing", "templates",
})
STEP_KEYS = frozenset({
    "component", "variant_id", "support", "decision", "action", "reasons",
    "package_manager", "planning_only", "execution_allowed",
    "added_as_dependency", "depends_on", "blocked_by",
})
# 4.42: a REMOVE step carries what it removes (details).
OPTIONAL_STEP_KEYS = frozenset({"details"})
HELD_KEYS = frozenset({"component", "action", "reasons", "blocked_by"})

ACTIONS = frozenset({"INSTALL", "SKIP", "REVIEW", "BLOCKED", "REMOVE", "ADOPT", "UPDATE"})
RUNNABLE_ACTIONS = frozenset({"INSTALL", "SKIP", "REMOVE", "ADOPT", "UPDATE"})
# Which actions each operation may plan (4.42).
OPERATION_ACTIONS = {
    "install": frozenset({"INSTALL", "SKIP", "REVIEW", "BLOCKED"}),
    "remove": frozenset({"REMOVE", "BLOCKED"}),
    "adopt": frozenset({"ADOPT", "BLOCKED"}),
    "update": frozenset({"UPDATE", "SKIP", "BLOCKED"}),
}
PANEL_DETAILS_KEYS = frozenset({"panel", "reversible"})
# What a REMOVE (4.42) or UPDATE (4.43) step carries.
DETAILS_KEYS = {
    "REMOVE": frozenset({
        "remove_packages", "kept_dependencies", "stop_units", "disable_units",
        "repository_files", "kept_paths", "reversible", "unavailable", "record_entry",
        "remove_files", "kept_files",
    }),
    "UPDATE": frozenset({"updates", "units", "running_units", "reversible", "unavailable"}),
}

PLAN_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HOST_KEY_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_.-]+)*$")


class PlanError(ValueError):
    """A plan document is malformed or internally inconsistent."""


def _require(condition, message):
    if not condition:
        raise PlanError(message)


def _exact_keys(value, keys, where):
    _require(isinstance(value, dict), f"{where}: must be an object")
    _require(set(value) == set(keys), f"{where}: unexpected or missing fields")


def _strings(value, where, pattern=None):
    _require(isinstance(value, list), f"{where}: must be a list")
    for item in value:
        _require(isinstance(item, str), f"{where}: items must be strings")
        if pattern is not None:
            _require(pattern.fullmatch(item), f"{where}: invalid value {item!r}")
    return value


def validate_plan_document(plan):
    """Raise PlanError unless the plan is well formed and consistent."""

    _exact_keys(plan, PLAN_KEYS, "plan")
    _require(plan["schema_version"] == "1.0", "plan: unsupported schema version")
    _require(plan["planning_only"] is True, "plan: must be planning-only")
    _require(plan["execution_allowed"] is False, "plan: must not allow execution")
    _require(
        isinstance(plan["plan_id"], str) and PLAN_ID_RE.fullmatch(plan["plan_id"]),
        "plan: invalid plan_id",
    )
    _require(
        isinstance(plan["created_at"], str) and TIME_RE.fullmatch(plan["created_at"]),
        "plan: invalid created_at",
    )
    _require(plan["package_manager"] in ("apt", "dnf"), "plan: invalid package manager")
    _require(isinstance(plan["allow_draft"], bool), "plan: allow_draft must be true or false")
    _require(isinstance(plan["all_or_nothing"], bool), "plan: all_or_nothing must be true or false")
    operation = plan["operation"]
    _require(operation in OPERATION_ACTIONS, "plan: invalid operation")
    _require(operation == "install" or not plan["allow_draft"], "plan: drafts apply to installs only")

    target = plan["target"]
    _exact_keys(target, ("target_id", "host_key_sha256"), "plan.target")
    _require(
        isinstance(target["target_id"], str) and TARGET_RE.fullmatch(target["target_id"]),
        "plan.target: invalid target_id",
    )
    _require(
        isinstance(target["host_key_sha256"], str)
        and HOST_KEY_RE.fullmatch(target["host_key_sha256"]),
        "plan.target: invalid host key fingerprint",
    )

    selection = _strings(plan["selection"], "plan.selection", COMPONENT_RE)
    _require(selection, "plan.selection: must not be empty")
    _require(len(set(selection)) == len(selection), "plan.selection: duplicates")

    steps = plan["steps"]
    _require(isinstance(steps, list) and steps, "plan.steps: must not be empty")

    seen = []
    actions = {}
    for index, step in enumerate(steps):
        where = f"plan.steps[{index}]"
        _require(isinstance(step, dict), f"{where}: must be an object")
        _require(STEP_KEYS <= set(step) <= STEP_KEYS | OPTIONAL_STEP_KEYS,
                 f"{where}: unexpected or missing fields")
        component = step["component"]
        _require(
            isinstance(component, str) and COMPONENT_RE.fullmatch(component),
            f"{where}: invalid component",
        )
        _require(component not in seen, f"{where}: duplicate component {component}")
        _require(step["action"] in OPERATION_ACTIONS[operation], f"{where}: invalid action for {operation}")
        _require(step["decision"] in ACTIONS, f"{where}: invalid decision")
        if step["action"] in DETAILS_KEYS:
            details = step.get("details")
            _require(isinstance(details, dict) and set(details) == DETAILS_KEYS[step["action"]],
                     f"{where}: {step['action']} needs its details")
            _require(isinstance(details["reversible"], bool), f"{where}: invalid reversible flag")
        elif "details" in step:
            # Panels (4.48): an install step may say which panel it is.
            details = step["details"]
            _require(isinstance(details, dict) and set(details) == PANEL_DETAILS_KEYS
                     and isinstance(details["reversible"], bool)
                     and isinstance(details["panel"], str) and COMPONENT_RE.fullmatch(details["panel"]),
                     f"{where}: unexpected details")
        _require(step["planning_only"] is True, f"{where}: must be planning-only")
        _require(step["execution_allowed"] is False, f"{where}: must not allow execution")
        _require(isinstance(step["added_as_dependency"], bool), f"{where}: invalid flag")
        _require(
            step["added_as_dependency"] == (component not in selection),
            f"{where}: added_as_dependency disagrees with the selection",
        )
        _strings(step["reasons"], f"{where}.reasons")
        _require(step["reasons"], f"{where}: reasons must not be empty")
        depends_on = _strings(step["depends_on"], f"{where}.depends_on", COMPONENT_RE)
        blocked_by = _strings(step["blocked_by"], f"{where}.blocked_by", COMPONENT_RE)

        # Dependencies present in the plan must come first; a removal
        # goes the other way: dependents first (4.42).
        for dependency in depends_on:
            if dependency in {s["component"] for s in steps}:
                if operation == "remove":
                    _require(
                        dependency not in seen,
                        f"{where}: dependency {dependency} is removed before {component}",
                    )
                else:
                    _require(
                        dependency in seen,
                        f"{where}: dependency {dependency} is ordered after {component}",
                    )

        # A blocker must be an earlier, held dependency.
        for blocker in blocked_by:
            _require(blocker in depends_on, f"{where}: blocker is not a dependency")
            _require(
                actions.get(blocker) not in RUNNABLE_ACTIONS,
                f"{where}: blocker {blocker} is runnable",
            )
        if blocked_by:
            _require(step["action"] == "BLOCKED", f"{where}: blocked step must be BLOCKED")

        # A draft variant is planned as INSTALL only when the plan allows
        # drafts (4.34: lab targets, confirmed by the operator).
        if step["support"] == "draft" and not plan["allow_draft"]:
            _require(step["action"] != "INSTALL", f"{where}: draft variant planned as INSTALL")

        seen.append(component)
        actions[component] = step["action"]

    _require(set(selection) <= set(seen), "plan: selected component missing from steps")

    expected_runnable = [s["component"] for s in steps if s["action"] in RUNNABLE_ACTIONS]
    _require(plan["runnable"] == expected_runnable, "plan.runnable: inconsistent with steps")

    held = plan["held"]
    _require(isinstance(held, list), "plan.held: must be a list")
    expected_held = [s for s in steps if s["action"] not in RUNNABLE_ACTIONS]
    _require(len(held) == len(expected_held), "plan.held: inconsistent with steps")
    for entry, step in zip(held, expected_held):
        _exact_keys(entry, HELD_KEYS, "plan.held[]")
        _require(
            all(entry[key] == step[key] for key in HELD_KEYS),
            "plan.held: inconsistent with steps",
        )

    templates = plan["templates"]
    _require(isinstance(templates, dict), "plan.templates: must be an object")
    for template, digest in templates.items():
        _require(isinstance(template, str) and TEMPLATE_ID_RE.fullmatch(template),
                 f"plan.templates: invalid template id {template!r}")
        _require(isinstance(digest, str) and DIGEST_RE.fullmatch(digest),
                 f"plan.templates.{template}: invalid digest")

    definitions = plan["definitions"]
    _require(isinstance(definitions, dict), "plan.definitions: must be an object")
    _require(set(definitions) == set(seen), "plan.definitions: must match plan steps")
    for component, digest in definitions.items():
        _require(
            isinstance(digest, str) and DIGEST_RE.fullmatch(digest),
            f"plan.definitions.{component}: invalid digest",
        )

    return plan


def definition_digest(path):
    """SHA-256 of a definition file's exact text, as the backend computes it."""

    return "sha256:" + hashlib.sha256(Path(path).read_bytes().rstrip(b"\n")).hexdigest()


def changed_definitions(plan, directory=None):
    """Return components whose local definition differs from the plan's."""

    directory = Path(directory or DEFAULT_DEFINITIONS_DIR)
    changed = []
    for component, digest in sorted(plan["definitions"].items()):
        path = directory / f"{component}.json"
        if not path.is_file() or definition_digest(path) != digest:
            changed.append(component)
    # 4.45: templates are bound the same way (DSSA_TEMPLATES_DIR, or the
    # templates folder next to the definitions folder).
    templates_dir = Path(os.environ.get("DSSA_TEMPLATES_DIR") or directory.parent / "templates")
    for template, digest in sorted(plan.get("templates", {}).items()):
        path = templates_dir / template
        if not path.is_file() or definition_digest(path) != digest:
            changed.append(f"template {template}")
    return changed
