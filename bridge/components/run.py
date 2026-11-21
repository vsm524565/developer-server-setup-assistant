"""Drive the Bash run entry point from Python (Milestone 4.34).

Python never opens SSH and never sees a password. It starts
``backend/linux/bin/dssa-run.sh``, lets it use the terminal (so the
remote ``sudo`` prompt reaches the operator directly), and:

- stores plans on the control machine (``<state>/plans/<plan_id>.json``),
- hides the result marker lines from the terminal while passing every
  other byte through unchanged,
- saves the returned files as the laptop copy of the run
  (``<state>/runs/<plan_id>/``, 0700 directory, 0600 files).
"""

import base64
import binascii
import json
import os
import subprocess
import sys
from pathlib import Path

from bridge.components.approval import default_state_dir
from bridge.components.plan import PLAN_ID_RE, validate_plan_document


ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = ROOT / "backend/linux/bin/dssa-run.sh"

RUN_FILES = frozenset({
    "summary.json", "resume.json", "run.log",
    "journal.jsonl", "result.json", "snapshot.json",
})
STATUSES = frozenset({"finished", "running", "interrupted", "none"})
MAX_FILE_BYTES = 32 * 1024 * 1024
MARKER = b"DSSA_"


class RunError(RuntimeError):
    """The backend could not plan or run."""


# --- state on the control machine -----------------------------------------

def _private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != os.getuid():
        raise RunError(f"Unsafe state directory: {path}")
    os.chmod(path, 0o700)
    return path


def plans_dir(state=None):
    return Path(state or default_state_dir()) / "plans"


def runs_dir(state=None):
    return Path(state or default_state_dir()) / "runs"


def save_plan(plan_bytes, state=None):
    """Store a new plan's exact bytes. Returns its path."""

    plan = validate_plan_document(json.loads(plan_bytes))
    directory = _private_dir(plans_dir(state))
    path = directory / f"{plan['plan_id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(plan_bytes)
    return path


def load_plan(plan_id, state=None):
    """Return (plan_bytes, plan, path) of a stored plan."""

    if not isinstance(plan_id, str) or not PLAN_ID_RE.fullmatch(plan_id):
        raise RunError("Invalid plan ID.")
    path = plans_dir(state) / f"{plan_id}.json"
    if not path.is_file() or path.is_symlink():
        raise RunError(f"No stored plan {plan_id}. Run the plan step first.")
    data = path.read_bytes()
    return data, validate_plan_document(json.loads(data)), path


def save_run_files(plan_id, files, state=None):
    """Write the returned files as the laptop copy. Returns the directory."""

    # Each level private: mkdir(parents=True) would leave runs/ at the umask.
    directory = _private_dir(_private_dir(runs_dir(state)) / plan_id)
    for name, data in files.items():
        if name not in RUN_FILES:
            continue
        temporary = directory / f".{name}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, directory / name)
    return directory


# --- marker filter ----------------------------------------------------------

class MarkerFilter:
    """Pass terminal output through, keeping DSSA_ marker lines back.

    Bytes are forwarded as they arrive (a ``sudo`` prompt has no newline),
    except at the start of a line that could be a marker: those bytes are
    held only until they can no longer be one.
    """

    def __init__(self, out):
        self.out = out
        self.files = {}
        self.status = None
        self.exit_code = None
        self._line_start = True
        self._held = bytearray()
        self._in_marker = False

    def feed(self, chunk):
        passthrough = bytearray()
        for value in chunk:
            byte = bytes((value,))
            if self._in_marker:
                if byte == b"\n":
                    self._marker(bytes(self._held))
                    self._held.clear()
                    self._in_marker = False
                    self._line_start = True
                else:
                    self._held += byte
                    if len(self._held) > MAX_FILE_BYTES * 2:
                        raise RunError("Result line too large.")
                continue
            if self._line_start:
                self._held += byte
                if MARKER.startswith(bytes(self._held)):
                    continue
                if bytes(self._held).startswith(MARKER):
                    self._in_marker = True
                    continue
                passthrough += self._held
                self._held.clear()
                self._line_start = byte == b"\n"
                continue
            passthrough += byte
            if byte == b"\n":
                self._line_start = True
        if passthrough:
            self.out.write(bytes(passthrough))
            self.out.flush()

    def close(self):
        if self._held and not self._in_marker:
            self.out.write(bytes(self._held))
            self.out.flush()
        elif self._in_marker:
            self._marker(bytes(self._held))
        self._held.clear()

    def _marker(self, line):
        text = line.rstrip(b"\r").decode("ascii", "replace")
        parts = text.split(" ")
        kind = parts[0]
        if kind == "DSSA_FILE" and len(parts) == 3 and parts[1] in RUN_FILES:
            try:
                data = base64.b64decode(parts[2], validate=True)
            except (binascii.Error, ValueError):
                raise RunError(f"Corrupt result file: {parts[1]}") from None
            if len(data) > MAX_FILE_BYTES:
                raise RunError(f"Result file too large: {parts[1]}")
            self.files[parts[1]] = data
        elif kind == "DSSA_STATUS" and len(parts) == 2 and parts[1] in STATUSES:
            self.status = parts[1]
        elif kind == "DSSA_EXIT" and len(parts) == 2 and parts[1].isdigit():
            self.exit_code = int(parts[1])
        else:
            # Not ours: show it unchanged.
            self.out.write(line + b"\n")
            self.out.flush()


