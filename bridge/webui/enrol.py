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

Without "Save server" (requirements item 2, decided 2026-11-25), the same
password step installs a new key made for this setup only instead
(``temporary_access``). Its authorized_keys line carries ``expiry-time``
(computed on the server, in its own time zone) and ``from=`` the
master's address as the server sees it, so OpenSSH refuses it after the
window even if removal fails. Nothing goes into the inventory or the
master's known_hosts: the entry, the trusted host key and the private key
live in one private folder, removed with the line (``end_temporary_access``).
"""

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+( [A-Za-z0-9@._-]+)?$")
FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
KEYSCAN_TYPES = "ed25519,ecdsa,rsa"
TAG_RE = re.compile(r"^dssa-temp-[0-9a-f]{16}$")


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


# --- temporary access (requirements item 2) -----------------------------------------

def _ssh_common(known_hosts, port, user):
    return ["-F", "/dev/null", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-p", str(port), "-l", user]


def _with_password(runner, common, host, command, password):
    with tempfile.TemporaryDirectory(prefix="dssa-enrol-") as private:
        os.chmod(private, 0o700)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", private),
               "SSH_ASKPASS": str(_askpass_script(private)), "SSH_ASKPASS_REQUIRE": "force",
               "DISPLAY": "dssa:0", "DSSA_ASKPASS_SECRET": password}
        result = runner(["ssh", *common, "-o", "PubkeyAuthentication=no",
                         "-o", "PreferredAuthentications=password,keyboard-interactive",
                         "-o", "NumberOfPasswordPrompts=1", "--", host, command],
                        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        timeout=60, check=False)
        del env["DSSA_ASKPASS_SECRET"]
    return result


def _with_key(runner, entry, directory, command, timeout=30):
    common = _ssh_common(Path(directory) / "known_hosts", entry["ssh_port"], entry["ssh_user"])
    return runner(["ssh", *common, "-i", entry["ssh_key"], "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                   "--", entry["host"], command],
                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)


def temporary_access(target_id, host, port, user, password, fingerprint, keys, directory, owner,
                     lab=False, window=3600, runner=subprocess.run, now=time.time):
    """Install a key made for this setup only. Returns the meta record
    (also written to ``directory/meta.json``). ``directory`` must not exist."""

    _check(host, port, user)
    if not TARGET_RE.fullmatch(target_id or ""):
        raise EnrolError("Invalid server name (lower case letters, digits, _ and -).")
    chosen = [k for k in keys if k["fingerprint"] == fingerprint]
    if len(chosen) != 1:
        raise EnrolError("Confirm exactly one of the fingerprints the server offered.")
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.mkdir(mode=0o700)
    try:
        tag = f"dssa-temp-{secrets.token_hex(8)}"
        key = directory / "key"
        made = runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", tag, "-f", str(key)],
                      stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
        public = Path(f"{key}.pub").read_text().strip() if made.returncode == 0 else ""
        if not PUBKEY_RE.fullmatch(public) or not public.endswith(" " + tag):
            raise EnrolError("Could not make the temporary key.")
        known = directory / "known_hosts"
        _add_known_host(known, chosen[0]["line"])
        expires = int(now()) + int(window)
        # The server formats the expiry in its own time zone and reads the
        # master's address from SSH_CLIENT; refuse if either is missing.
        install = (
            'ip=${SSH_CLIENT%% *}; '
            'case "$ip" in ""|*[!0-9a-fA-F.:]*) echo "no client address" >&2; exit 3;; esac; '
            f'when=$(date -d @{expires} +%Y%m%d%H%M) || exit 3; '
            'umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && '
            'printf \'%s\\n\' "expiry-time=\\"$when\\",from=\\"$ip\\",no-port-forwarding,'
            f'no-agent-forwarding,no-X11-forwarding {public}" >> ~/.ssh/authorized_keys'
        )
        result = _with_password(runner, _ssh_common(known, port, user), host, install, password)
        if result.returncode != 0:
            raise EnrolError("Logging in with the password failed: "
                             + (result.stderr.strip().splitlines() or ["no detail"])[-1])
        entry = {"target_id": target_id, "host": host, "ssh_user": user, "ssh_port": port,
                 "ssh_key": str(key), "platform": "linux", "enabled": True, "lab": bool(lab)}
        meta = {"target_id": target_id, "owner": owner, "tag": tag, "created": int(now()),
                "expires": expires, "host": host, "entry": entry}
        _save_inventory(directory / "inventory.json", {"schema_version": "1.0", "servers": [entry]})
        if _with_key(runner, entry, directory, "true").returncode != 0:
            # The new key can't log in, so it can't remove itself: use the
            # password session once more, then drop the password.
            _with_password(runner, _ssh_common(known, port, user), host, _remove_command(tag), password)
            raise EnrolError("The temporary key was installed, but key login does not work; it was removed again.")
        _save_inventory(directory / "meta.json", meta)
        return meta
    except BaseException:
        if directory.exists() and not (directory / "meta.json").exists():
            shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        password = None  # noqa: F841  (dropped; never stored)


def _remove_command(tag):
    return ('f=~/.ssh/authorized_keys; [ -f "$f" ] || exit 0; '
            f'grep -v -F " {tag}" "$f" > "$f.dssa-tmp"; cat "$f.dssa-tmp" > "$f" && rm -f "$f.dssa-tmp"; '
            f'! grep -q -F " {tag}" "$f"')


def end_temporary_access(directory, runner=subprocess.run):
    """Remove exactly the line DSSA added, then the private folder.
    Returns True when the line is gone; on False the folder is kept for a
    retry (the line stops working at its expiry-time anyway)."""

    directory = Path(directory)
    meta = json.loads((directory / "meta.json").read_text())
    tag = meta["tag"]
    if not TAG_RE.fullmatch(tag):
        raise EnrolError("Invalid temporary access record.")
    try:
        result = _with_key(runner, meta["entry"], directory, _remove_command(tag))
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def discard_temporary_access(directory):
    """Delete the private folder without logging in (after the line expired)."""

    shutil.rmtree(Path(directory), ignore_errors=True)
