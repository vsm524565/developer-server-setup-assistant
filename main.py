"""Developer Server Setup Assistant: plan -> approve -> apply (Milestone 4.34).

Run with no arguments for a guided session, or use one step at a time:

    python main.py                        guided: target, components, plan, approve, apply
    python main.py targets                list inventory targets
    python main.py plan TARGET COMPONENT... [--allow-draft]
    python main.py plan --remove TARGET COMPONENT...   uninstall what DSSA installed
    python main.py plan --adopt TARGET COMPONENT...    let DSSA manage an existing install
    python main.py plan --update TARGET COMPONENT...   update installed components
    (any plan) --all-or-nothing                        a failure undoes the whole run
    python main.py approve PLAN_ID
    python main.py apply PLAN_ID
    python main.py report PLAN_ID
    python main.py resume-rollback PLAN_ID
    python main.py installed TARGET       what DSSA installed there (no sudo)

Add --json (before the command) for machine-readable output: JSON Lines
on stdout, ending with one result document; text for a person goes to
stderr (4.40, docs/design/json-output.md).

Python never opens SSH and never sees a password: the Bash backend owns
SSH, and sudo asks for the password on the server's own terminal.
"""

import argparse
import contextlib
import hashlib
import json
import os
import sys

from bridge.components import run
from bridge.components.approval import (
    CONFIRMATION,
    ApprovalError,
    approve_plan,
    consume_approval,
    default_state_dir,
    known_host_fingerprints,
    plan_digest,
    preflight_plan,
)
from bridge.components.loader import load_component_definitions
from bridge.components.output import LogTee, Output
from bridge.components.risk import risky_steps


# Same override the Bash backend honours (tests, lab checks).
DEFINITIONS_DIR = os.environ.get("DSSA_DEFINITIONS_DIR") or None

# Result and events of the current command (JSON mode, 4.40).
OUT = Output()

EXIT_MEANING = {
    0: "every runnable component installed or verified",
    2: "refused before any change",
    3: "some components failed and were rolled back, or were skipped",
    4: "rollback incomplete or engine error: the server needs review",
    5: "the plan could not be sent to the server; nothing was run there",
    255: "the connection failed; remote completion is not confirmed (use report)",
}


# --- display ------------------------------------------------------------------

def show_plan(plan, plan_bytes):
    print("\n=== PLAN ===")
    print(f"Plan ID   : {plan['plan_id']}")
    print(f"Target    : {plan['target']['target_id']}  ({plan['package_manager']})")
    print(f"Host key  : {plan['target']['host_key_sha256']}")
    print(f"Created   : {plan['created_at']}  (approve within 30 minutes)")
    print(f"Plan hash : {plan_digest(plan_bytes)[:19]}…")
    if plan.get("operation", "install") != "install":
        print(f"Operation : {plan['operation'].upper()}")
    if plan.get("all_or_nothing"):
        print("Mode      : ALL OR NOTHING (any failure undoes the whole run)")
    if plan["allow_draft"]:
        print("Drafts    : ALLOWED for this lab target")
    print()
    for step in plan["steps"]:
        flag = "  (added as dependency)" if step["added_as_dependency"] else ""
        print(f"  {step['component']:<12} {step['action']:<8} variant {step['variant_id'] or '-'}{flag}")
        for reason in step["reasons"]:
            print(f"      - {reason}")
    print(f"\nRunnable  : {', '.join(plan['runnable']) or 'none'}")
    if plan["held"]:
        print(f"Held      : {', '.join(h['component'] for h in plan['held'])}")


def show_outcome(outcome, kind="apply", approved=False):
    print("\n=== RESULT ===")
    summary = outcome.summary
    code = outcome.exit_code if outcome.exit_code is not None else outcome.rc
    again = ("The approval is used up: plan and approve again." if approved
             else "Plan again, then apply.")
    if summary is None:
        print("No result came back from the server.")
        if code == 5:
            print(f"Nothing was run on the server. {again}")
        elif code == 2:
            # Refused on the server before any change (4.39.1), for
            # example a missing command or an unsafe state folder.
            print("The server refused the run before any change; the reason is shown above.")
            print(f"Nothing was run there. Fix the cause. {again}")
        elif outcome.status == "interrupted":
            print("The run stopped before finishing. Next: python main.py report PLAN_ID,")
            print("then python main.py resume-rollback PLAN_ID to undo it.")
        elif outcome.status != "running":
            print("If the connection dropped, the run may still be going on the server.")
            print("Check it with: python main.py report PLAN_ID")
    else:
        status = summary.get("status") or ("resumed" if summary.get("resumed") else "unknown")
        print(f"Status: {status}")
        for result in summary.get("results", []):
            line = f"  {result.get('component', '?'):<12} {result.get('outcome', '?')}"
            detail = result.get("failure") or result.get("reason") or result.get("note")
            if detail:
                line += f"  - {detail}"
            print(line)
            for action in (result.get("rollback") or {}).get("actions", []):
                print(f"      undo {action.get('action')} {action.get('target')}: {action.get('result')}")
            for issue in (result.get("rollback") or {}).get("issues", []):
                print(f"      ISSUE: {issue}")
            for upgraded in result.get("upgraded_not_reverted") or []:
                print(f"      upgraded, not reverted: {upgraded}")
    if outcome.status:
        print(f"Run state : {outcome.status}")
    if outcome.directory:
        print(f"Saved copy: {outcome.directory}")
    if kind == "report":
        meaning = "result fetched" if code == 0 else "could not fetch the result"
    elif kind == "resume" and code == 0:
        meaning = "interrupted components rolled back"
    else:
        meaning = EXIT_MEANING.get(code, "see the run log")
    print(f"Exit code : {code} ({meaning})")


