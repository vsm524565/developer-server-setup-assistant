#!/usr/bin/env python3
"""Mock ssh for the unified apply tests (4.34).

Runs the remote command locally with ``bash -c`` in a fake remote home,
as a real server's login shell would. It enforces host-key pinning the
way OpenSSH does: if UserKnownHostsFile is given and none of its lines
holds the key the "server" presents ($MOCK_HOST_PUB), it fails with 255
before running anything.

  MOCK_HOST_PUB    "type base64" of the key the server presents
  MOCK_HOST_FP     its SHA256 fingerprint (printed with LogLevel=DEBUG1)
  MOCK_REMOTE_HOME directory used as the remote $HOME and cwd
  MOCK_SSH_LOG     one JSON line per call: options and command
  MOCK_SSH_KILL_TTY  if set, a terminal (-t) session's client is killed
  MOCK_SSH_KEY       if set, the only -i key the "server" accepts (4.38)
  MOCK_SSH_FAIL_STAGE  if set, the bundle upload (cat > .cache/dssa/...) fails
  MOCK_CLOCK_SKEW    seconds added to the "server" clock in the plan's
                     clock read (4.39); MOCK_NTP is its NTPSynchronized
                     answer (default "yes"); MOCK_CLOCK_RAW replaces the
                     whole answer
  MOCK_REMOTE_HIDE   space-separated commands missing on the "server":
                     the remote side gets a PATH without them (4.39)
"""

import json
import os
import subprocess
import sys
import time

VALUE_FLAGS = {"-F", "-i", "-o", "-p", "-l"}


def hide_commands(path, hidden, home):
    """A PATH of one folder linking every command on PATH except HIDDEN."""
    folder = os.path.join(os.path.dirname(home), "hidden-path-" + "-".join(sorted(hidden)))
    if not os.path.isdir(folder):
        os.makedirs(folder)
        for directory in path.split(":"):
            if not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                link = os.path.join(folder, name)
                if name in hidden or os.path.lexists(link):
                    continue
                os.symlink(os.path.join(directory, name), link)
    return folder


def main(argv):
    options = {}
    tty = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            index += 1
            break
        if arg == "-t":
            tty = True
            index += 1
            continue
        if arg in VALUE_FLAGS:
            value = argv[index + 1]
            if arg == "-o":
                key, _, val = value.partition("=")
                options[key] = val
            else:
                options[arg] = value
            index += 2
            continue
        break
    host = argv[index]
    command = " ".join(argv[index + 1:])

    with open(os.environ["MOCK_SSH_LOG"], "a") as log:
        log.write(json.dumps({"host": host, "tty": tty, "options": options,
                              "command": command}) + "\n")

    presented = os.environ["MOCK_HOST_PUB"].split()
    known = options.get("UserKnownHostsFile")
    if known:
        trusted = False
        with open(known) as handle:
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1:3] == presented:
                    trusted = True
        if not trusted:
            print("Host key verification failed.", file=sys.stderr)
            return 255
        algorithms = options.get("HostKeyAlgorithms")
        if algorithms and presented[0] not in algorithms.split(","):
            print("no matching host key type found", file=sys.stderr)
            return 255

    if options.get("LogLevel") == "DEBUG1":
        print(f"debug1: Server host key: {presented[0]} {os.environ['MOCK_HOST_FP']}",
              file=sys.stderr)

    allowed = os.environ.get("MOCK_SSH_KEY")
    if allowed and options.get("-i") != allowed:
        print(f"{options.get('-l', 'user')}@{host}: Permission denied (publickey,password).",
              file=sys.stderr)
        return 255

    if os.environ.get("MOCK_SSH_FAIL_STAGE") and "cat > .cache/dssa/" in command:
        print("client_loop: send disconnect: Connection reset by peer", file=sys.stderr)
        return 255

    if tty and os.environ.get("MOCK_SSH_KILL_TTY"):
        # The laptop's ssh client is killed mid-session (a dropped link).
        os.kill(os.getpid(), 9)

    if command.startswith("date -u +%s;") and "MOCK_CLOCK_RAW" in os.environ:
        print(os.environ["MOCK_CLOCK_RAW"])
        return 0
    if command.startswith("date -u +%s;") and (
            "MOCK_CLOCK_SKEW" in os.environ or "MOCK_NTP" in os.environ):
        print(int(time.time()) + int(os.environ.get("MOCK_CLOCK_SKEW", "0")))
        print(os.environ.get("MOCK_NTP", "yes"))
        return 0

    home = os.environ["MOCK_REMOTE_HOME"]
    env = dict(os.environ, HOME=home)
    hidden = os.environ.get("MOCK_REMOTE_HIDE", "").split()
    if hidden:
        env["PATH"] = hide_commands(env["PATH"], hidden, home)
    return subprocess.run(["bash", "-c", command], cwd=home, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
