"""DSSA web UI on the master server (Milestone 4.46).

Standard library only. The same steps as the command line, through the
same bridge functions: plan, approve (the UI's own approval screen, with
typed names), apply (a terminal job: sudo asks on the server's own
terminal), report, resume-rollback, the install record, and batches of
servers (decision 7: one approval, applied server by server, stopping at
the first failure).

Security (docs/design/web-ui.md):
- HTTPS with the master's certificate; plain HTTP only on 127.0.0.1 (for
  a reverse proxy on the same machine).
- Login required for everything but /login and /static; sessions in
  HttpOnly, SameSite=Strict cookies; a CSRF token on every POST.
- Content-Security-Policy without inline scripts; no framing.
- Every action is appended to the web UI's audit log with the user name.
"""

import hashlib
import html
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.components import run
from bridge.components.approval import (
    CONFIRMATION, ApprovalError, approve_plan, consume_approval, default_state_dir,
    known_host_fingerprints, plan_digest, preflight_plan,
)
from bridge.components.loader import load_component_definitions
from bridge.components.risk import risky_steps
from bridge.webui import enrol as enrolment
from bridge.webui.auth import AuthError, Sessions, Users
from bridge.webui.jobs import Job, Jobs

COOKIE = "dssa_session"
PLAN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OPERATIONS = ("install", "update", "remove", "adopt")
MAX_SELECTED = 25  # applications in one plan from the catalog (decision A)
STATIC = Path(__file__).with_name("static")
RECORD_TTL = 60  # seconds an install record read is reused for the catalog page
TEMP_WINDOW = 3600  # requirements item 2, decision 2b: temporary access lasts 60 minutes
TEMP_MARGIN = 300   # end temporary access this long before its key expires
CATEGORY_LABELS = {
    "webserver": "Web servers", "database": "Databases", "cache": "Caches",
    "runtime": "Runtimes", "proxy_ssl": "Proxies and TLS", "container": "Containers",
    "control_panel": "Control panels", "system": "System", "tool": "Tools",
}
# What the catalog offers for one application (the planner still decides).
RECORDED_OPERATIONS = ("update", "remove")
UNRECORDED_OPERATIONS = ("install", "adopt")
OPERATION_LABELS = {"install": "Install", "update": "Update", "remove": "Remove", "adopt": "Adopt (let DSSA manage it)"}


def esc(value):
    return html.escape(str(value), quote=True)


class Config:
    """Paths the web UI uses. Everything private lives under ``home``."""

    def __init__(self, home, definitions_dir=None, master_key=None, known_hosts=None):
        self.home = Path(home)
        self.users = self.home / "users.json"
        self.inventory = self.home / "inventory.json"
        self.audit = self.home / "audit.jsonl"
        self.definitions_dir = definitions_dir
        self.master_key = Path(master_key or Path.home() / ".ssh/dssa_master_ed25519")
        self.known_hosts = Path(known_hosts or os.environ.get("DSSA_KNOWN_HOSTS") or Path.home() / ".ssh/known_hosts")


