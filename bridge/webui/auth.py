"""Web UI login: users, sessions, CSRF tokens (Milestone 4.46).

- Users live in ``users.json`` in the web UI's state folder (mode 0600),
  each with an scrypt hash. They are added on the master's command line
  (``python -m bridge.webui adduser NAME``), never through the web.
- A session is a random token in an HttpOnly, SameSite=Strict cookie
  (Secure when served over HTTPS). Sessions live in memory only, expire
  after 8 hours, or 30 minutes idle.
- Every form carries the session's CSRF token; a POST without it is refused.
- Five failed logins for a user name lock it for 5 minutes.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
SESSION_LIFETIME = 8 * 3600
SESSION_IDLE = 30 * 60
MAX_FAILURES = 5
LOCKOUT = 5 * 60
MIN_PASSWORD = 12


class AuthError(Exception):
    pass


def _private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return {"salt": salt.hex(), "hash": digest.hex()}


def check_password(password, stored):
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(stored["salt"]), **SCRYPT)
    return hmac.compare_digest(digest.hex(), stored["hash"])


class Users:
    """users.json: {"schema_version": "1.0", "users": {name: {salt, hash}}}."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {"schema_version": "1.0", "users": {}}
        if self.path.is_symlink() or (self.path.stat().st_mode & 0o077):
            raise AuthError(f"{self.path} must be a private file (mode 0600, not a symlink).")
        return json.loads(self.path.read_text())

    def add(self, name, password):
        if not USER_RE.fullmatch(name):
            raise AuthError("User names are lower case letters, digits, _ and -.")
        if len(password) < MIN_PASSWORD:
            raise AuthError(f"Passwords need at least {MIN_PASSWORD} characters.")
        data = self._load()
        data["users"][name] = hash_password(password)
        _private_write(self.path, data)

    def remove(self, name):
        data = self._load()
        data["users"].pop(name, None)
        _private_write(self.path, data)

    def verify(self, name, password):
        stored = self._load()["users"].get(name)
        if stored is None:
            # Same work as a real check, so timing does not reveal names.
            check_password(password, hash_password("x" * MIN_PASSWORD))
            return False
        return check_password(password, stored)

    def names(self):
        return sorted(self._load()["users"])


class Sessions:
    def __init__(self, users, clock=time.monotonic):
        self.users = users
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions = {}
        self._failures = {}

    def login(self, name, password):
        now = self.clock()
        with self._lock:
            count, since = self._failures.get(name, (0, now))
            if count >= MAX_FAILURES and now - since < LOCKOUT:
                raise AuthError("Too many failed logins; wait a few minutes.")
        if not self.users.verify(name, password):
            with self._lock:
                count, since = self._failures.get(name, (0, now))
                if now - since >= LOCKOUT:
                    count, since = 0, now
                self._failures[name] = (count + 1, since)
            raise AuthError("Wrong user name or password.")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._failures.pop(name, None)
            self._sessions[token] = {"user": name, "created": now, "seen": now,
                                     "csrf": secrets.token_urlsafe(32)}
        return token

    def get(self, token):
        if not token:
            return None
        now = self.clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if now - session["created"] > SESSION_LIFETIME or now - session["seen"] > SESSION_IDLE:
                del self._sessions[token]
                return None
            session["seen"] = now
            return dict(session)

    def logout(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def active_users(self):
        """Users with at least one live session (expired ones are dropped)."""

        now = self.clock()
        with self._lock:
            for token, session in list(self._sessions.items()):
                if now - session["created"] > SESSION_LIFETIME or now - session["seen"] > SESSION_IDLE:
                    del self._sessions[token]
            return {s["user"] for s in self._sessions.values()}

    @staticmethod
    def csrf_ok(session, value):
        return bool(session and value) and hmac.compare_digest(session["csrf"], value)