def ask(prompt):
    """A person's answer. In JSON mode it is read from the terminal itself,
    never from stdin, so --json cannot be used to approve from a script."""

    if not OUT.json:
        return input(prompt)
    try:
        tty = _open_tty()
    except OSError:
        raise run.RunError("This step needs a person at a terminal (--json reads the answer from the terminal).")
    with tty:
        tty.write(prompt)
        tty.flush()
        return tty.readline().rstrip("\n")


def _open_tty():
    return open("/dev/tty", "r+")


def outcome_data(outcome):
    return {"run_status": outcome.status, "summary": outcome.summary,
            "files": outcome.files,
            "saved_copy": str(outcome.directory) if outcome.directory else None}


def target_host(target, inventory):
    for entry in run.list_targets(inventory):
        if entry["target_id"] == target:
            return entry
    raise run.RunError(f"Unknown target: {target}")


# --- steps --------------------------------------------------------------------

def step_plan(target, components, allow_draft, inventory, operation="install", all_or_nothing=False):
    plan_bytes, plan = run.make_plan(target, components, allow_draft, inventory, operation,
                                     all_or_nothing)

    # Draft variants run only after the operator types each one's name.
    drafts = [s["component"] for s in plan["steps"]
              if s["support"] == "draft" and s["action"] == "INSTALL"]
    show_plan(plan, plan_bytes)
    for component in drafts:
        typed = ask(f'\n{component} is a draft variant. Type "{component}" to allow it on {target}: ').strip()
        if typed != component:
            print("Not confirmed. The plan was discarded.")
            OUT.fail(f"Draft {component} not confirmed; the plan was discarded.")
            return None
    path = run.save_plan(plan_bytes)
    print(f"\nPlan saved: {path}")
    OUT.set(plan=plan, plan_path=str(path), plan_sha256=plan_digest(plan_bytes))
    OUT.event("plan.saved", plan_id=plan["plan_id"], target=target)
    return plan["plan_id"]


def step_approve(plan_id, inventory):
    plan_bytes, plan, _ = run.load_plan(plan_id)
    host = target_host(plan["target"]["target_id"], inventory)["host"]
    keys = known_host_fingerprints(host)
    check = preflight_plan(plan_bytes, keys, definitions_dir=DEFINITIONS_DIR)
    show_plan(plan, plan_bytes)
    OUT.set(plan_id=plan_id, verification_only=bool(check["verification_only"]), approval=None)
    if not check["passed"]:
        print("\nPreflight failed:")
        for issue in check["issues"]:
            print(f"  - {issue}")
        OUT.fail("Preflight failed.", check["issues"])
        return False
    if check["verification_only"]:
        print("\nVerification-only plan: no approval is needed. Run apply.")
        return True
    print(f"\nApproving runs exactly: {', '.join(plan['runnable'])}")
    # Risky actions need the component's name typed too (decision 4, 4.42).
    for name, what, warnings in risky_steps(plan):
        for warning in warnings:
            print(f"\n{name}: {warning}")
        typed_name = ask(f'\nType "{name}" to {what} on {plan["target"]["target_id"]}: ').strip()
        if typed_name != name:
            print("Not confirmed. Nothing was approved.")
            OUT.fail(f"{name} not confirmed; nothing was approved.")
            return False
    typed = ask(f"Type {CONFIRMATION} to approve: ").strip()
    try:
        record = approve_plan(plan_bytes, keys, typed, definitions_dir=DEFINITIONS_DIR)
    except ApprovalError as error:
        print(f"\n{error}")
        for issue in error.issues:
            if issue != str(error):
                print(f"  - {issue}")
        OUT.fail(error, error.issues)
        return False
    print(f"\nApproved by {record['approved_by']}, valid until {record['expires_at']} (single use).")
    OUT.set(approval=record)
    return True


