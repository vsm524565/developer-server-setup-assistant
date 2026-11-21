"""Adding a server from the web UI (Milestone 4.46).

The operator enters the server's host, port, SSH user and that user's
password once:

1. ``scan``: the master reads the host keys the server offers
   (ssh-keyscan) and shows their fingerprints. Nothing is trusted yet.
2. The operator compares a fingerprint with the server's own (for example
   from the hosting panel or the console) and confirms it.
3. ``enrol``: the confirmed key goes into the master's known_hosts; the
   password is used once, through SSH_ASKPASS, to add the master's public
   key to the user's ``~/.ssh/authorized_keys``; key login is checked;
   the server is added to the web UI's inventory. The password is then
   dropped: it is never written to disk, logged or kept.

From then on the master logs in with its own key only, exactly like the
command line, and sudo still asks on the server's terminal at apply time.
"""

import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+( [A-Za-z0-9@._-]+)?$")
FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
KEYSCAN_TYPES = "ed25519,ecdsa,rsa"


class EnrolError(Exception):
    pass


def _check(host, port, user=None):
    if not HOST_RE.fullmatch(host or ""):
        raise EnrolError("Invalid host.")
    if not (isinstance(port, int) and 1 <= port <= 65535):
        raise EnrolError("Invalid port.")
    if user is not None and not USER_RE.fullmatch(user):
        raise EnrolError("Invalid SSH user.")


def _host_spec(host, port):
    return host if port == 22 else f"[{host}]:{port}"


def scan(host, port, runner=subprocess.run):
    """The host keys the server offers: [{"line", "type", "fingerprint"}]."""

    _check(host, port)
    result = runner(["ssh-keyscan", "-T", "10", "-p", str(port), "-t", KEYSCAN_TYPES, host],
                    capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL)
    keys = []
    for line in result.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3:
            continue
        printed = runner(["ssh-keygen", "-l", "-f", "-"], input=f"{fields[1]} {fields[2]}\n",
                         capture_output=True, text=True, timeout=10, check=False)
        parts = printed.stdout.split()
        if printed.returncode == 0 and len(parts) >= 2 and FINGERPRINT_RE.fullmatch(parts[1]):
            keys.append({"line": f"{_host_spec(host, port)} {fields[1]} {fields[2]}",
                         "type": fields[1], "fingerprint": parts[1]})
    if not keys:
        raise EnrolError(f"No host key could be read from {host}:{port}.")
    return keys


def _add_known_host(known_hosts, line):
    known = Path(known_hosts)
    known.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = known.read_text().splitlines() if known.exists() else []
    if line not in existing:
        with open(known, "a") as handle:
            handle.write(line + "\n")
        os.chmod(known, 0o600)


def _askpass_script(directory):
    script = Path(directory) / "askpass"
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$DSSA_ASKPASS_SECRET"\n')
    script.chmod(stat.S_IRWXU)
    return script


def enrol(target_id, host, port, user, password, fingerprint, keys, master_key, known_hosts,
          inventory, lab=False, runner=subprocess.run):
    """Trust the confirmed host key, install the master key, check key login,
    and add the server to the inventory. Returns the inventory entry."""

    _check(host, port, user)
    if not TARGET_RE.fullmatch(target_id or ""):
        raise EnrolError("Invalid server name (lower case letters, digits, _ and -).")
    chosen = [k for k in keys if k["fingerprint"] == fingerprint]
    if len(chosen) != 1:
        raise EnrolError("Confirm exactly one of the fingerprints the server offered.")
    master_key = Path(master_key)
    public = Path(f"{master_key}.pub").read_text().strip()
    if not PUBKEY_RE.fullmatch(public):
        raise EnrolError("The master's public key is not a plain ed25519 key.")
    entries = _load_inventory(inventory)
    if any(s["target_id"] == target_id for s in entries["servers"]):
        raise EnrolError(f"A server named {target_id} already exists.")

    _add_known_host(known_hosts, chosen[0]["line"])
    common = ["-F", "/dev/null", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
              "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-p", str(port), "-l", user]
    install = ("umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF '{public}' ~/.ssh/authorized_keys || printf '%s\\n' '{public}' >> ~/.ssh/authorized_keys)")
    with tempfile.TemporaryDirectory(prefix="dssa-enrol-") as private:
        os.chmod(private, 0o700)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", private),
               "SSH_ASKPASS": str(_askpass_script(private)), "SSH_ASKPASS_REQUIRE": "force",
               "DISPLAY": "dssa:0", "DSSA_ASKPASS_SECRET": password}
        result = runner(["ssh", *common, "-o", "PubkeyAuthentication=no",
                         "-o", "PreferredAuthentications=password,keyboard-interactive",
                         "-o", "NumberOfPasswordPrompts=1", "--", host, install],
                        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        timeout=60, check=False)
        del env["DSSA_ASKPASS_SECRET"]
    password = None  # noqa: F841  (dropped; never stored)
    if result.returncode != 0:
        raise EnrolError("Logging in with the password failed: "
                         + (result.stderr.strip().splitlines() or ["no detail"])[-1])

    check = runner(["ssh", *common, "-i", str(master_key), "-o", "BatchMode=yes",
                    "-o", "IdentitiesOnly=yes", "--", host, "true"],
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    if check.returncode != 0:
        raise EnrolError("The master's key was installed, but key login does not work yet.")

    entry = {"target_id": target_id, "host": host, "ssh_user": user, "ssh_port": port,
             "ssh_key": str(master_key), "platform": "linux", "enabled": True, "lab": bool(lab)}
    entries["servers"].append(entry)
    _save_inventory(inventory, entries)
    return entry


def _load_inventory(path):
    path = Path(path)
    if not path.exists():
        return {"schema_version": "1.0", "servers": []}
    return json.loads(path.read_text())


def _save_inventory(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)
