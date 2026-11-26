"""Approval records for multi-component plans (Milestone 4.32).

plan -> approve -> apply. The approve step writes a single-use record
bound to the SHA-256 of the exact plan file bytes, valid for 30 minutes
from the plan's creation. Apply (4.34) must call ``consume_approval``,
which re-checks every gate and claims the record exactly once.

Records live on the control machine DSSA runs from, never on targets:
``~/.local/state/developer-server-setup-assistant/approvals/<plan_id>/``.

An approval records operator intent and plan integrity. It is not
cryptographic authorization against someone who already controls the
operator's account; that relies on file permissions and the audit log.
"""

import getpass
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.components.plan import (
    HOST_KEY_RE,
    PLAN_ID_RE,
    PlanError,
    changed_definitions,
    validate_plan_document,
)


ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = ROOT / "backend/linux/logging/audit.sh"

APPROVAL_WINDOW = timedelta(minutes=30)
CLOCK_SKEW = timedelta(minutes=5)
CONFIRMATION = "APPROVE"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MAX_FILE_BYTES = 1024 * 1024

RECORD_KEYS = frozenset({
    "schema_version", "approval_id", "plan_id", "plan_sha256", "target_id",
    "host_key_sha256", "approved_components", "approved_by", "approved_at",
    "expires_at", "single_use",
})
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPERATOR_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ApprovalError(ValueError):
    """An approval could not be created, validated or consumed."""

    def __init__(self, message, issues=()):
        super().__init__(message)
        self.issues = list(issues) or [message]


# --- helpers --------------------------------------------------------------

def default_state_dir():
    """Per-user state directory on the machine running DSSA."""

    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "developer-server-setup-assistant"


def _approvals_root(root):
    return Path(root) if root is not None else default_state_dir() / "approvals"


def _now(now):
    return now if now is not None else datetime.now(timezone.utc)


def _format(moment):
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def _parse_time(value):
    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)


def plan_digest(plan_bytes):
    return "sha256:" + hashlib.sha256(plan_bytes).hexdigest()


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ApprovalError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(data, what):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError(f"{what} is not valid JSON: {error}") from None


def known_host_fingerprints(host, known_hosts=None):
    """SHA256 fingerprints recorded for ``host`` in known_hosts."""

    known_hosts = Path(known_hosts or Path.home() / ".ssh/known_hosts")
    if not known_hosts.is_file():
        return set()

    found = subprocess.run(
        ["ssh-keygen", "-F", host, "-f", str(known_hosts)],
        text=True, capture_output=True, timeout=15, check=False,
        stdin=subprocess.DEVNULL,
    )
    fingerprints = set()
    for line in found.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        key_line = f"{parts[1]} {parts[2]}\n"
        listed = subprocess.run(
            ["ssh-keygen", "-l", "-f", "-"],
            input=key_line, text=True, capture_output=True,
            timeout=15, check=False,
        )
        fields = listed.stdout.split()
        if listed.returncode == 0 and len(fields) >= 2:
            fingerprints.add(fields[1])
    return fingerprints


def _audit(event, plan, status, details):
    """Append an approval event to the JSONL audit log."""

    record = {
        "schema_version": "1.0",
        "request_id": plan["plan_id"],
        "source": "python-bridge",
        "event": event,
        "actor": details.get("approved_by", "unknown"),
        "target_id": plan["target"]["target_id"],
        "operation": "approval",
        "status": status,
        "details": details,
    }
    result = subprocess.run(
        [str(AUDIT_SCRIPT)],
        input=json.dumps(record), text=True, capture_output=True,
        timeout=15, check=False,
    )
    if result.returncode != 0:
        raise ApprovalError("Audit recording failed; approval state unchanged.")


def _private_dir(path):
    """Create or verify a 0700 directory owned by the current user."""

    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ApprovalError(f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise ApprovalError(f"{path} is not owned by the current user")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)


