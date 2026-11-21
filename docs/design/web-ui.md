# Web UI on a Master Server — Milestone 4.46

Application flow, decisions 6 and 7. The web UI runs on one **master
server** on a subdomain and manages other servers over SSH, through the
same steps as the command line (`bridge/components/*`, `dssa-run.sh`).
Standard library only.

These were the open points decision 6 left to this design. Each choice
below is the safer option, built first; each can be changed.

## 1. Privilege on managed servers: sudo still asks on the server

The UI cannot type at a server's terminal, so it relays one:

- Apply, report and resume-rollback run `dssa-run.sh` with a
  pseudo-terminal as input, so `ssh -t` gives the server a terminal.
- The run page shows the server's output live. When sudo asks, the
  operator types the password into the page's password field; the master
  writes it to that terminal and nothing else. It is not stored, logged
  or echoed. sudo does not echo it either.
- No standing passwordless sudo is installed anywhere.

Alternative for later: a dedicated user per server whose sudo rule allows
only a fixed DSSA runner without a password. That would allow unattended
runs, but whoever holds the master's key would be root on every server.
Not built.

## 2. Server credentials: entered once, then dropped

Adding a server (`/servers/add`):

1. The operator enters a name, host, port and SSH user.
2. The master reads the host keys the server offers (`ssh-keyscan`) and
   shows their SHA-256 fingerprints. Nothing is trusted yet.
3. The operator compares one with the server's own, for example
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on its console,
   confirms it, and enters the SSH user's password.
4. The confirmed key is added to the master's `known_hosts`. The password
   is used once, through `SSH_ASKPASS` (never on a command line), to
   append the master's public key to `~/.ssh/authorized_keys`. Key login
   is then checked, and the server is added to the web UI's inventory.
5. The password is dropped. It is never written to disk or logged.

From then on the master logs in with its own key
(`~/.ssh/dssa_master_ed25519`, made by `init`), pinned to the confirmed
host key exactly as on the command line. The scan result waits at most
10 minutes, for the same UI user only.

## 3. The UI's own login and HTTPS

- **Users:** added on the master's command line only
  (`python -m bridge.webui adduser NAME`), at least 12 characters, scrypt
  hashes in `users.json` (mode 0600). Five failed logins lock a name for
  5 minutes.
- **Sessions:** a random token in an HttpOnly, SameSite=Strict cookie
  (Secure on HTTPS), kept in memory. A session expires after 8 hours, or
  30 minutes idle. A restart logs everyone out.
- **CSRF:** every form carries the session's token; a POST without it is
  refused (403).
- **Headers:** CSP `default-src 'self'` with no inline scripts,
  `frame-ancestors 'none'`, `no-store`, HSTS on HTTPS.
- **TLS:** the UI serves HTTPS itself (`--cert`, `--key`, TLS 1.2+).
  Plain HTTP only on `127.0.0.1`, for a reverse proxy on the same machine
  that terminates TLS for the subdomain. Anything else is refused.

## 4. Audit

`audit.jsonl` in the state folder (0600): logins, failed logins, servers
added (with the confirmed fingerprint), plans, approvals, applies, reports
and resumes, each with the UI user. Approvals are also recorded by the
approval code with `approved_by` = the UI user. Passwords never appear.

## 5. Flow in the UI

- **Servers page:** pick servers, components, the operation (install,
  update, remove, adopt) and optionally "all or nothing", then **Make plan**.
- **One server:** the plan page. Approve on that page: typed names for
  risky actions (shared rules: `bridge/components/risk.py`), then
  `APPROVE`. Then **Apply**, which opens the run page.
- **Several servers:** a batch (decision 7). One plan per server, shown
  together with each plan's hash. One approval covers them all, then they
  are applied server by server; the batch stops at the first server that
  does not finish with exit 0, and the rest are not started.
- **Server page:** the install record ("Installed by DSSA").

## 6. Setting it up on the master

```bash
git clone … && cd developer-server-setup-assistant
python3 -m bridge.webui init                    # state folder + master key
python3 -m bridge.webui adduser alice           # password asked twice
# HTTPS directly (certificate for the subdomain):
python3 -m bridge.webui serve --host 0.0.0.0 --port 8443 \
    --cert /etc/letsencrypt/live/dssa.example.com/fullchain.pem \
    --key  /etc/letsencrypt/live/dssa.example.com/privkey.pem
# or behind nginx/caddy on the same machine:
python3 -m bridge.webui serve --host 127.0.0.1 --port 8443
```

Run it as a dedicated, unprivileged user (it needs no root on the
master). A systemd unit example is in `docs/webui/dssa-webui.service`.

State: `$DSSA_WEBUI_HOME`, else `~/.config/dssa-webui/` (users, inventory,
audit). Plans, approvals and run copies use the same folders as the
command line (`~/.local/state/developer-server-setup-assistant/`).

## Not built yet

- Live terminal over WebSocket (the page polls every 0.7 s).
- Unattended runs (needs the alternative privilege model in section 1).
- UI user roles (every UI user can do everything).
