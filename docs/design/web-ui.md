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

Integration pass 1 (2026-11-25, `docs/REQUIREMENTS.md`, "Web UI Integration
Requirements", item 1):

- **Workspace** (after sign-in): **Saved servers** and **Add server**.
  Add server is the enrolment described in section 2, unchanged.
- **Saved servers:** the inventory; each server opens its applications.
  An empty inventory points to Add server.
- **Applications:** the shared catalog (every definition), with search
  (name, ID, category, description) and a category filter. Both work
  without JavaScript; with it, rows filter as you type. Rows are links,
  so Tab and Enter choose one; the chosen row has `aria-current`, and
  focus moves to its actions.
- **Installed state** comes from DSSA's install record (one SSH read,
  reused for 60 s). Recorded apps offer update or remove; others offer
  install or adopt. If the record cannot be read, the page says why and
  offers all four: the planner decides. A control panel in the record
  marks the apps it owns "Managed by <panel>" (the same ownership the
  planner uses); a panel installed some other way is found at planning.
- **Review plan** sends exactly one server, one component and one
  operation to the planner. A repeated or unknown field is refused before
  planning. Drafts are allowed only on lab servers and only for install.
- A plan where nothing can run says so and keeps every held or blocked
  reason; it offers no Approve or Apply.

Items 2 and 3 (2026-11-25, `docs/design/webui-temporary-access-and-approval.md`):

- **Add server** has "Save this server for future use", unticked. Unticked,
  the password installs a key for this setup only: its line has
  `expiry-time` (60 minutes, in the server's own time zone), `from=` the
  master's address and no forwarding. The entry, host key and private key
  stay in `<home>/temporary/<name>/` (0700), never in the inventory or the
  master's known_hosts. Only its owner sees or uses it (Workspace, "This
  setup only"). Access ends, line removed, after a successful run, on
  **End access**, on sign-out, near expiry, and after a web UI restart
  (sessions are in memory). After a failed run it stays until then, so
  Report and Resume rollback still work. If removal fails, the line stops
  working at its expiry time anyway.
- **Approve plan:** tick "I reviewed the target, operation and warnings";
  typed names stay for risky actions. Only the approver can apply.

The earlier form below is kept at **/batch** (linked from Saved servers)
until the single-approval change (requirements item 3) decides how
several servers or applications are handled. Multi-app actions are
deferred in the new flow.

**Decision (2026-11-26): A, a selection list.** Each catalog row gets a
checkbox. The panel shows "Selected (n)" with one action for all of them,
then **Review plan**. That gives one plan, one approval and one apply,
using the planner's existing multi-component plans. Risky items still need
their names typed at approval. Mixed operations in one plan are out of
scope. The goal: a developer never logs in to the server, so the web UI
has to be both easy and safe.

Built 2026-11-26 (lab check: `docs/testing/manual-webui-selection.md`):
- **Selection.** Each row has a checkbox that belongs to the plan form.
  - The selection is kept in the URL (`sel=`), so it survives filtering
    and opening a row, also without JavaScript.
  - Apps the filter hides stay in the plan, and each can be dropped with
    its **Remove** button.
  - At most 25 applications go in one plan.
- **Actions.** One action applies to everything selected. The page offers
  only the actions that fit:
  - all selected apps not installed: Install or Adopt;
  - all installed: Update or Remove;
  - a mix: a warning, and Review plan is disabled.

  Without JavaScript all four actions are offered, and the planner
  decides.
- **Plan.** `POST /servers/<server>/plan` takes repeated `component`
  fields (duplicates dropped) and exactly one `operation`. It makes one plan
  through `make_app_plan`; risky and draft names are still typed at
  approval.
- **Plan page.** The summary lists **Changes** (no SKIP rows) and
  **Verified only**. The red "Nothing in the plan is runnable." is not
  repeated under the notice. Report and Resume rollback appear only once
  the plan has been applied.

- **Batch plan page (/batch):** pick servers, components, the operation (install,
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

- Panel access (`docs/design/panels.md`).

- Live terminal over WebSocket (the page polls every 0.7 s).
- Unattended runs (needs the alternative privilege model in section 1).
- UI user roles (every UI user can do everything).
