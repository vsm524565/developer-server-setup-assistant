"""Terminal jobs for the web UI (Milestone 4.46).

An apply, report or resume-rollback needs ``sudo`` on the server, and
sudo asks on the server's own terminal. The web UI keeps that model: it
runs ``dssa-run.sh`` with a pseudo-terminal as its input (so ``ssh -t``
gives the server a terminal), shows the server's output in the browser,
and passes what the operator types (for example the sudo password)
straight to that terminal. Typed input is never stored, logged or
echoed by DSSA; sudo itself does not echo it.

Result marker lines are filtered and collected exactly as on the command
line (bridge.components.run.MarkerFilter), and the run's files are saved
the same way.
"""

import os
import secrets
import signal
import subprocess
import threading
import time

from bridge.components import run

MAX_OUTPUT = 2 * 1024 * 1024
MAX_INPUT = 1024


class JobStartError(OSError):
    """The job's process could not be started. The job is left finished
    (exit 127, the reason in its output), so it blocks nothing.

    launched: False when the process was never created, so nothing ran
    (confirmed). True when it was created and then had to be stopped because
    its output could not be followed: part of it may have run on the server,
    so the outcome is uncertain (Codex review, round 6).

    stopped: for a launched process, True only when DSSA saw it exit after
    stopping it; False when that could not be confirmed (round 7). None
    when nothing was launched."""

    def __init__(self, message, launched=False, stopped=None):
        super().__init__(message)
        self.launched = launched
        self.stopped = stopped if launched else None


class _Buffer:
    """Byte sink for MarkerFilter: keeps the visible terminal text."""

    def __init__(self, job):
        self.job = job

    def write(self, chunk):
        with self.job.lock:
            self.job.output += bytes(chunk)
            if len(self.job.output) > MAX_OUTPUT:
                self.job.output = self.job.output[-MAX_OUTPUT:]
                self.job.trimmed = True
        return len(chunk)

    def flush(self):
        pass


class Job:
    def __init__(self, kind, argv, env, plan_id, target, user, on_finish=None):
        self.id = secrets.token_hex(8)
        self.kind, self.argv, self.env = kind, argv, env
        self.plan_id, self.target, self.user = plan_id, target, user
        self.on_finish = on_finish
        self.lock = threading.Lock()
        self.output = b""
        self.trimmed = False
        self.started = time.time()
        self.finished = None
        self.rc = None
        self.outcome = None
        self._master = None
        self._process = None

    # --- lifecycle ---------------------------------------------------------

    def start(self):
        master = slave = None
        try:
            master, slave = os.openpty()
            self._master = master
            self._process = subprocess.Popen(
                self.argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=self.env, start_new_session=True, close_fds=True,
            )
            os.close(slave)
            slave = None
            threading.Thread(target=self._read, name=f"job-{self.id}", daemon=True).start()
        except Exception as error:
            launched = self._process is not None
            stopped = None
            try:
                stopped = self._teardown(master, slave)
            except Exception:  # round 7: a teardown failure never hides the start failure
                stopped = False if launched else None
            self._master = None
            if not launched:
                reason = f"the run could not start: {error}"
            elif stopped:
                reason = f"the run was started but could not be followed, so DSSA stopped it: {error}"
            else:
                reason = (f"the run was started but could not be followed; DSSA tried to stop it "
                          f"but could not confirm that it stopped: {error}")
            # Whatever the teardown did, the job ends here: a job left
            # "running" would block cleanup of its server.
            try:
                self.fail(reason)
            except Exception:
                self.rc, self.finished = 127, self.finished or time.time()
            raise JobStartError(str(error), launched=launched, stopped=stopped) from error
        return self

    def _teardown(self, master, slave):
        """Best effort: stop a half-started process group, close descriptors.
        Each step runs even if an earlier one fails. Returns None when no
        process was launched, else whether it was seen to exit."""

        process = self._process
        stopped = None
        if process is not None:
            stopped = False
            steps = (lambda: os.killpg(process.pid, signal.SIGKILL), lambda: process.kill())
            for step in steps:
                try:
                    step()
                except Exception:
                    pass
            try:
                process.wait(timeout=10)
                stopped = process.returncode is not None
            except Exception:
                stopped = False
            try:
                process.stdout.close()
            except Exception:
                pass
        for fd in (master, slave):
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
        return stopped

    def fail(self, reason):
        """Leave the job finished without a process (exit 127)."""

        with self.lock:
            self.rc = 127
            self.finished = time.time()
            self.output += f"[DSSA web UI: {reason}]\n".encode(errors="replace")

    def _read(self):
        marker = run.MarkerFilter(_Buffer(self))
        try:
            while True:
                chunk = os.read(self._process.stdout.fileno(), 65536)
                if not chunk:
                    break
                marker.feed(chunk)
        finally:
            self._process.stdout.close()
            rc = self._process.wait()
            marker.close()
            try:
                outcome = run._finish(rc, marker, self.plan_id, None)
            except Exception as error:  # a broken result must not hide the run's end
                outcome = None
                _Buffer(self).write(f"\n[DSSA web UI: could not save the run files: {error}]\n".encode())
            try:
                os.close(self._master)
            except OSError:
                pass
            with self.lock:
                self.rc = rc
                self.outcome = outcome
                self.finished = time.time()
            if self.on_finish:
                self.on_finish(self)

    # --- operator interaction -----------------------------------------------

    def send(self, text):
        """Pass one line the operator typed to the server's terminal."""

        if self.finished is not None or self._master is None:
            raise ValueError("The run has finished.")
        data = text.encode()
        if len(data) > MAX_INPUT or b"\n" in data or b"\r" in data:
            raise ValueError("One line of at most 1024 bytes.")
        os.write(self._master, data + b"\r")

    @property
    def code(self):
        if self.outcome is not None and self.outcome.exit_code is not None:
            return self.outcome.exit_code
        return self.rc

    def view(self, offset=0):
        with self.lock:
            text = self.output[offset:].decode(errors="replace")
            return {"id": self.id, "kind": self.kind, "plan_id": self.plan_id,
                    "target": self.target, "user": self.user,
                    "running": self.finished is None, "exit_code": self.code,
                    "offset": len(self.output), "text": text,
                    "summary": self.outcome.summary if self.outcome else None}


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}

    def start(self, job):
        with self._lock:
            self._jobs[job.id] = job
        return job.start()

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit=20):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)[:limit]
