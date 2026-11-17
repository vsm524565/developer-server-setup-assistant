"""Developer Server Setup Assistant: plan -> approve -> apply (Milestone 4.34).

Run with no arguments for a guided session, or use one step at a time:

    python main.py                        guided: target, components, plan, approve, apply
    python main.py targets                list inventory targets
    python main.py plan TARGET COMPONENT... [--allow-draft]
    python main.py approve PLAN_ID
    python main.py apply PLAN_ID
    python main.py report PLAN_ID
    python main.py resume-rollback PLAN_ID

Python never opens SSH and never sees a password: the Bash backend owns
SSH, and sudo asks for the password on the server's own terminal.
"""

import argparse
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


# Same override the Bash backend honours (tests, lab checks).
DEFINITIONS_DIR = os.environ.get("DSSA_DEFINITIONS_DIR") or None

EXIT_MEANING = {
    0: "every runnable component installed or verified",
    2: "refused before any change",
    3: "some components failed and were rolled back, or were skipped",
    4: "rollback incomplete or engine error: the server needs review",
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


def show_outcome(outcome, kind="apply"):
    print("\n=== RESULT ===")
    summary = outcome.summary
    if summary is None:
        print("No result came back from the server.")
        if outcome.status == "interrupted":
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
            detail = result.get("failure") or result.get("reason")
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
    code = outcome.exit_code if outcome.exit_code is not None else outcome.rc
    if kind == "report":
        meaning = "result fetched" if code == 0 else "could not fetch the result"
    elif kind == "resume" and code == 0:
        meaning = "interrupted components rolled back"
    else:
        meaning = EXIT_MEANING.get(code, "see the run log")
    print(f"Exit code : {code} ({meaning})")


def target_host(target, inventory):
    for entry in run.list_targets(inventory):
        if entry["target_id"] == target:
            return entry
    raise run.RunError(f"Unknown target: {target}")


# --- steps --------------------------------------------------------------------

def step_plan(target, components, allow_draft, inventory):
    plan_bytes, plan = run.make_plan(target, components, allow_draft, inventory)

    # Draft variants run only after the operator types each one's name.
    drafts = [s["component"] for s in plan["steps"]
              if s["support"] == "draft" and s["action"] == "INSTALL"]
    show_plan(plan, plan_bytes)
    for component in drafts:
        typed = input(f'\n{component} is a draft variant. Type "{component}" to allow it on {target}: ').strip()
        if typed != component:
            print("Not confirmed. The plan was discarded.")
            return None
    path = run.save_plan(plan_bytes)
    print(f"\nPlan saved: {path}")
    return plan["plan_id"]


def step_approve(plan_id, inventory):
    plan_bytes, plan, _ = run.load_plan(plan_id)
    host = target_host(plan["target"]["target_id"], inventory)["host"]
    keys = known_host_fingerprints(host)
    check = preflight_plan(plan_bytes, keys, definitions_dir=DEFINITIONS_DIR)
    show_plan(plan, plan_bytes)
    if not check["passed"]:
        print("\nPreflight failed:")
        for issue in check["issues"]:
            print(f"  - {issue}")
        return False
    if check["verification_only"]:
        print("\nVerification-only plan: no approval is needed. Run apply.")
        return True
    print(f"\nApproving runs exactly: {', '.join(plan['runnable'])}")
    typed = input(f"Type {CONFIRMATION} to approve: ").strip()
    try:
        record = approve_plan(plan_bytes, keys, typed, definitions_dir=DEFINITIONS_DIR)
    except ApprovalError as error:
        print(f"\n{error}")
        for issue in error.issues:
            if issue != str(error):
                print(f"  - {issue}")
        return False
    print(f"\nApproved by {record['approved_by']}, valid until {record['expires_at']} (single use).")
    return True


def step_apply(plan_id, inventory):
    plan_bytes, plan, plan_path = run.load_plan(plan_id)
    target = plan["target"]["target_id"]
    keys = known_host_fingerprints(target_host(target, inventory)["host"])
    check = preflight_plan(plan_bytes, keys, definitions_dir=DEFINITIONS_DIR)

    if check["passed"] and check["verification_only"]:
        print("Verification-only plan: no approval needed.")
    else:
        try:
            consume_approval(plan_id, keys, definitions_dir=DEFINITIONS_DIR)
        except ApprovalError as error:
            print(f"Apply refused: {error}")
            for issue in error.issues:
                if issue != str(error):
                    print(f"  - {issue}")
            return 2
        plan_path = default_state_dir() / "approvals" / plan_id / "plan.json"
        plan_bytes = plan_path.read_bytes()

    digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    outcome = run.apply_plan(target, plan_path, digest, plan_id, inventory)
    show_outcome(outcome)
    return outcome.exit_code if outcome.exit_code is not None else outcome.rc


def step_remote(kind, plan_id, inventory):
    _, plan, _ = run.load_plan(plan_id)
    target = plan["target"]["target_id"]
    key = plan["target"]["host_key_sha256"]
    call = run.report if kind == "report" else run.resume_rollback
    outcome = call(target, plan_id, key, inventory)
    show_outcome(outcome, kind)
    return outcome.exit_code if outcome.exit_code is not None else outcome.rc


# --- guided session -----------------------------------------------------------

def choose(prompt, options):
    for number, label in enumerate(options, 1):
        print(f"  {number}. {label}")
    answer = input(prompt).strip()
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
    answer = input("\nStart apply now? [y/N] ").strip().lower()
    if answer != "y":
        print(f"Not applied. Later: python main.py apply {plan_id}")
        return 0
    return step_apply(plan_id, inventory)


def main(argv=None):
    parser = argparse.ArgumentParser(description="DSSA: plan -> approve -> apply")
    parser.add_argument("--inventory", help="inventory file (default: config/inventory/servers.json)")
    parser.add_argument("--allow-draft", action="store_true",
                        help="lab targets only: allow draft variants after typed confirmation")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("targets")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("target")
    plan_parser.add_argument("components", nargs="+")
    for name in ("approve", "apply", "report", "resume-rollback"):
        sub.add_parser(name).add_argument("plan_id")
    args = parser.parse_args(argv)

    try:
        if args.command is None:
            return guided(args.allow_draft, args.inventory)
        if args.command == "targets":
            print(json.dumps(run.list_targets(args.inventory), indent=2))
            return 0
        if args.command == "plan":
            return 0 if step_plan(args.target, args.components, args.allow_draft, args.inventory) else 2
        if args.command == "approve":
            return 0 if step_approve(args.plan_id, args.inventory) else 2
        if args.command == "apply":
            return step_apply(args.plan_id, args.inventory)
        return step_remote("report" if args.command == "report" else "resume",
                           args.plan_id, args.inventory)
    except (run.RunError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. If apply had started, check with: python main.py report PLAN_ID",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