def _write_new(path, data):
    """Write a new 0600 file; never replace an existing one."""

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _read_private(path, what):
    """Read a regular, private file owned by the current user."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ApprovalError(f"{what} is missing") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ApprovalError(f"{what} must be a regular file")
    if info.st_uid != os.getuid():
        raise ApprovalError(f"{what} is not owned by the current user")
    if info.st_mode & 0o077:
        raise ApprovalError(f"{what} permissions are too open")
    if info.st_size > MAX_FILE_BYTES:
        raise ApprovalError(f"{what} is too large")
    return path.read_bytes()


# --- preflight ------------------------------------------------------------

def preflight_plan(plan_bytes, current_host_keys, now=None, definitions_dir=None):
    """Check a plan before approval. Returns a result dict; never raises."""

    now = _now(now)
    issues = []
    plan = None

    try:
        plan = validate_plan_document(_load_json(plan_bytes, "plan"))
    except (ApprovalError, PlanError) as error:
        issues.append(f"Plan is invalid: {error}")

    if plan is not None:
        created = _parse_time(plan["created_at"])
        if created - now > CLOCK_SKEW:
            issues.append("Plan creation time is in the future.")
        if now - created > APPROVAL_WINDOW:
            issues.append("Plan is older than 30 minutes; re-plan before approving.")

        changed = changed_definitions(plan, definitions_dir)
        if changed:
            issues.append(f"Definitions changed since planning: {', '.join(changed)}")

        if plan["target"]["host_key_sha256"] not in set(current_host_keys):
            issues.append("Target host key no longer matches known_hosts.")

        if not plan["runnable"]:
            issues.append("Nothing in the plan is runnable.")
        # 4.44: all or nothing cannot start with part of the plan held.
        if plan.get("all_or_nothing") and plan["held"]:
            issues.append("All-or-nothing plan has held components ("
                          + ", ".join(h["component"] for h in plan["held"])
                          + "); plan again without them.")

    # Anything that changes the server needs approval; only SKIP (verify)
    # does not. REMOVE and ADOPT (4.42) change it or what DSSA may remove.
    requires_approval = bool(plan) and any(
        step["action"] != "SKIP" for step in plan["steps"]
        if step["component"] in plan["runnable"]
    )

    return {
        "passed": not issues,
        "issues": issues,
        "requires_approval": requires_approval,
        "verification_only": bool(plan) and not issues and not requires_approval,
        "plan": plan,
    }


# --- approve --------------------------------------------------------------

def approve_plan(plan_bytes, current_host_keys, confirmation, operator=None,
                 now=None, root=None, definitions_dir=None):
    """Write a single-use approval record for one plan. Returns the record."""

    now = _now(now)
    result = preflight_plan(plan_bytes, current_host_keys, now, definitions_dir)
    if not result["passed"]:
        raise ApprovalError("Preflight failed; no approval written.", result["issues"])
    if result["verification_only"]:
        raise ApprovalError("Verification-only plan: no approval is needed.")
    if confirmation != CONFIRMATION:
        raise ApprovalError("Approval not confirmed; no approval written.")

    plan = result["plan"]
    operator = operator or getpass.getuser()
    if not OPERATOR_RE.fullmatch(operator):
        raise ApprovalError("Invalid operator name.")

    record = {
        "schema_version": "1.0",
        "approval_id": str(uuid.uuid4()),
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_digest(plan_bytes),
        "target_id": plan["target"]["target_id"],
        "host_key_sha256": plan["target"]["host_key_sha256"],
        "approved_components": list(plan["runnable"]),
        "approved_by": operator,
        "approved_at": _format(now),
        "expires_at": _format(_parse_time(plan["created_at"]) + APPROVAL_WINDOW),
        "single_use": True,
    }

    approvals = _approvals_root(root)
    _private_dir(approvals)
    directory = approvals / plan["plan_id"]
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        raise ApprovalError("This plan already has an approval record.") from None

    try:
        _write_new(directory / "plan.json", plan_bytes)
        _write_new(
            directory / "approval.json",
            (json.dumps(record, indent=2) + "\n").encode("utf-8"),
        )
        _audit("approval.granted", plan, "approved", {
            "approval_id": record["approval_id"],
            "approved_by": operator,
            "approved_components": record["approved_components"],
            "plan_sha256": record["plan_sha256"],
            "expires_at": record["expires_at"],
        })
    except BaseException:
        # An approval that was not audited must not remain usable.
        for name in ("approval.json", "plan.json"):
            try:
                (directory / name).unlink()
            except FileNotFoundError:
                pass
        directory.rmdir()
        raise
    return record


# --- check and consume ----------------------------------------------------

def _validate_record(record):
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ApprovalError("Approval record fields are invalid.")
    if record["schema_version"] != "1.0" or record["single_use"] is not True:
        raise ApprovalError("Approval record version or use is invalid.")
    checks = (
        ("approval_id", lambda v: isinstance(v, str) and PLAN_ID_RE.fullmatch(v)),
        ("plan_id", lambda v: isinstance(v, str) and PLAN_ID_RE.fullmatch(v)),
        ("plan_sha256", lambda v: isinstance(v, str) and DIGEST_RE.fullmatch(v)),
        ("host_key_sha256", lambda v: isinstance(v, str) and HOST_KEY_RE.fullmatch(v)),
        ("approved_by", lambda v: isinstance(v, str) and OPERATOR_RE.fullmatch(v)),
        ("approved_components", lambda v: isinstance(v, list) and v
            and all(isinstance(c, str) for c in v)),
    )
    for key, ok in checks:
        if not ok(record[key]):
            raise ApprovalError(f"Approval record field {key} is invalid.")
    for key in ("approved_at", "expires_at"):
        try:
            _parse_time(record[key])
        except (TypeError, ValueError):
            raise ApprovalError(f"Approval record field {key} is invalid.") from None


def check_approval(plan_id, current_host_keys, now=None, root=None,
                   definitions_dir=None):
    """Evaluate every apply gate. Returns a result dict; never raises."""

    now = _now(now)
    checks = dict.fromkeys(
        ("record", "binding", "freshness", "unused", "scope",
         "definitions", "identity"),
        False,
    )
    issues = []
    plan = record = None

    if not isinstance(plan_id, str) or not PLAN_ID_RE.fullmatch(plan_id):
        return {"ready": False, "status": "BLOCKED", "checks": checks,
                "issues": ["Invalid plan ID."], "plan": None}

    directory = _approvals_root(root) / plan_id

    # Gate: record files exist, are private and parse.
    try:
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ApprovalError("Approval directory must be a real directory")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ApprovalError("Approval directory ownership or permissions are unsafe")
        plan_bytes = _read_private(directory / "plan.json", "plan.json")
        record = _load_json(_read_private(directory / "approval.json", "approval.json"),
                            "approval.json")
        _validate_record(record)
        plan = validate_plan_document(_load_json(plan_bytes, "plan.json"))
        checks["record"] = True
    except FileNotFoundError:
        issues.append("No approval exists for this plan.")
    except (ApprovalError, PlanError) as error:
        issues.append(str(error))

    if checks["record"]:
        # Gate: the record binds to these exact plan bytes and target.
        checks["binding"] = (
            hmac.compare_digest(plan_digest(plan_bytes), record["plan_sha256"])
            and record["plan_id"] == plan["plan_id"] == plan_id
            and record["target_id"] == plan["target"]["target_id"]
            and record["host_key_sha256"] == plan["target"]["host_key_sha256"]
        )
        if not checks["binding"]:
            issues.append("Approval does not match the stored plan.")

        # Gate: still within the window (recomputed from the plan itself).
        expires = _parse_time(plan["created_at"]) + APPROVAL_WINDOW
        checks["freshness"] = (
            now < expires and now < _parse_time(record["expires_at"])
        )
        if not checks["freshness"]:
            issues.append("Approval has expired; re-plan and approve again.")

        # Gate: single use.
        checks["unused"] = not os.path.lexists(directory / "consumed")
        if not checks["unused"]:
            issues.append("Approval has already been used.")

        # Gate: exactly the runnable components were approved.
        checks["scope"] = record["approved_components"] == plan["runnable"]
        if not checks["scope"]:
            issues.append("Approved components differ from the plan's runnable list.")

        # Gate: definitions unchanged on the control machine.
        changed = changed_definitions(plan, definitions_dir)
        checks["definitions"] = not changed
        if changed:
            issues.append(f"Definitions changed since approval: {', '.join(changed)}")

        # Gate: the target's identity is unchanged.
        checks["identity"] = plan["target"]["host_key_sha256"] in set(current_host_keys)
        if not checks["identity"]:
            issues.append("Target host key no longer matches known_hosts.")

    ready = all(checks.values())
    return {
        "ready": ready,
        "status": "READY" if ready else "BLOCKED",
        "checks": checks,
        "issues": issues,
        "plan": plan if ready else None,
    }


def consume_approval(plan_id, current_host_keys, now=None, root=None,
                     definitions_dir=None, expected_operator=None):
    """Claim an approval exactly once. Returns the approved plan.

    expected_operator: when given (the web UI, decision 3b), only an approval
    made by that operator is claimed. It is checked here, right before the
    marker, so an approval written after the caller's own check cannot be
    claimed by someone else (Codex review, 2026-11-26). Records are written
    once (exclusive mkdir and creates) and never rewritten."""

    now = _now(now)
    result = check_approval(plan_id, current_host_keys, now, root, definitions_dir)
    if not result["ready"]:
        raise ApprovalError("Approval is not valid for apply.", result["issues"])

    directory = _approvals_root(root) / plan_id
    if expected_operator is not None:
        record = _load_json(_read_private(directory / "approval.json", "approval.json"), "approval.json")
        by = record.get("approved_by")
        if by != expected_operator:
            raise ApprovalError(f"Apply refused: {by} approved this plan, so only {by} can apply it. "
                                "The approval was not used.")
    try:
        _write_new(directory / "consumed", (_format(now) + "\n").encode("utf-8"))
    except FileExistsError:
        raise ApprovalError("Approval has already been used.") from None

    # The marker stays even if auditing fails: a claimed approval must
    # never become usable again. The caller does not apply on error.
    record = json.loads((directory / "approval.json").read_text())
    _audit("approval.consumed", result["plan"], "consumed", {
        "approval_id": record["approval_id"],
        "approved_by": record["approved_by"],
        "approved_components": record["approved_components"],
    })
    return result["plan"]