class App:
    def __init__(self, config, jobs=None, clock=time.monotonic, upgrade=True):
        self.config = config
        self.users = Users(config.users)
        self.sessions = Sessions(self.users, clock)
        self.jobs = jobs or Jobs()
        self.pending = {}   # host-key scans awaiting confirmation, by token
        self.batches = {}   # batch id -> {"plans": [...], "user", "jobs": [], "state"}
        self.records = {}   # target -> (read at, record) for the catalog page
        self.temporary = {}  # target -> meta of a temporary server (item 2), from <home>/temporary
        # plan ID -> {"owner", "connection", "target"} for plans made on a
        # temporary server, kept on disk so a reused name never inherits them.
        self.plan_owners = {}
        self.lock = threading.Lock()
        self.load_temporary()
        self.load_plan_owners()
        if upgrade:  # plan-owners --dry-run looks without writing
            self.upgrade_legacy_plans()

    # --- helpers --------------------------------------------------------------

    def audit(self, user, action, **details):
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "user": user, "action": action, **details}, sort_keys=True)
        self.config.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.config.audit, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(line + "\n")

    def inventory(self, target=None):
        temp = self.temporary.get(target)
        if temp:
            return str(self.temp_dir(target) / "inventory.json")
        return str(self.config.inventory)

    def access(self, target):
        """Extra arguments for the run helpers: a temporary server's own known_hosts."""

        if target in self.temporary:
            return {"known_hosts": str(self.temp_dir(target) / "known_hosts")}
        return {}

    # --- temporary servers (requirements item 2) ----------------------------------

    def temp_dir(self, target):
        return self.config.home / "temporary" / target

    def load_temporary(self):
        root = self.config.home / "temporary"
        if not root.is_dir():
            return
        for folder in sorted(root.iterdir()):
            try:
                meta = json.loads((folder / "meta.json").read_text())
            except (OSError, ValueError):
                continue
            if meta.get("target_id") == folder.name:
                self.temporary[folder.name] = meta

    def temporary_of(self, user):
        return sorted((m for m in self.temporary.values() if m["owner"] == user), key=lambda m: m["target_id"])

    def add_temporary(self, user, pending, password, fingerprint):
        name = pending["name"]
        if not TARGET_RE.fullmatch(name or ""):
            raise ValueError("Invalid server name (lower case letters, digits, _ and -).")
        with self.lock:
            taken = name in self.temporary or any(t["target_id"] == name for t in self.targets())
        if taken:
            raise ValueError(f"A server named {name} already exists.")
        meta = enrolment.temporary_access(name, pending["host"], pending["port"], pending["user"], password,
                                          fingerprint, pending["keys"], self.temp_dir(name), user,
                                          pending["lab"], TEMP_WINDOW)
        with self.lock:
            self.temporary[name] = meta
        self.audit(user, "temp_access_created", target=name, host=meta["host"], fingerprint=fingerprint,
                   connection=meta["tag"],
                   expires=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta["expires"])))
        return meta

    def require_owner(self, user, target):
        """A temporary server is its owner's only: planning, approval, apply,
        report and resume all refuse anyone else."""

        meta = self.temporary.get(target)
        if meta is not None and meta["owner"] != user:
            raise ApprovalError(f"{target} is another user's temporary server.")

    def plans_dir(self):
        return self.config.home / "temporary-plans"

    def load_plan_owners(self):
        root = self.plans_dir()
        if not root.is_dir():
            return
        for path in root.glob("*.json"):
            try:
                owner = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if PLAN_ID_RE.fullmatch(path.stem) and isinstance(owner, dict):
                self.plan_owners[path.stem] = owner

    def write_plan_owner(self, plan_id, owner):
        """Persist one ownership record (exclusive create, 0600) and keep it
        in memory. An existing record is never overwritten."""

        root = self.plans_dir()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / f"{plan_id}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(owner, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.plan_owners[plan_id] = owner

    def bind_plan(self, user, plan_id, target, connection=None):
        """Bind a plan made on a temporary server to its owner and to that
        connection's unique tag (Codex review, 2026-11-26)."""

        self.write_plan_owner(plan_id, {"owner": user, "connection": connection or self.temporary[target]["tag"],
                                        "target": target, "source": "bound"})

    def unbind_plan(self, plan_id):
        self.plan_owners.pop(plan_id, None)
        (self.plans_dir() / f"{plan_id}.json").unlink(missing_ok=True)

    # --- plans made before ownership records (Codex follow-up, 2026-11-26) -------

    def legacy_plan_owners(self):
        """{plan_id: record} for stored plans that have no ownership record,
        decided from this web UI's audit log. Nothing is written here.

        - Inside exactly one temporary-access interval of its server name, and
          planned in this web UI by that interval's owner: that owner, with
          no connection (the old connection has ended; no action is allowed).
        - Inside an interval otherwise (no web plan line, another user, or
          overlapping intervals): ambiguous, nobody sees it or acts on it.
        - Outside every interval, for a server not saved now: ambiguous.
        - Outside every interval, for a saved server: no record (shared, as
          before)."""

        events = []
        try:
            with open(self.config.audit) as handle:
                for line in handle:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        intervals, open_ = [], {}
        planned = {}
        for event in events:
            action, target = event.get("action"), event.get("target")
            if action == "temp_access_created" and target:
                if target in open_:  # never closed: assume it ran to its expiry
                    intervals.append(open_.pop(target))
                open_[target] = {"target": target, "owner": event.get("user"), "start": event.get("time", ""),
                                 "end": event.get("expires", ""), "connection": event.get("connection")}
            elif action == "temp_access_removed" and target in open_:
                interval = open_.pop(target)
                interval["end"] = min(interval["end"] or event.get("time", ""), event.get("time", ""))
                intervals.append(interval)
            elif action == "plan" and event.get("plan_id"):
                planned[event["plan_id"]] = event
        intervals += open_.values()
        saved = {entry["target_id"] for entry in self.targets()}
        out = {}
        directory = run.plans_dir()
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        for path in paths:
            plan_id = path.stem
            if not PLAN_ID_RE.fullmatch(plan_id) or plan_id in self.plan_owners:
                continue
            try:
                document = json.loads(path.read_text())
                target = document["target"]["target_id"]
                created = document.get("created_at", "")
            except (OSError, ValueError, KeyError, TypeError):
                continue
            line = planned.get(plan_id)
            when = (line or {}).get("time") or created
            if line and line.get("target") != target:
                inside = None
            else:
                inside = [i for i in intervals if i["target"] == target and i["start"] <= when <= (i["end"] or "~")]
            record = {"owner": None, "connection": None, "target": target, "source": "ambiguous"}
            if inside is not None and len(inside) == 1 and line and line.get("user") == inside[0]["owner"]:
                record = {"owner": inside[0]["owner"], "connection": None, "target": target,
                          "source": "legacy-audit"}
            elif inside == [] and target in saved:
                continue
            out[plan_id] = record
        return out

    def upgrade_legacy_plans(self):
        """Record ownership for plans made before records existed. Repeatable:
        existing records are never changed, and each run writes only what is
        missing. Returns the records written."""

        found = self.legacy_plan_owners()
        written = {}
        for plan_id, record in found.items():
            try:
                self.write_plan_owner(plan_id, record)
            except FileExistsError:
                continue
            written[plan_id] = record
            self.audit(None, "plan_owner_upgraded", plan_id=plan_id, target=record["target"],
                       owner=record["owner"], source=record["source"])
        return written

    def require_plan_owner(self, user, plan_id, target):
        """Approval, apply, report and resume: a plan made on a temporary
        server stays its owner's, and acts only through the same connection.
        A plan for a saved server never acts through a temporary one."""

        owner = self.plan_owners.get(plan_id)
        meta = self.temporary.get(target)
        if owner is not None:
            if owner["owner"] is None:
                raise ApprovalError("Nobody can use this plan: who made it on which connection is not known. "
                                    "Make a new plan.")
            if owner["owner"] != user:
                raise ApprovalError("This plan was made on another user's temporary server.")
            if owner["connection"] is None:
                raise ApprovalError(f"This plan was made on an earlier temporary connection to {target}, "
                                    "before ownership was recorded. Make a new plan.")
            if meta is None or meta.get("tag") != owner["connection"]:
                raise ApprovalError(f"This plan was made for an earlier connection to {target}, "
                                    "which has ended. Connect again and make a new plan.")
            return
        if meta is not None:
            raise ApprovalError(f"This plan was not made on the temporary server {target}. "
                                "Make a new plan.")

    def may_see(self, user, target, plan_id=None, run_by=None):
        """Plans and runs on saved servers are shared with every signed-in
        user. A temporary server's plans and runs are their owner's only,
        decided by the plan's recorded owner first, never by whichever server
        has that name now (Codex review, 2026-11-26)."""

        owner = self.plan_owners.get(plan_id)
        if owner is not None:
            return owner["owner"] is not None and owner["owner"] == user
        saved = any(entry["target_id"] == target for entry in self.targets())
        if plan_id is not None:
            # Every plan made on a temporary server has a record (bound before
            # it is stored, or upgraded at start), so a plan without one is a
            # saved server's: shared, never its name's current temporary owner's.
            return saved
        meta = self.temporary.get(target)
        if meta is not None:
            return meta["owner"] == user
        return saved or (user is not None and run_by == user)

    def running_on(self, target):
        return any(j.target == target and j.finished is None for j in self.jobs.recent(1000))

    def end_temporary(self, user, target, reason):
        """Remove the key line and the private folder. Returns True when done.
        A run still going keeps its access until it ends."""

        with self.lock:
            meta = self.temporary.get(target)
        if meta is None:
            return True
        if self.running_on(target):
            return False
        # Audit first, then forget it: whoever sees it gone also finds the audit line.
        if enrolment.end_temporary_access(self.temp_dir(target)):
            self.audit(user, "temp_access_removed", target=target, reason=reason, connection=meta.get("tag"))
            with self.lock:
                self.temporary.pop(target, None)
                self.records.pop(target, None)
            return True
        if time.time() >= meta["expires"]:
            # The key stopped working at its expiry-time: nothing left to log in with.
            enrolment.discard_temporary_access(self.temp_dir(target))
            self.audit(user, "temp_access_expired", target=target, reason=reason)
            with self.lock:
                self.temporary.pop(target, None)
            return True
        self.audit(user, "temp_access_remove_failed", target=target, reason=reason)
        return False

    def sweep_temporary(self, active_users=None):
        """End temporary access near its expiry, or when its owner has no
        session left (decision 2b: never past the user's session)."""

        now = time.time()
        for target, meta in list(self.temporary.items()):
            ending = now >= meta["expires"] - TEMP_MARGIN
            orphaned = active_users is not None and meta["owner"] not in active_users
            if ending or orphaned:
                self.end_temporary(meta["owner"], target, "expiry" if ending else "session ended")

    def targets(self):
        """Saved servers only; temporary ones are never in the inventory."""
        if not self.config.inventory.exists():
            return []
        return run.list_targets(self.inventory())

    def host_of(self, target):
        if target in self.temporary:
            return self.temporary[target]["host"]
        for entry in self.targets():
            if entry["target_id"] == target:
                return entry["host"]
        raise run.RunError(f"Unknown server: {target}")

    def target_entry(self, target, user=None):
        """A saved server, or the user's own temporary server (with "temporary": meta)."""

        if not TARGET_RE.fullmatch(target or ""):
            raise ValueError("Invalid server.")
        meta = self.temporary.get(target)
        if meta is not None:
            if user is None or meta["owner"] != user:
                raise KeyError(f"No saved server named {target}.")
            return dict(meta["entry"], temporary=meta)
        for entry in self.targets():
            if entry["target_id"] == target:
                return entry
        raise KeyError(f"No saved server named {target}.")

    def catalog(self):
        """[{id, name, description, category, label, draft, panels}] sorted by
        category label, then name. panels: "any" or the panel IDs that own the
        component (its variants' conflicts.panels, as the planner reads them)."""

        out = []
        for cid, definition in load_component_definitions(self.config.definitions_dir).items():
            owners = [v.conflicts.panels for v in definition.variants]
            panels = "any" if "any" in owners else sorted({p for o in owners for p in o})
            out.append({"id": cid, "name": definition.display_name, "description": definition.description,
                        "category": definition.category,
                        "label": CATEGORY_LABELS.get(definition.category, definition.category),
                        "draft": all(v.support == "draft" for v in definition.variants),
                        "panels": panels})
        return sorted(out, key=lambda a: (a["label"], a["name"].lower()))

    def installed(self, target):
        """(record or None, reason it is unavailable). Cached briefly: each
        read is an SSH login to the server."""

        now = time.monotonic()
        with self.lock:
            cached = self.records.get(target)
        if cached and now - cached[0] < RECORD_TTL:
            return cached[1], None
        try:
            record = run.read_record(target, self.inventory(target), **self.access(target))
        except (run.RunError, OSError, ValueError) as error:
            return None, str(error)
        with self.lock:
            self.records[target] = (now, record)
        return record, None

    def env(self, target=None):
        env = dict(os.environ, DSSA_INVENTORY_FILE=self.inventory(target))
        if target in self.temporary:
            env["DSSA_KNOWN_HOSTS"] = self.access(target)["known_hosts"]
            env.pop("DSSA_SSH_KEY", None)
        if self.config.definitions_dir:
            env["DSSA_DEFINITIONS_DIR"] = str(self.config.definitions_dir)
        return env

    def check(self, plan_id):
        plan_bytes, plan, path = run.load_plan(plan_id)
        target = plan["target"]["target_id"]
        keys = known_host_fingerprints(self.host_of(target), **self.access(target))
        return plan_bytes, plan, path, keys, preflight_plan(plan_bytes, keys, definitions_dir=self.config.definitions_dir)

    # --- operations -------------------------------------------------------------

    def make_plans(self, user, targets, components, operation, all_or_nothing, allow_draft=False):
        if operation not in OPERATIONS:
            raise ValueError("Unknown operation.")
        if not targets or not all(TARGET_RE.fullmatch(t) for t in targets):
            raise ValueError("Choose at least one server.")
        if not components or not all(COMPONENT_RE.fullmatch(c) for c in components):
            raise ValueError("Choose at least one component.")
        for target in targets:
            self.require_owner(user, target)
        plan_ids = []
        for target in targets:
            # Codex follow-up (2026-11-26): a temporary server's identity is
            # captured before planning and must be unchanged afterwards; the
            # ownership record is written before the plan is stored.
            with self.lock:
                meta = self.temporary.get(target)
                connection = meta["tag"] if meta else None
                if meta is not None and meta["owner"] != user:
                    raise ApprovalError(f"{target} is another user's temporary server.")
                inventory, access = self.inventory(target), self.access(target)
            # Drafts: lab servers only; dssa-run.sh refuses them elsewhere.
            plan_bytes, plan = run.make_plan(target, components, allow_draft and operation == "install",
                                             inventory, operation, all_or_nothing, **access)
            plan_id = plan["plan_id"]
            with self.lock:
                now = self.temporary.get(target)
                if connection is not None and (now is None or now["tag"] != connection or now["owner"] != user):
                    raise ApprovalError(f"Access to {target} ended or changed while planning, so the plan was "
                                        "discarded. Connect again and plan again.")
                if connection is None and now is not None:
                    raise ApprovalError(f"{target} became a temporary server while planning, so the plan was "
                                        "discarded. Plan again.")
                if connection is not None:
                    self.bind_plan(user, plan_id, target, connection)
                try:
                    run.save_plan(plan_bytes)
                except BaseException:
                    if connection is not None:
                        self.unbind_plan(plan_id)
                    raise
            plan_ids.append(plan_id)
            self.audit(user, "plan", target=target, plan_id=plan_id, operation=operation,
                       components=components, **({"connection": connection} if connection else {}))
        return plan_ids

    def make_single_plan(self, user, target, component, operation, allow_draft=False):
        return self.make_app_plan(user, target, [component], operation, allow_draft)

    def make_app_plan(self, user, target, components, operation, allow_draft=False):
        """The catalog's selection (decision A, 2026-11-26): one server, one
        operation and one or more applications, in one plan through the same
        planner as everything else. One approval and one apply cover it."""

        entry = self.target_entry(target, user)
        if operation not in OPERATIONS:
            raise ValueError("Unknown operation.")
        if not components:
            raise ValueError("Choose at least one application.")
        if len(components) > MAX_SELECTED:
            raise ValueError(f"Choose at most {MAX_SELECTED} applications for one plan.")
        known = {a["id"] for a in self.catalog()}
        if any(c not in known for c in components):
            raise ValueError("Unknown application.")
        allow_draft = allow_draft and bool(entry.get("lab")) and operation == "install"
        plan_id = self.make_plans(user, [target], list(components), operation, False, allow_draft)[0]
        with self.lock:
            self.records.pop(target, None)
        return plan_id

    def approve(self, user, plan_id, typed):
        """typed: {component: name typed, "_confirm": APPROVE}."""

        plan_bytes, plan, _, keys, check = self.check(plan_id)
        self.require_owner(user, plan["target"]["target_id"])
        self.require_plan_owner(user, plan_id, plan["target"]["target_id"])
        if check["verification_only"]:
            return None
        for name, _what, _warnings in risky_steps(plan, include_drafts=True):
            if typed.get(name, "").strip() != name:
                raise ApprovalError(f"{name} not confirmed; nothing was approved.")
        record = approve_plan(plan_bytes, keys, typed.get("_confirm", "").strip(), operator=user,
                              definitions_dir=self.config.definitions_dir)
        self.audit(user, "approve", plan_id=plan_id, target=plan["target"]["target_id"])
        return record

    def start_apply(self, user, plan_id, on_finish=None):
        """Like main.step_apply: log in first, then use the approval, then run."""

        plan_bytes, plan, path, keys, check = self.check(plan_id)
        target = plan["target"]["target_id"]
        self.require_owner(user, target)
        self.require_plan_owner(user, plan_id, target)
        if not check["passed"] and not check["requires_approval"]:
            raise ApprovalError("Apply refused: the plan failed its checks.", check["issues"])
        if not check["verification_only"]:
            # Decision 3b (2026-11-25): only the user who approved may apply.
            # Checked before logging in, so a refusal leaves the approval unused.
            by = approver(plan_id)
            if by is not None and by != user:
                raise ApprovalError(f"Apply refused: {by} approved this plan, so only {by} can apply it. "
                                    "The approval was not used.")
            if run.check_login(target, plan["target"]["host_key_sha256"], self.inventory(target),
                               **self.access(target)) != 0:
                raise ApprovalError("Apply refused: cannot log in to the server. The approval was not used.")
            consume_approval(plan_id, keys, definitions_dir=self.config.definitions_dir,
                             expected_operator=user)
            path = default_state_dir() / "approvals" / plan_id / "plan.json"
            plan_bytes = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        if target in self.temporary:
            # Item 2: a successful run ends temporary access; after a failure it
            # stays (until the window ends) so Report and Resume rollback still work.
            then = on_finish

            def on_finish(job, then=then):
                if then:
                    then(job)
                if job.code == 0:
                    threading.Thread(target=self.end_temporary, args=(user, target, "run finished"),
                                     daemon=True).start()
        job = Job("apply", ["bash", str(run.RUN_SCRIPT), "apply", target, str(path), digest],
                  self.env(target), plan_id, target, user, on_finish)
        self.audit(user, "apply", plan_id=plan_id, target=target, job=job.id)
        return self.jobs.start(job)

    def start_remote(self, user, kind, plan_id):
        _, plan, _ = run.load_plan(plan_id)
        target, key = plan["target"]["target_id"], plan["target"]["host_key_sha256"]
        self.require_owner(user, target)
        self.require_plan_owner(user, plan_id, target)
        command = "report" if kind == "report" else "resume-rollback"
        job = Job(kind, ["bash", str(run.RUN_SCRIPT), command, target, plan_id, key],
                  self.env(target), plan_id, target, user)
        self.audit(user, kind, plan_id=plan_id, target=target, job=job.id)
        return self.jobs.start(job)

    # --- batches (decision 7) -------------------------------------------------

    def new_batch(self, user, plan_ids):
        batch_id = secrets.token_hex(8)
        with self.lock:
            self.batches[batch_id] = {"plans": plan_ids, "user": user, "jobs": [], "state": "planned"}
        return batch_id

    def approve_batch(self, user, batch_id, typed):
        batch = self.batches[batch_id]
        for plan_id in batch["plans"]:
            self.approve(user, plan_id, typed)
        batch["state"] = "approved"

    def apply_batch(self, user, batch_id):
        batch = self.batches[batch_id]
        if batch["state"] != "approved":
            raise ApprovalError("Approve the batch first.")
        batch["state"] = "running"
        self._next_in_batch(user, batch_id, 0)

    def _next_in_batch(self, user, batch_id, index):
        batch = self.batches[batch_id]
        if index >= len(batch["plans"]):
            batch["state"] = "finished"
            return

        def done(job):
            if job.code == 0:
                self._next_in_batch(user, batch_id, index + 1)
            else:
                batch["state"] = f"stopped at {job.target} (exit {job.code}); the rest were not started"

        try:
            job = self.start_apply(user, batch["plans"][index], on_finish=done)
            batch["jobs"].append(job.id)
        except (ApprovalError, run.RunError, OSError) as error:
            batch["state"] = f"stopped before {batch['plans'][index][:8]}: {error}"