# --- backend calls ------------------------------------------------------------

def _env(inventory=None):
    env = dict(os.environ)
    if inventory:
        env["DSSA_INVENTORY_FILE"] = str(inventory)
    return env


def list_targets(inventory=None):
    result = subprocess.run(
        ["bash", str(RUN_SCRIPT), "targets"], capture_output=True,
        timeout=30, check=False, env=_env(inventory), stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RunError(result.stderr.decode(errors="replace").strip() or "Inventory unreadable.")
    return json.loads(result.stdout)


def make_plan(target, components, allow_draft=False, inventory=None, operation="install",
              all_or_nothing=False):
    """Build a read-only plan on the target. Returns (plan_bytes, plan).
    operation: install (default), remove or adopt (4.42)."""

    if operation not in ("install", "remove", "adopt", "update"):
        raise RunError(f"Unknown operation: {operation}")
    argv = ["bash", str(RUN_SCRIPT), "plan"]
    if allow_draft:
        argv.append("--allow-draft")
    if operation != "install":
        argv.append(f"--{operation}")
    if all_or_nothing:
        argv.append("--all-or-nothing")
    argv += [target, *components]
    result = subprocess.run(
        argv, stdout=subprocess.PIPE, timeout=600, check=False,
        env=_env(inventory), stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RunError(f"Planning failed (exit {result.returncode}).")
    plan_bytes = result.stdout
    return plan_bytes, validate_plan_document(json.loads(plan_bytes))


def read_record(target, inventory=None):
    """The target's install record (4.41), read without sudo."""

    result = subprocess.run(
        ["bash", str(RUN_SCRIPT), "record", target], stdout=subprocess.PIPE,
        timeout=120, check=False, env=_env(inventory), stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RunError(f"Reading the install record failed (exit {result.returncode}).")
    record = json.loads(result.stdout)
    if not isinstance(record, dict) or record.get("schema_version") != "1.0" \
            or not isinstance(record.get("components"), dict):
        raise RunError("The target returned an invalid install record.")
    return record


def check_login(target, host_key, inventory=None):
    """Log in once with the pinned host key (no sudo). Returns the exit code;
    the backend's explanation goes to the terminal."""

    result = subprocess.run(
        ["bash", str(RUN_SCRIPT), "check", target, host_key],
        timeout=60, check=False, env=_env(inventory), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,  # stdout stays clean for main.py --json
    )
    return result.returncode


def _stream(argv, inventory=None, out=None):
    """Run a terminal-using backend command; return (rc, MarkerFilter)."""

    out = out or sys.stdout.buffer
    marker = MarkerFilter(out)
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, env=_env(inventory))
    try:
        while True:
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            marker.feed(chunk)
    finally:
        process.stdout.close()
        rc = process.wait()
        marker.close()
    return rc, marker


class RunOutcome:
    """What came back from apply, report or resume."""

    def __init__(self, rc, marker, directory):
        self.rc = rc
        self.status = marker.status
        self.exit_code = marker.exit_code
        self.directory = directory
        self.files = sorted(marker.files)
        summary = marker.files.get("summary.json") or marker.files.get("resume.json")
        self.summary = json.loads(summary) if summary else None


def _finish(rc, marker, plan_id, state):
    directory = save_run_files(plan_id, marker.files, state) if marker.files else None
    return RunOutcome(rc, marker, directory)


def apply_plan(target, plan_path, plan_sha256, plan_id, inventory=None, state=None, out=None):
    rc, marker = _stream(
        ["bash", str(RUN_SCRIPT), "apply", target, str(plan_path), plan_sha256],
        inventory, out,
    )
    return _finish(rc, marker, plan_id, state)


def report(target, plan_id, host_key, inventory=None, state=None, out=None):
    rc, marker = _stream(
        ["bash", str(RUN_SCRIPT), "report", target, plan_id, host_key], inventory, out,
    )
    return _finish(rc, marker, plan_id, state)


def resume_rollback(target, plan_id, host_key, inventory=None, state=None, out=None):
    rc, marker = _stream(
        ["bash", str(RUN_SCRIPT), "resume-rollback", target, plan_id, host_key],
        inventory, out,
    )
    return _finish(rc, marker, plan_id, state)
