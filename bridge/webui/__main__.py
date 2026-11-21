"""python -m bridge.webui init | adduser NAME | deluser NAME | users | serve ...

  init      create the private state folder and the master's SSH key
            (~/.ssh/dssa_master_ed25519, ed25519, no passphrase: it is the
            key the master logs in to servers with; keep the master secure)
  adduser   add a web UI user (password asked twice on this terminal)
  serve     --host 0.0.0.0 --port 8443 --cert FULLCHAIN.pem --key KEY.pem
            (plain HTTP only with --host 127.0.0.1, behind a reverse proxy)

State folder: $DSSA_WEBUI_HOME, else ~/.config/dssa-webui (mode 0700).
"""

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

from bridge.webui.app import App, Config, serve
from bridge.webui.auth import AuthError, Users


def config(args):
    home = Path(os.environ.get("DSSA_WEBUI_HOME") or Path.home() / ".config/dssa-webui")
    return Config(home, definitions_dir=os.environ.get("DSSA_DEFINITIONS_DIR") or None,
                  master_key=getattr(args, "master_key", None))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m bridge.webui")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--master-key")
    for name in ("adduser", "deluser"):
        sub.add_parser(name).add_argument("name")
    sub.add_parser("users")
    srv = sub.add_parser("serve")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8443)
    srv.add_argument("--cert")
    srv.add_argument("--key")
    srv.add_argument("--master-key")
    args = parser.parse_args(argv)
    cfg = config(args)

    if args.command == "init":
        cfg.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(cfg.home, 0o700)
        if not cfg.master_key.exists():
            cfg.master_key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "dssa-master",
                            "-f", str(cfg.master_key)], check=True, stdin=subprocess.DEVNULL)
        fingerprint = subprocess.run(["ssh-keygen", "-l", "-f", f"{cfg.master_key}.pub"],
                                     capture_output=True, text=True, check=True).stdout.strip()
        print(f"State folder: {cfg.home}\nMaster key  : {cfg.master_key}\n              {fingerprint}")
        return 0
    users = Users(cfg.users)
    try:
        if args.command == "adduser":
            first = getpass.getpass(f"Password for {args.name}: ")
            if first != getpass.getpass("Again: "):
                print("The passwords differ; nothing changed.", file=sys.stderr)
                return 2
            users.add(args.name, first)
            print(f"Added {args.name}.")
            return 0
        if args.command == "deluser":
            users.remove(args.name)
            print(f"Removed {args.name}.")
            return 0
        if args.command == "users":
            print("\n".join(users.names()) or "No users yet.")
            return 0
    except AuthError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.command == "serve":
        if not users.names():
            print("ERROR: Add a user first: python -m bridge.webui adduser NAME", file=sys.stderr)
            return 2
        if bool(args.cert) != bool(args.key):
            print("ERROR: --cert and --key go together.", file=sys.stderr)
            return 2
        serve(App(cfg), args.host, args.port, args.cert, args.key)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