# --- HTTP ---------------------------------------------------------------------------

def page(title, body, session=None):
    nav = ""
    if session:
        nav = (f'<nav aria-label="Workspace"><a href="/">Workspace</a> <a href="/servers">Saved servers</a> '
               f'<a href="/servers/add">Add server</a> <a href="/jobs">Runs</a> <span class="user">{esc(session["user"])}</span>'
               f'<form method="post" action="/logout" class="inline">{csrf(session)}'
               f'<button>Log out</button></form></nav>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)} · DSSA</title><link rel="stylesheet" href="/static/style.css">'
            f'<script src="/static/app.js" defer></script></head>'
            f'<body>{nav}<main><h1>{esc(title)}</h1>{body}</main></body></html>')


def search_text(app):
    return " ".join((app["id"], app["name"], app["label"], app["description"])).lower()


def managed_by(catalog, recorded):
    """{component: panel names} for components a recorded control panel owns.
    Only panels in DSSA's install record are known here; the planner also
    detects panels installed some other way."""

    names = {a["id"]: a["name"] for a in catalog}
    panels = [a["id"] for a in catalog if a["category"] == "control_panel" and a["id"] in recorded]
    out = {}
    for app in catalog:
        if app["category"] == "control_panel" or not panels:
            continue
        owners = panels if app["panels"] == "any" else [p for p in panels if p in app["panels"]]
        if owners:
            out[app["id"]] = ", ".join(names[p] for p in owners)
    return out