def step_apply(plan_id, inventory):
    plan_bytes, plan, plan_path = run.load_plan(plan_id)
    target = plan["target"]["target_id"]
    keys = known_host_fingerprints(target_host(target, inventory)["host"])
    check = preflight_plan(plan_bytes, keys, definitions_dir=DEFINITIONS_DIR)
    OUT.set(plan_id=plan_id, target=target, verification_only=bool(check["verification_only"]),
            approval_used=False)

    if check["passed"] and check["verification_only"]:
        print("Verification-only plan: no approval needed.")
    elif not check["requires_approval"]:
        # Nothing to install, so no approval could exist: show the real
        # reason (e.g. a stale plan) instead of "No approval exists".
        print("Apply refused: the plan failed its checks.")
        for issue in check["issues"]:
            print(f"  - {issue}")
        OUT.fail("Apply refused: the plan failed its checks.", check["issues"])
        return 2
    else:
        # Log in first (4.38): a refused key or an unreachable server must
        # not use up the single-use approval.
        OUT.event("login.check", target=target)
        login = run.check_login(target, plan["target"]["host_key_sha256"], inventory)
        if login != 0:
            print("Apply refused: cannot log in to the server. The approval was not used.")
            OUT.fail("Apply refused: cannot log in to the server. The approval was not used.")
            return 2
        try:
            consume_approval(plan_id, keys, definitions_dir=DEFINITIONS_DIR)
        except ApprovalError as error:
            print(f"Apply refused: {error}")
            for issue in error.issues:
                if issue != str(error):
                    print(f"  - {issue}")
            OUT.fail(f"Apply refused: {error}", error.issues)
            return 2
        OUT.set(approval_used=True)
        OUT.event("approval.consumed", plan_id=plan_id)
        plan_path = default_state_dir() / "approvals" / plan_id / "plan.json"
        plan_bytes = plan_path.read_bytes()

    digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    OUT.event("run.started", plan_id=plan_id, target=target)
    outcome = run_streamed(run.apply_plan, target, plan_path, digest, plan_id, inventory)
    show_outcome(outcome, approved=not check["verification_only"])
    code = outcome.exit_code if outcome.exit_code is not None else outcome.rc
    OUT.set(**outcome_data(outcome))
    if code != 0 and outcome.summary is None:
        OUT.fail(f"No result came back from the server: {EXIT_MEANING.get(code, 'see the run log')}.")
    OUT.event("run.finished", plan_id=plan_id, exit_code=code)
    return code


def run_streamed(call, *args):
    """Run a terminal-using backend call; in JSON mode its stream goes to
    stderr and each line also becomes a log event."""

    if not OUT.json:
        return call(*args)
    tee = LogTee(OUT)
    try:
        return call(*args, out=tee)
    finally:
        tee.close()


def step_installed(target, inventory):
    record = run.read_record(target, inventory)
    OUT.set(target=target, record=record)
    components = record["components"]
    print(f"\n=== INSTALLED BY DSSA on {target} ===")
    if not components:
        print("Nothing recorded yet.")
        return 0
    for component_id in sorted(components):
        entry = components[component_id]
        packages = ", ".join(f"{p['package']} {p['version']}" for p in entry.get("packages", []))
        print(f"  {component_id:<14} {entry.get('how', '?'):<9} {entry.get('recorded_at', '?')}  plan {entry.get('plan_id', '?')[:8]}")
        if packages:
            print(f"      packages: {packages}")
        deps = entry.get("dependencies", [])
        if deps:
            print(f"      + {len(deps)} dependencies")
        units = sorted(set(entry.get("enabled_units", []) + entry.get("started_units", [])))
        if units:
            print(f"      units: {', '.join(units)}")
    return 0


def step_remote(kind, plan_id, inventory):
    _, plan, _ = run.load_plan(plan_id)
    target = plan["target"]["target_id"]
    key = plan["target"]["host_key_sha256"]
    call = run.report if kind == "report" else run.resume_rollback
    OUT.set(plan_id=plan_id, target=target)
    outcome = run_streamed(call, target, plan_id, key, inventory)
    show_outcome(outcome, kind)
    OUT.set(**outcome_data(outcome))
    return outcome.exit_code if outcome.exit_code is not None else outcome.rc


# --- guided session -----------------------------------------------------------