REVIEWED_BOX = ('<label class="choice"><input type="checkbox" name="reviewed" value="1" required> '
                'I reviewed the target, operation and warnings.</label>')


def approver(plan_id):
    """Who approved the plan in the web UI, or None (decision 3b)."""

    try:
        record = json.loads((default_state_dir() / "approvals" / plan_id / "approval.json").read_text())
    except (OSError, ValueError):
        return None
    return record.get("approved_by")


def csrf(session):
    return f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'


def plan_table(plan):
    rows = []
    for step in plan["steps"]:
        reasons = "".join(f"<li>{esc(r)}</li>" for r in step["reasons"])
        rows.append(f'<tr class="a-{esc(step["action"].lower())}"><td>{esc(step["component"])}</td>'
                    f'<td>{esc(step["action"])}</td><td>{esc(step["variant_id"] or "-")}</td>'
                    f'<td><ul>{reasons}</ul></td></tr>')
    mode = " · ALL OR NOTHING" if plan.get("all_or_nothing") else ""
    return (f'<p>Plan <code>{esc(plan["plan_id"])}</code> on <b>{esc(plan["target"]["target_id"])}</b> '
            f'({esc(plan["package_manager"])}) · {esc(plan["operation"].upper())}{mode} · '
            f'host key <code>{esc(plan["target"]["host_key_sha256"])}</code> · created {esc(plan["created_at"])}</p>'
            f'<table><tr><th>Component</th><th>Action</th><th>Variant</th><th>Reasons</th></tr>{"".join(rows)}</table>')