def choose(prompt, options):
    for number, label in enumerate(options, 1):
        print(f"  {number}. {label}")
    answer = ask(prompt).strip()
    picked = []
    for word in answer.replace(",", " ").split():
        if not word.isdigit() or not 1 <= int(word) <= len(options):
            raise ValueError(f"Not an option: {word}")
        if int(word) - 1 not in picked:
            picked.append(int(word) - 1)
    return picked


def guided(allow_draft, inventory):
    targets = run.list_targets(inventory)
    if not targets:
        print("No enabled targets in the inventory.")
        return 2
    print("\nTargets:")
    picked = choose("Choose one target: ",
                    [f"{t['target_id']}  ({t['host']}{', lab' if t['lab'] else ''})" for t in targets])
    if len(picked) != 1:
        print("Choose exactly one target.")
        return 2
    target = targets[picked[0]]["target_id"]

    catalog = load_component_definitions(DEFINITIONS_DIR)
    ids = sorted(catalog)
    print("\nComponents:")
    labels = []
    for component_id in ids:
        definition = catalog[component_id]
        support = {v.support for v in definition.variants}
        status = "supported" if "supported" in support else "draft"
        labels.append(f"{component_id:<10} {definition.display_name}  [{status}]")
    picked = choose("Choose components (numbers, in order): ", labels)
    if not picked:
        print("No components selected.")
        return 0

    plan_id = step_plan(target, [ids[i] for i in picked], allow_draft, inventory)
    if plan_id is None:
        return 2
    if not step_approve(plan_id, inventory):
        return 2
    answer = ask("\nStart apply now? [y/N] ").strip().lower()
    if answer != "y":
        print(f"Not applied. Later: python main.py apply {plan_id}")
        return 0
    return step_apply(plan_id, inventory)


def main(argv=None):
    parser = argparse.ArgumentParser(description="DSSA: plan -> approve -> apply")
    parser.add_argument("--inventory", help="inventory file (default: config/inventory/servers.json)")
    parser.add_argument("--allow-draft", action="store_true",
                        help="lab targets only: allow draft variants after typed confirmation")
    parser.add_argument("--json", action="store_true",
                        help="JSON Lines on stdout (events, then one result); text goes to stderr")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("targets")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("target")
    plan_parser.add_argument("components", nargs="+")
    kind = plan_parser.add_mutually_exclusive_group()
    kind.add_argument("--remove", action="store_true", help="uninstall components DSSA installed (4.42)")
    kind.add_argument("--adopt", action="store_true", help="record existing installs as DSSA-managed (4.42)")
    kind.add_argument("--update", action="store_true", help="update installed components to exact versions (4.43)")
    plan_parser.add_argument("--all-or-nothing", action="store_true",
                             help="any failure undoes every change of the run (4.44)")
    for name in ("approve", "apply", "report", "resume-rollback"):
        sub.add_parser(name).add_argument("plan_id")
    sub.add_parser("installed").add_argument("target")
    args = parser.parse_args(argv)

    global OUT
    OUT = Output(json_mode=args.json, stream=sys.stdout)
    try:
        return run_command(args)
    finally:
        OUT = Output()  # the next in-process call starts in text mode


def run_command(args):
    if not args.json:
        return dispatch(args)
    command = args.command or "guided"
    if args.command is None:
        OUT.fail("--json needs a command; the guided session is for a person at a terminal.")
        OUT.result(command, 2)
        return 2
    with contextlib.redirect_stdout(sys.stderr):
        code = dispatch(args)
    if code != 0 and OUT.error is None and command in ("plan", "approve"):
        OUT.fail(f"{command} did not complete.")
    OUT.result(command, code)
    return code


def dispatch(args):
    try:
        if args.command is None:
            return guided(args.allow_draft, args.inventory)
        if args.command == "targets":
            targets = run.list_targets(args.inventory)
            OUT.set(targets=targets)
            if not OUT.json:
                print(json.dumps(targets, indent=2))
            return 0
        if args.command == "plan":
            operation = ("remove" if args.remove else "adopt" if args.adopt
                         else "update" if args.update else "install")
            if operation != "install" and args.allow_draft:
                raise ValueError("--allow-draft applies to installs only.")
            return 0 if step_plan(args.target, args.components, args.allow_draft, args.inventory,
                                  operation, args.all_or_nothing) else 2
        if args.command == "approve":
            return 0 if step_approve(args.plan_id, args.inventory) else 2
        if args.command == "apply":
            return step_apply(args.plan_id, args.inventory)
        if args.command == "installed":
            return step_installed(args.target, args.inventory)
        return step_remote("report" if args.command == "report" else "resume",
                           args.plan_id, args.inventory)
    except (run.RunError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        OUT.fail(error)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. If apply had started, check with: python main.py report PLAN_ID",
              file=sys.stderr)
        OUT.fail("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