def confirm_fields(plan):
    out = []
    for name, what, warnings in risky_steps(plan, include_drafts=True):
        notes = "".join(f'<p class="warn">{esc(w)}</p>' for w in warnings)
        out.append(f'{notes}<label>Type <b>{esc(name)}</b> to {esc(what)}: '
                   f'<input name="name_{esc(name)}" autocomplete="off" required></label>')
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    app = None
    secure = True
    server_version = "DSSA"
    sys_version = ""

    # --- plumbing ---------------------------------------------------------------

    def log_message(self, fmt, *args):  # no request bodies or query strings in logs
        pass

    def _headers(self, status, ctype="text/html; charset=utf-8", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                         "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()

    def send_html(self, body, status=HTTPStatus.OK, extra=()):
        data = body.encode()
        self._headers(status, extra=extra)
        self.wfile.write(data)

    def send_json(self, value, status=HTTPStatus.OK):
        self._headers(status, "application/json")
        self.wfile.write(json.dumps(value).encode())

    def redirect(self, where, extra=()):
        self._headers(HTTPStatus.SEE_OTHER, extra=[("Location", where), *extra])

    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie[COOKIE].value if COOKIE in cookie else None
        return token, self.app.sessions.get(token)

    def form(self):
        """Last value per field; self.fields keeps every value (checkbox lists)."""

        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            raise ValueError("Request too large.")
        self.fields = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        return {k: v[-1] for k, v in self.fields.items()}

    def fail(self, session, error, back="/", status=HTTPStatus.BAD_REQUEST):
        issues = "".join(f"<li>{esc(i)}</li>" for i in getattr(error, "issues", []) if str(i) != str(error))
        self.send_html(page("Not done", f'<p class="error" role="alert">{esc(error)}</p><ul>{issues}</ul>'
                                        f'<p><a href="{esc(back)}">Back</a></p>', session),
                       status)

    # --- GET --------------------------------------------------------------------

    def do_GET(self):
        self._safely(self._get)

    def do_POST(self):
        self._safely(self._post)

    def _safely(self, handler):
        """An unexpected error answers 500 instead of dropping the
        connection; the traceback goes to the UI's terminal only."""
        try:
            handler()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self.send_html(page("Error", "<p class='error'>Something went wrong in the web UI; "
                                    "the details are in its terminal.</p><p><a href='/'>Back</a></p>"),
                               HTTPStatus.INTERNAL_SERVER_ERROR)
            except OSError:
                pass

    def _get(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
        if path == "/login":
            return self.send_html(page("Log in", LOGIN_FORM))
        token, session = self.session()
        if session is None:
            return self.redirect("/login")
        try:
            if path == "/":
                return self.workspace(session)
            if path == "/batch":
                return self.home(session)
            if path == "/servers":
                return self.saved_servers(session)
            if path == "/servers/add":
                return self.send_html(page("Add a server", ADD_FORM.format(csrf=csrf(session)), session))
            if path.startswith("/servers/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "apps":
                    return self.apps_page(session, parts[2], urllib.parse.parse_qs(url.query))
                if len(parts) == 3:
                    return self.server_page(session, parts[2])
            if path.startswith("/plans/"):
                return self.plan_page(session, path.split("/")[2])
            if path.startswith("/batches/"):
                return self.batch_page(session, path.split("/")[2])
            if path == "/jobs":
                return self.jobs_page(session)
            if path.startswith("/jobs/"):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "poll":
                    return self.poll(session, parts[2], urllib.parse.parse_qs(url.query).get("offset", ["0"])[0])
                return self.job_page(session, parts[2])
        except KeyError as error:
            return self.fail(session, error.args[0] if error.args else error, status=HTTPStatus.NOT_FOUND)
        except (run.RunError, ApprovalError, ValueError, OSError) as error:
            return self.fail(session, error)
        self.send_html(page("Not found", "", session), HTTPStatus.NOT_FOUND)

    def static(self, name):
        files = {"style.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
        if name not in files:
            return self.send_html("", HTTPStatus.NOT_FOUND)
        self._headers(HTTPStatus.OK, files[name])
        self.wfile.write((STATIC / name).read_bytes())

    def workspace(self, session):
        body = ('<p class="hint">Resume with a saved server or connect a new one.</p>'
                '<div class="options">'
                '<a class="card option" href="/servers"><strong>Saved servers</strong>'
                '<span class="hint">Choose from your inventory and open its applications.</span></a>'
                '<a class="card option" href="/servers/add"><strong>Add server</strong>'
                '<span class="hint">Check a new server\'s host key, then save it or use it for this '
                'setup only.</span></a>'
                '</div>')
        temps = self.app.temporary_of(session["user"])
        if temps:
            rows = "".join(
                f'<tr><td>{esc(m["target_id"])}</td><td><code>{esc(m["host"])}</code></td>'
                f'<td>{esc(time.strftime("%H:%M UTC", time.gmtime(m["expires"])))}</td>'
                f'<td><a href="/servers/{esc(m["target_id"])}/apps">Applications &rarr;</a></td>'
                f'<td><form method="post" action="/servers/{esc(m["target_id"])}/end" class="inline">'
                f'{csrf(session)}<button>End access</button></form></td></tr>' for m in temps)
            body += ('<h2>This setup only</h2><p class="hint">Servers you connected without saving. '
                     'Only you see them.</p><table><tr><th scope="col">Server</th><th scope="col">Host</th>'
                     f'<th scope="col">Access ends</th><th scope="col">Open</th><th scope="col"></th></tr>{rows}</table>')
        self.send_html(page("Workspace", body, session))

    def saved_servers(self, session):
        targets = self.app.targets()
        if not targets:
            body = ('<p>No saved servers yet.</p>'
                    '<p><a class="button" href="/servers/add">Add server</a></p>')
            return self.send_html(page("Saved servers", body, session))
        rows = "".join(
            f'<tr><td><a href="/servers/{esc(t["target_id"])}">{esc(t["target_id"])}</a></td>'
            f'<td><code>{esc(t["host"])}</code></td><td>{"lab" if t.get("lab") else ""}</td>'
            f'<td><a href="/servers/{esc(t["target_id"])}/apps" '
            f'aria-label="Applications on {esc(t["target_id"])}">Applications &rarr;</a></td></tr>'
            for t in targets)
        body = ('<p class="hint">Choose a server to browse its applications.</p>'
                '<table><caption class="visually-hidden">Saved servers</caption>'
                '<tr><th scope="col">Server</th><th scope="col">Host</th><th scope="col">Kind</th>'
                f'<th scope="col">Open</th></tr>{rows}</table>'
                '<p class="hint"><a href="/batch">Batch plan</a> (several servers or applications) '
                'stays available from the earlier screen.</p>')
        self.send_html(page("Saved servers", body, session))

    def apps_page(self, session, target, query):
        entry = self.app.target_entry(target, session["user"])
        search = (query.get("q") or [""])[-1].strip()[:64]
        category = (query.get("category") or [""])[-1]
        chosen = (query.get("app") or [""])[-1]
        catalog = self.app.catalog()
        by_id = {a["id"]: a for a in catalog}
        # The selection lives in the URL (sel=…), so it survives choosing a
        # row or filtering, with or without JavaScript.
        selected = [c for c in dict.fromkeys(query.get("sel") or []) if c in by_id][:MAX_SELECTED]
        if category not in CATEGORY_LABELS:
            category = ""
        record, unavailable = self.app.installed(target)
        recorded = (record or {}).get("components", {})
        managed = managed_by(catalog, recorded)
        needle = search.lower()
        shown = [a for a in catalog
                 if (not category or a["category"] == category)
                 and (not needle or needle in search_text(a))]

        def link(app_id):
            params = [("app", app_id)]
            if search:
                params.append(("q", search))
            if category:
                params.append(("category", category))
            params += [("sel", c) for c in selected]
            return f"/servers/{target}/apps?" + urllib.parse.urlencode(params)

        def tags(app):
            out = ""
            if app["id"] in recorded:
                how = recorded[app["id"]].get("how")
                out += ('<span class="chip ok">Installed (adopted)</span>' if how == "adopted"
                        else '<span class="chip ok">Installed</span>')
            if app["id"] in managed:
                out += f'<span class="chip warn">Managed by {esc(managed[app["id"]])}</span>'
            if app["draft"]:
                out += '<span class="chip warn">Draft</span>'
            return out

        def state_attrs(app):
            installed = "" if record is None else ("1" if app["id"] in recorded else "0")
            return (f'data-id="{esc(app["id"])}" data-name="{esc(app["name"])}" '
                    f'data-installed="{installed}" data-draft="{"1" if app["draft"] else "0"}"')

        CURRENT = ' aria-current="true"'
        rows = "".join(
            f'<li><input type="checkbox" class="pick" form="plan-form" name="component" value="{esc(a["id"])}" '
            f'{state_attrs(a)} aria-label="Select {esc(a["name"])}"{" checked" if a["id"] in selected else ""}>'
            f'<a class="app-row" href="{esc(link(a["id"]))}" data-search="{esc(search_text(a))}" '
            f'data-category="{esc(a["category"])}"{CURRENT if a["id"] == chosen else ""}>'
            f'<span><strong>{esc(a["name"])}</strong> <span class="hint">· {esc(a["label"])}</span>'
            f'{"<span class=\"desc\">" + esc(a["description"]) + "</span>" if a["description"] else ""}</span>'
            f'<span class="tags">{tags(a)}</span></a></li>'
            for a in shown)
        options = "".join(f'<option value="{esc(k)}"{" selected" if k == category else ""}>{esc(v)}</option>'
                          for k, v in sorted(CATEGORY_LABELS.items(), key=lambda kv: kv[1]))
        state = ('<p class="hint">Installed state: from DSSA\'s install record on the server. A control panel '
                 'installed without DSSA is found when you plan.</p>' if record is not None
                 else f'<p class="warn" role="status">Installed state unavailable: {esc(unavailable.rstrip("."))}. '
                      'Every action is offered; the planner checks the server.</p>')
        filters = (f'<form method="get" action="/servers/{esc(target)}/apps" class="filters" role="search" id="filters">'
                   f'<label>Search <input type="search" name="q" value="{esc(search)}" id="app-search" '
                   f'autocomplete="off"></label>'
                   f'<label>Category <select name="category" id="app-category"><option value="">All categories</option>'
                   f'{options}</select></label>'
                   + (f'<input type="hidden" name="app" value="{esc(chosen)}">' if chosen else "")
                   + "".join(f'<input type="hidden" name="sel" value="{esc(c)}" class="keep-sel">' for c in selected)
                   + '<button>Filter</button></form>')
        listing = (f'<ul class="catalog" id="app-list" aria-label="Applications">{rows}</ul>' if rows
                   else '<p class="hint" id="app-empty">No matching applications.</p>')
        # Selected applications the filter hides still belong to the plan.
        shown_ids = {a["id"] for a in shown}
        kept = "".join(f'<input type="hidden" class="pick" name="component" value="{esc(c)}" {state_attrs(by_id[c])}>'
                       for c in selected if c not in shown_ids)
        temp = entry.get("temporary")
        where = (f' · This setup only, access ends {esc(time.strftime("%H:%M UTC", time.gmtime(temp["expires"])))}'
                 if temp else " · Saved server")
        side = (self.details(by_id.get(chosen), selected, managed)
                + self.selection(session, entry, by_id, recorded, record is not None, selected, kept, target))
        body = (f'<p><a href="{"/" if temp else "/servers"}">&larr; {"Workspace" if temp else "Saved servers"}</a></p>'
                f'<p>{esc(target)} · <code>{esc(entry["host"])}</code>{" · lab" if entry.get("lab") else ""}{where}</p>'
                f'{state}<div class="grid"><section class="card">{filters}{listing}</section>'
                f'<section class="card" aria-live="polite">{side}</section></div>')
        self.send_html(page("Applications", body, session))

    @staticmethod
    def details(app, selected, managed):
        """The chosen row's description (its link), next to the selection."""

        if app is None:
            return ""
        notes = ""
        if app["id"] in managed:
            notes = (f'<p class="notice">Managed by {esc(managed[app["id"]])}: use the panel\'s own tools. '
                     'The planner holds this for review on a panel server.</p>')
        if app["draft"]:
            notes += '<p class="warn">Draft component: lab servers only.</p>'
        where = ("In your selection." if app["id"] in selected
                 else "Tick its box in the list to add it to your selection.")
        return (f'<h2 id="selected-app" tabindex="-1">{esc(app["name"])}</h2>'
                f'<p class="hint">{esc(app["label"])} · <code>{esc(app["id"])}</code></p>'
                f'{"<p>" + esc(app["description"]) + "</p>" if app["description"] else ""}{notes}'
                f'<p class="hint" id="chosen-hint" data-id="{esc(app["id"])}">{where}</p><hr>')

    def selection(self, session, entry, by_id, recorded, known, selected, kept, target):
        """One action for every selected application, then one plan."""

        names = "".join(f'<li>{esc(by_id[c]["name"])}</li>' for c in selected)
        installed = {c in recorded for c in selected} if known else set()
        mixed = len(installed) > 1
        if installed == {True}:
            default = "update"
        else:
            default = "install"
        hints = {"install": "not installed", "adopt": "installed without DSSA",
                 "update": "installed", "remove": "installed by DSSA"}
        needs = {"install": "0", "adopt": "0", "update": "1", "remove": "1"}
        radios = "".join(
            f'<label><input type="radio" name="operation" value="{op}" data-needs="{needs[op]}" required'
            f'{" checked" if op == default else ""}> {esc(OPERATION_LABELS[op])} '
            f'<span class="hint">({hints[op]})</span></label>'
            for op in ("install", "adopt", "update", "remove"))
        drafts = any(by_id[c]["draft"] for c in selected)
        draft = (f'<div id="draft-choice"{"" if drafts else " hidden"}>' + (
            '<label><input type="checkbox" name="allow_draft" value="1"> allow the drafts in this selection '
            '(you type each draft\'s name again at approval)</label>' if entry.get("lab") else
            '<p class="hint">Drafts run on lab servers only; on this server the planner holds them.</p>')
            + '</div>')
        return (f'<form method="post" action="/servers/{esc(target)}/plan" id="plan-form">{csrf(session)}'
                f'<h2>Selected (<span id="sel-count" tabindex="-1">{len(selected)}</span>)</h2>'
                f'<ul id="sel-list">{names}</ul>{kept}'
                f'<p class="hint" id="sel-empty"{" hidden" if selected else ""}>Tick one or more applications '
                f'in the list. They go into one plan, with one approval.</p>'
                f'<p><a href="/servers/{esc(target)}/apps" id="sel-clear"{"" if selected else " hidden"}>'
                f'Clear selection</a></p>'
                f'<fieldset><legend>Action for all selected</legend>{radios}</fieldset>'
                f'<p class="warn" id="sel-mixed"{"" if mixed else " hidden"}>Your selection mixes installed and '
                f'not installed applications. One plan has one action: split them into two plans.</p>'
                f'{draft}<button id="plan-button">Review plan</button>'
                f'<p class="hint">Planning reads the server and changes nothing. Approval and Apply '
                f'are separate steps.</p></form>')

    def home(self, session):
        """The earlier multi-server, multi-component form (batches, decision 7), now at /batch."""
        rows = "".join(
            f'<tr><td><a href="/servers/{esc(t["target_id"])}">{esc(t["target_id"])}</a></td>'
            f'<td>{esc(t["host"])}</td><td>{"lab" if t["lab"] else ""}</td>'
            f'<td><input type="checkbox" name="targets" value="{esc(t["target_id"])}"></td></tr>'
            for t in self.app.targets())
        catalog = load_component_definitions(self.app.config.definitions_dir)
        options = "".join(f'<label><input type="checkbox" name="components" value="{esc(c)}"> '
                          f'{esc(c)} <small>{esc(catalog[c].display_name)}</small></label>' for c in sorted(catalog))
        body = (f'<form method="post" action="/plan">{csrf(session)}'
                f'<table><tr><th>Server</th><th>Host</th><th></th><th>Plan</th></tr>{rows}</table>'
                f'<fieldset><legend>Components</legend>{options}</fieldset>'
                f'<label>Operation <select name="operation">'
                + "".join(f'<option value="{o}">{o}</option>' for o in OPERATIONS)
                + '</select></label> <label><input type="checkbox" name="all_or_nothing" value="1"> all or nothing</label> '
                '<label><input type="checkbox" name="allow_draft" value="1"> allow drafts (lab servers only)</label> '
                '<button>Make plan</button><p class="hint">Several servers make a batch: one approval, '
                'applied server by server, stopping at the first failure.</p></form>')
        self.send_html(page("Batch plan", body, session))

    # Not "server": BaseHTTPRequestHandler.server is the HTTP server itself.
    def server_page(self, session, target):
        if not TARGET_RE.fullmatch(target):
            raise ValueError("Invalid server.")
        if target in self.app.temporary:
            self.app.target_entry(target, session["user"])  # the owner only
            record = run.read_record(target, self.app.inventory(target), **self.app.access(target))
        else:
            record = run.read_record(target, self.app.inventory())
        rows = "".join(
            f'<tr><td>{esc(c)}</td><td>{esc(e.get("how"))}</td><td>{esc(e.get("recorded_at"))}</td>'
            f'<td>{esc(", ".join(p["package"] + " " + p["version"] for p in e.get("packages", [])))}</td></tr>'
            for c, e in sorted(record["components"].items()))
        body = (f'<p><a href="/servers/{esc(target)}/apps">Applications on {esc(target)} &rarr;</a></p>'
                f'<h2>Installed by DSSA</h2><table><tr><th>Component</th><th>How</th><th>When</th>'
                f'<th>Packages</th></tr>{rows or "<tr><td colspan=4>Nothing recorded yet.</td></tr>"}</table>')
        self.send_html(page(target, body, session))

    def plan_page(self, session, plan_id):
        if not PLAN_ID_RE.fullmatch(plan_id):
            raise ValueError("Invalid plan ID.")
        _, plan, _, _, check = self.app.check(plan_id)
        if not self.app.may_see(session["user"], plan["target"]["target_id"], plan_id=plan_id):
            raise KeyError("No such plan.")
        approvals = default_state_dir() / "approvals" / plan_id
        approved = (approvals / "approval.json").exists()
        used = (approvals / "consumed").exists()
        by = approver(plan_id) if approved else None
        target = plan["target"]["target_id"]
        runnable = plan.get("runnable", [])
        held = [s["component"] for s in plan["steps"] if s["component"] not in runnable]
        # "Changes" lists only what the run changes; SKIP steps are verified
        # (lab finding, web UI pass 1 step 9).
        skipped = [s["component"] for s in plan["steps"] if s["component"] in runnable and s["action"] == "SKIP"]
        changes = [c for c in runnable if c not in skipped]
        body = (f'<dl class="meta"><div><dt>Server</dt><dd>{esc(target)}</dd></div>'
                f'<div><dt>Operation</dt><dd>{esc(plan["operation"])}</dd></div>'
                f'<div><dt>Changes</dt><dd>{esc(", ".join(changes) or "nothing")}</dd></div>'
                + (f'<div><dt>Verified only</dt><dd>{esc(", ".join(skipped))}</dd></div>' if skipped else "")
                + f'<div><dt>Held or blocked</dt><dd>{esc(", ".join(held) or "none")}</dd></div></dl>'
                + plan_table(plan))
        # A plan runs once; after that only its result (Report) makes sense
        # (lab finding, combined test step 12).
        saved = run.runs_dir() / plan_id / "summary.json"
        if saved.is_file():
            try:
                summary = json.loads(saved.read_text())
                outcomes = ", ".join(f'{r.get("component")}: {r.get("outcome")}' for r in summary.get("results", []))
            except (OSError, ValueError):
                summary, outcomes = {}, ""
            body += (f'<p>Already run: status <b>{esc(summary.get("status", "?"))}</b>'
                     f'{" (" + esc(outcomes) + ")" if outcomes else ""}. A plan runs only once; '
                     f'make a new plan to run again.</p>')
            body += (f'<form method="post" action="/plans/{plan_id}/report" class="inline">{csrf(session)}'
                     f'<button>Report</button></form>')
            return self.send_html(page("Plan", body, session))
        issues = list(check["issues"])
        if not runnable:
            body += (f'<p class="notice" role="status">Nothing in this plan can run on <b>{esc(target)}</b>: '
                     f'the planner held or blocked every step. Its reasons are in the table above.</p>')
            # The notice says it already (lab finding, pass 1 step 10).
            issues = [i for i in issues if i != "Nothing in the plan is runnable."]
        if issues:
            body += "<ul class='error'>" + "".join(f"<li>{esc(i)}</li>" for i in issues) + "</ul>"
        if check["verification_only"] and check["passed"]:
            body += (f'<form method="post" action="/plans/{plan_id}/apply">{csrf(session)}'
                     f'<p>Verification only: no approval needed.</p><button>Run verification</button></form>')
        elif check["passed"] and not approved:
            body += (f'<form method="post" action="/plans/{plan_id}/approve">{csrf(session)}'
                     f'<p>Approving runs exactly: <b>{esc(", ".join(plan["runnable"]))}</b> (single use, '
                     f'30 minutes from planning). Only you can apply it.</p>{confirm_fields(plan)}'
                     f'{REVIEWED_BOX}<button>Approve plan</button></form>')
        elif approved and not used and by not in (None, session["user"]):
            body += (f'<p class="notice">Approved by <b>{esc(by)}</b>; only {esc(by)} can apply it.</p>')
        elif approved and not used:
            body += (f'<form method="post" action="/plans/{plan_id}/apply">{csrf(session)}'
                     f'<p>Approved by {esc(by or "you")}. Apply is a separate step: it logs in first; '
                     f'sudo will ask on the server, in the run page.</p>'
                     f'<button>Apply</button></form>')
        # Report and Resume rollback only once the plan has been applied
        # (lab finding, pass 1 step 10): before that there is nothing to read.
        started = used or (run.runs_dir() / plan_id).exists() or any(
            j.plan_id == plan_id for j in self.app.jobs.recent(1000))
        if started:
            for kind in ("report", "resume"):
                body += (f'<form method="post" action="/plans/{plan_id}/{kind}" class="inline">{csrf(session)}'
                         f'<button>{"Report" if kind == "report" else "Resume rollback"}</button></form>')
        body += f'<p><a href="/servers/{esc(target)}/apps">&larr; Applications on {esc(target)}</a></p>'
        self.send_html(page("Plan", body, session))

    def batch_page(self, session, batch_id):
        batch = self.app.batches[batch_id]
        parts, plans = [], []
        for plan_id in batch["plans"]:
            plan_bytes, plan, _ = run.load_plan(plan_id)
            plans.append(plan)
            parts.append(plan_table(plan) + f'<p>Plan hash <code>{esc(plan_digest(plan_bytes))}</code></p>')
        body = f'<p>State: <b>{esc(batch["state"])}</b></p>' + "".join(parts)
        if batch["state"] == "planned":
            fields, seen = [], set()
            for plan in plans:
                for name, what, warnings in risky_steps(plan, include_drafts=True):
                    if name not in seen:
                        seen.add(name)
                        notes = "".join(f'<p class="warn">{esc(w)}</p>' for w in warnings)
                        fields.append(f'{notes}<label>Type <b>{esc(name)}</b> to {esc(what)} on every server: '
                                      f'<input name="name_{esc(name)}" autocomplete="off" required></label>')
            body += (f'<form method="post" action="/batches/{batch_id}/approve">{csrf(session)}'
                     f'<p>One approval for all {len(plans)} plans above (each bound to its hash).</p>'
                     f'{"".join(fields)}{REVIEWED_BOX}'
                     f'<button>Approve batch</button></form>')
        elif batch["state"] == "approved":
            body += (f'<form method="post" action="/batches/{batch_id}/apply">{csrf(session)}'
                     f'<button>Apply server by server</button></form>')
        body += "".join(f'<p><a href="/jobs/{j}">Run {esc(j)}</a></p>' for j in batch["jobs"])
        self.send_html(page("Batch", body, session))

    def jobs_page(self, session):
        rows = "".join(
            f'<tr><td><a href="/jobs/{j.id}">{esc(j.id)}</a></td><td>{esc(j.kind)}</td><td>{esc(j.target)}</td>'
            f'<td>{esc(j.user)}</td><td>{"running" if j.finished is None else esc(j.code)}</td></tr>'
            for j in self.app.jobs.recent() if self.app.may_see(session["user"], j.target, j.plan_id, j.user))
        self.send_html(page("Runs", f'<table><tr><th>Run</th><th>Kind</th><th>Server</th><th>By</th>'
                                    f'<th>Exit</th></tr>{rows}</table>', session))

    def job_page(self, session, job_id):
        job = self.visible_job(session, job_id)
        if job is None:
            raise KeyError("No such run.")
        body = (f'<p>{esc(job.kind)} of plan <a href="/plans/{esc(job.plan_id)}">{esc(job.plan_id)}</a> '
                f'on {esc(job.target)}</p><pre id="terminal" data-job="{esc(job.id)}"></pre>'
                f'<form id="input" method="post" action="/jobs/{esc(job.id)}/input">{csrf(session)}'
                f'<label>Type into the server\'s terminal (for example the sudo password; it is not shown or stored): '
                f'<input type="password" name="line" autocomplete="off"></label><button>Send</button></form>'
                f'<p id="status"></p>')
        self.send_html(page("Run", body, session))

    def visible_job(self, session, job_id):
        job = self.app.jobs.get(job_id) if JOB_ID_RE.fullmatch(job_id) else None
        if job is None or not self.app.may_see(session["user"], job.target, job.plan_id, job.user):
            return None
        return job

    def poll(self, session, job_id, offset):
        job = self.visible_job(session, job_id)
        if job is None or not offset.isdigit():
            return self.send_json({"error": "No such run."}, HTTPStatus.NOT_FOUND)
        self.send_json(job.view(int(offset)))

    # --- POST -------------------------------------------------------------------

    def _post(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            form = self.form()
        except (ValueError, UnicodeDecodeError) as error:
            return self.send_html(page("Not done", esc(error)), HTTPStatus.BAD_REQUEST)
        if path == "/login":
            return self.login(form)
        token, session = self.session()
        if session is None:
            return self.redirect("/login")
        if not Sessions.csrf_ok(session, form.get("csrf")):
            return self.send_html(page("Refused", "<p>The form expired; reload the page.</p>", session),
                                  HTTPStatus.FORBIDDEN)
        user = session["user"]
        try:
            if path == "/logout":
                self.app.sessions.logout(token)
                self.app.audit(user, "logout")
                if user not in self.app.sessions.active_users():
                    for meta in self.app.temporary_of(user):
                        threading.Thread(target=self.app.end_temporary,
                                         args=(user, meta["target_id"], "signed out"), daemon=True).start()
                return self.redirect("/login", [("Set-Cookie", self.cookie("", expire=True))])
            if path == "/plan":
                return self.post_plan(user, form)
            parts = path.split("/")
            if len(parts) == 4 and parts[1] == "servers" and parts[3] == "plan":
                return self.post_single_plan(user, parts[2], form)
            if len(parts) == 4 and parts[1] == "servers" and parts[3] == "end":
                self.app.target_entry(parts[2], user)  # the owner only
                if not self.app.end_temporary(user, parts[2], "ended by the user"):
                    raise ValueError("Access could not be removed yet (a run is still going, or the server "
                                     "did not answer). It stops working at its expiry time anyway.")
                return self.redirect("/")
            if path == "/servers/scan":
                return self.post_scan(session, form)
            if path == "/servers/enrol":
                return self.post_enrol(user, form)
            parts = path.split("/")
            if len(parts) == 4 and parts[1] == "plans" and PLAN_ID_RE.fullmatch(parts[2]):
                plan_id, action = parts[2], parts[3]
                if action == "approve":
                    self.app.approve(user, plan_id, self.typed(form))
                    return self.redirect(f"/plans/{plan_id}")
                if action == "apply":
                    return self.redirect(f"/jobs/{self.app.start_apply(user, plan_id).id}")
                if action in ("report", "resume"):
                    return self.redirect(f"/jobs/{self.app.start_remote(user, action, plan_id).id}")
            if len(parts) == 4 and parts[1] == "batches" and parts[2] in self.app.batches:
                if parts[3] == "approve":
                    self.app.approve_batch(user, parts[2], self.typed(form))
                elif parts[3] == "apply":
                    self.app.apply_batch(user, parts[2])
                return self.redirect(f"/batches/{parts[2]}")
            if len(parts) == 4 and parts[1] == "jobs" and parts[3] == "input":
                job = self.visible_job(session, parts[2])
                if job is None:
                    raise KeyError("No such run.")
                # Only the user who started the run types into it: its prompts
                # are sudo and confirmations (lab finding, items 2–3).
                if job.user != user:
                    raise ApprovalError(f"Only {job.user}, who started this run, can type into it.")
                job.send(form.get("line", ""))
                return self.send_json({"sent": True})
        except (run.RunError, ApprovalError, enrolment.EnrolError, ValueError, KeyError, OSError) as error:
            back = "/"
            if path.startswith("/servers/") and path.endswith("/plan") and TARGET_RE.fullmatch(path.split("/")[2]):
                back = f"/servers/{path.split('/')[2]}/apps"
            if isinstance(error, KeyError) and error.args:
                error = error.args[0]
            return self.fail(session, error, back)
        self.send_html(page("Not found", "", session), HTTPStatus.NOT_FOUND)

    def typed(self, form):
        typed = {k[len("name_"):]: v for k, v in form.items() if k.startswith("name_")}
        # Decision 3a (2026-11-25): in the web UI the ticked review box is the
        # confirmation; typed names stay for risky actions. approve_plan gets the
        # same confirmation as the command line, so every check is unchanged.
        typed["_confirm"] = CONFIRMATION if form.get("reviewed") == "1" else ""
        return typed

    def cookie(self, value, expire=False):
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.secure:
            parts.append("Secure")
        if expire:
            parts.append("Max-Age=0")
        return "; ".join(parts)

    def login(self, form):
        try:
            token = self.app.sessions.login(form.get("user", ""), form.get("password", ""))
        except AuthError as error:
            self.app.audit(form.get("user", "")[:32], "login_failed")
            return self.send_html(page("Log in", f'<p class="error">{esc(error)}</p>{LOGIN_FORM}'),
                                  HTTPStatus.UNAUTHORIZED)
        self.app.audit(form.get("user"), "login")
        self.redirect("/", [("Set-Cookie", self.cookie(token))])

    def post_plan(self, user, form):
        targets = self.fields.get("targets", [])
        components = self.fields.get("components", [])
        plan_ids = self.app.make_plans(user, targets, components, form.get("operation", "install"),
                                       form.get("all_or_nothing") == "1", form.get("allow_draft") == "1")
        if len(plan_ids) == 1:
            return self.redirect(f"/plans/{plan_ids[0]}")
        return self.redirect(f"/batches/{self.app.new_batch(user, plan_ids)}")

    def post_single_plan(self, user, target, form):
        # One or more applications (decision A) and exactly one action; a
        # repeated action is refused, not reduced to its last value.
        if len(self.fields.get("operation", [])) != 1:
            raise ValueError("Choose exactly one action.")
        components = list(dict.fromkeys(self.fields.get("component", [])))
        plan_id = self.app.make_app_plan(user, target, components, form["operation"],
                                         form.get("allow_draft") == "1")
        return self.redirect(f"/plans/{plan_id}")

    def post_scan(self, session, form):
        host, user = form.get("host", "").strip(), form.get("user", "").strip()
        port = int(form.get("port") or 22)
        name = form.get("name", "").strip()
        keys = enrolment.scan(host, port)
        pending = secrets.token_hex(8)
        with self.app.lock:
            self.app.pending[pending] = {"host": host, "port": port, "user": user, "name": name,
                                         "lab": form.get("lab") == "1", "keys": keys,
                                         "save": form.get("save") == "1",
                                         "by": session["user"], "at": time.time()}
        save = form.get("save") == "1"
        use = ("used once to install the master\'s key, then dropped" if save else
               "used once to install a key for this setup only, then dropped")
        choices = "".join(f'<label><input type="radio" name="fingerprint" value="{esc(k["fingerprint"])}" required> '
                          f'{esc(k["type"])} <code>{esc(k["fingerprint"])}</code></label>' for k in keys)
        body = (f'<p>{esc(host)}:{port} offers these host keys. Compare one with the server\'s own '
                f'(for example <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> on its console) '
                f'before you confirm it.</p><form method="post" action="/servers/enrol">{csrf(session)}'
                f'<input type="hidden" name="pending" value="{pending}">{choices}'
                f'<label>Password of <b>{esc(user)}</b> ({use}): '
                f'<input type="password" name="password" autocomplete="off" required></label>'
                f'<button>{"Confirm key and save server" if save else "Confirm key and connect for this setup"}</button></form>')
        self.send_html(page(f"Add {name}", body, session))

    def post_enrol(self, user, form):
        with self.app.lock:
            pending = self.app.pending.pop(form.get("pending", ""), None)
        if pending is None or pending["by"] != user or time.time() - pending["at"] > 600:
            raise ValueError("The host key check expired; start again.")
        if not pending.get("save"):
            meta = self.app.add_temporary(user, pending, form.get("password", ""), form.get("fingerprint", ""))
            return self.redirect(f"/servers/{meta['target_id']}/apps")
        entry = enrolment.enrol(pending["name"], pending["host"], pending["port"], pending["user"],
                                form.get("password", ""), form.get("fingerprint", ""), pending["keys"],
                                self.app.config.master_key, self.app.config.known_hosts,
                                self.app.config.inventory, pending["lab"])
        self.app.audit(user, "server_added", target=entry["target_id"], host=entry["host"],
                       fingerprint=form.get("fingerprint"))
        self.redirect(f"/servers/{entry['target_id']}")


LOGIN_FORM = ('<form method="post" action="/login"><label>User <input name="user" autocomplete="username" required></label>'
              '<label>Password <input type="password" name="password" autocomplete="current-password" required></label>'
              '<button>Log in</button></form>')
ADD_FORM = ('<form method="post" action="/servers/scan">{csrf}'
            '<label>Name <input name="name" pattern="[a-z0-9][a-z0-9_-]{{0,63}}" required></label>'
            '<label>Host <input name="host" required></label>'
            '<label>Port <input name="port" type="number" value="22" min="1" max="65535"></label>'
            '<label>SSH user <input name="user" required></label>'
            '<label><input type="checkbox" name="lab" value="1"> lab server</label>'
            '<label><input type="checkbox" name="save" value="1"> Save this server for future use</label>'
            '<p class="hint">Not saved: DSSA installs a key for this setup only. It stops working after '
            '60 minutes, and DSSA removes it after a successful run, when you sign out or end access. '
            'Nothing is added to Saved servers.</p>'
            '<button>Read its host key</button></form>')


def serve(app, host, port, certfile=None, keyfile=None):
    """Serve until interrupted. HTTPS unless bound to 127.0.0.1 without a certificate."""

    if not certfile and host != "127.0.0.1":
        raise SystemExit("Refusing plain HTTP on a non-loopback address; pass --cert and --key.")
    handler = type("BoundHandler", (Handler,), {"app": app, "secure": bool(certfile)})

    def sweep():
        # Item 2: sessions live in memory, so after a restart every temporary
        # server's owner is signed out and its access ends now.
        while True:
            try:
                app.sweep_temporary(app.sessions.active_users())
            except Exception:  # keep sweeping; details go to the terminal
                import traceback
                traceback.print_exc()
            time.sleep(60)

    threading.Thread(target=sweep, daemon=True, name="dssa-temp-sweeper").start()
    httpd = ThreadingHTTPServer((host, port), handler)
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile, keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print(f"DSSA web UI on {'https' if certfile else 'http'}://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
