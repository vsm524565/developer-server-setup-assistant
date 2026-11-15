# Unified Plan → Approve → Apply (Milestone 4.34)

Status: **Implemented 2026-11-15** (decisions in §11; build notes in §13), manually verified by the project owner on rocky10 and ubuntu24.
Builds on 4.30 (definitions), 4.31 (plans), 4.32 (approvals) and
4.33 (journal and rollback).

## 1. Goal

One entry point, `main.py`, that takes a server from "selected
components" to "installed and verified, or rolled back" through three
distinct steps:

1. **Plan**: read-only, no sudo. Builds the 4.31 plan on the target.
2. **Approve**: on the laptop. Writes the 4.32 single-use approval
   record after you type `APPROVE`.
3. **Apply**: claims the approval, then runs the 4.33 journaled install
   on the target. `sudo` asks for the password on the remote terminal.

After 4.34 there is only one way to change a server. The two older
paths are retired (§8).

## 2. What exists today

| Path | How it connects | Status after 4.34 |
|---|---|---|
| `main.py` (original) | Python `paramiko` with a password typed into Python; discovery, planning and approval in `connection/`, `discovery/`, `planning/`, `verification/`, `execution/` | Replaced. It breaks the architecture rule that Python never holds credentials |
| `bridge/cli.py` → `setup-components.sh` | Bash `ssh -t`, `sudo` at the remote prompt; hard-coded Nginx/Git/Docker installers; all-or-nothing `eligible` | APPLY retired (§8) |
| 4.30–4.33 engine | `definitions.sh`, `plan.sh`, `journal.sh` and `bridge/components/*`, so far driven only by the manual test helpers | Becomes the only install path |

The `bridge/applications`, `bridge/lifecycle` and assessment CLIs are
read-only discovery and readiness work for the app catalog. 4.34 does
not change them.

## 3. The flow

```
main.py
  ├─ choose target        (inventory: servers.json or DSSA_INVENTORY_FILE)
  ├─ choose components    (catalog from definitions/components, support shown)
  │
  ├─ PLAN     Python → backend/linux/bin/dssa-run.sh plan
  │             ssh (BatchMode, no sudo) 'bash -s'  ← engine bundle + request
  │             → plan.json + the host key the session presented
  │           Python validates and displays the plan
  │
  ├─ APPROVE  preflight_plan → show the short hash → type APPROVE
  │             → approval record (4.32); SKIP-only plans skip this
  │
  └─ APPLY    consume_approval (atomic, single use)
              Python → dssa-run.sh apply PLAN_FILE PLAN_SHA256
                ssh -t (pinned host key, §5) sudo bash  ← apply bundle
                  target: lock → snapshot → per component:
                          re-check → journal → steps → verify → rollback if needed
                → report + journal copy saved on the laptop (§7)
```

`main.py` stays thin: menus, display and the approval prompt. Python
starts the Bash entry point with `subprocess` and lets it use the
terminal, so the remote `sudo` prompt reaches you directly. Python
never sees the password and never opens SSH itself.

## 4. Components and files

| File | Role |
|---|---|
| `main.py` | Interactive entry point (§11 A decides its shape) |
| `bridge/components/run.py` (new) | Calls `dssa-run.sh`, parses its single JSON result, stores reports |
| `backend/linux/bin/dssa-run.sh` (new) | Resolves the target from the inventory, builds bundles, owns SSH, audits `run.planned` / `run.started` / `run.completed` |
| `backend/linux/engine/apply.sh` (new) | Target-side run: plan order, re-check, `apply_component`, stop rules, final result |
| `backend/linux/engine/hooks/*.sh` (new) | Named per-component hooks allowed by definitions (e.g. `verify_docker_daemon`) |

`dssa-run.sh` reuses the SSH options already proven in
`setup-components.sh`: `-F /dev/null`, key only, `IdentitiesOnly`,
`StrictHostKeyChecking=yes`, no agent or forwarding, and
`ControlMaster=no`.

## 5. Host identity: bind the key the session presents

The 4.32 note: rocky10 has three keys in `known_hosts`, and the identity
gate accepts any of them.

- **Plan:** `dssa-run.sh plan` runs SSH with `-o LogLevel=VERBOSE` and
  reads the `Server host key: <type> SHA256:…` line from SSH's own
  stderr. That exact key goes into the plan as `host_key_sha256`, and
  the matching `known_hosts` line is saved next to it.
- **Apply:** SSH runs with `UserKnownHostsFile=<temp file holding only
  that one key line>` and `StrictHostKeyChecking=yes`. If the server
  presents any other key, SSH itself refuses before any command runs.
  No compare-after-connect race is possible.
- The 4.32 identity gate becomes "the bound key is still present in
  `known_hosts`" instead of "any key for the host".

## 6. Apply on the target

The apply bundle contains the engine (`package_manager.sh`,
`definitions.sh`, `plan.sh`, `journal.sh`, `apply.sh`, hooks) plus
the plan.

1. **Integrity:** the bundle re-checks the plan's sha256 and the
   definition digests (4.31). A mismatch stops before any change.
2. **Lock and unfinished runs:** `begin_transaction` (4.33) takes the
   run lock and refuses if an earlier run never finished. You are then
   offered `resume-rollback` for that plan (§11 has no decision here;
   this was agreed in 4.33).
3. **Per component, in plan order:**
   - **Re-check (drift):** re-run that component's single-component
     decision against the live server. If it differs from the approved
     step (for example, Git is now already installed, or a conflict has
     appeared), the component becomes `drift_skipped` and its required
     dependents `not_attempted`. Independent components continue
     (4.31 decision D).
   - **SKIP entries** run `verify_component` only: `verified`, or
     `verification_failed` with nothing to roll back.
   - **INSTALL entries** run `apply_component`: `installed`, or
     `failed_rolled_back` with required dependents `not_attempted`.
   - **`rollback_incomplete` stops the whole run.** Every remaining
     component becomes `not_attempted` (reason
     `stopped_after_rollback_incomplete`). The report says the server
     needs review (4.33 decision B).
4. **SSH drops:** the root runner starts the run under `setsid`, with
   its output also written to `<tx>/run.log`. The terminal only follows
   that log. If the connection drops, the run finishes on the target,
   and `main.py report <plan_id>` fetches the result later (4.33 §10).
5. **Exactly one JSON value** comes back, after a `DSSA_RESULT:` marker.
   All command output goes to stderr or the log, as in 4.33.

Outcomes: `installed`, `verified`, `drift_skipped`,
`verification_failed`, `failed_rolled_back`, `rollback_incomplete`,
`not_attempted`, plus `held` from the plan.

## 7. Report and laptop copy

- The final report lists every plan entry with its outcome, reason,
  `blocked_by`, rollback actions and `upgraded_not_reverted`.
- In the same sudo session, the bundle prints the journal, snapshot
  summary and `result.json` (base64, after a marker). They are saved on
  the laptop under
  `~/.local/state/developer-server-setup-assistant/runs/<plan_id>/`
  (0700/0600) beside the approval record. No second sudo prompt is
  needed.
- The exit code tells scripts the overall outcome: `0` every runnable
  component installed or verified, `3` some components failed and were
  rolled back, were skipped by drift, or failed verification, `4`
  rollback incomplete or engine error, `2` refused before any change,
  `255` the connection failed (use `report`).

## 8. Retiring the old paths

Removed once the new flow is tested:

- `setup-components.sh` APPLY mode, `operations/setup.sh`,
  `modules/install/{git,nginx,docker}.sh`, `verification/{git,nginx,docker}.sh`.
  This also removes the `verify_docker` stdout bug (it writes to
  stdout inside a JSON-producing function). Its replacement, the
  `verify_docker_daemon` hook, runs with stdout sent to stderr
  (`journal.sh` already does `"$hook" >&2`).
- `bridge/cli.py` setup and `LinuxBackend.setup_components`, with the
  tests that cover only them.
- The original Python `main.py` flow and the packages only it uses
  (`connection/`, `discovery/`, `planning/`, `verification/`,
  `execution/`), the root-level `test_*.py` files and `paramiko` in
  `requirements.txt` are deleted (§11 B).

Before any removal, a check confirms that nothing else imports these
modules (for example, `bridge/planning/snapshot.py` imports from
`planning`, so that is checked first).

## 9. Docker on ubuntu24 (the live install)

- Docker's only variant is `draft`, and `apply_component` refuses draft
  variants. That rule stays.
- Its verification names the hook `verify_docker_daemon`, which **does
  not exist yet**. 4.34 writes it in `engine/hooks/docker.sh`: the
  daemon answers `docker info` and `hello-world` is not pulled (no
  network side effects).
- How Docker moves from draft to supported is in §11 C.

## 10. Tests

- **Hermetic:** the 4.33 mock system on `PATH` plus a mock `ssh` that
  runs the bundle locally. Tests cover: plan → approve → apply end to
  end; a second apply refused (approval used); a wrong host key refused;
  drift skips a component and its dependents; a failure rolls back and
  independent components continue; `rollback_incomplete` stops the run;
  an interrupted run is refused, then resumed; SKIP-only plans need no
  approval; the report and laptop copy are written 0600.
- **Lab, with you running each step:** Git on rocky10 and ubuntu24
  (supported), a forced failure with a dependent, drift (installing
  Git by hand between approve and apply), Docker on ubuntu24 (§11 C).
- **Legacy removal:** the full regression passes after the removal,
  with the counts stated in the roadmap.

## 11. Decisions (2026-11-15)

- **A. Shape of `main.py`.**
  1. **Chosen:** run `main.py` with no arguments for one guided
     session (target → components → plan → APPROVE → apply). The same
     steps are also available as `main.py plan | approve | apply |
     report | resume-rollback`, so approval can happen separately.
  2. Subcommands only: each step is always a separate command.
- **B. Old Python `main.py` path.**
  1. **Chosen:** delete it, with `paramiko` and the packages only it
     uses (git history keeps it).
  2. Move it to `legacy/` and stop maintaining it.
- **C. Docker draft → supported.**
  1. **Chosen:** the target-side rule stays. `main.py apply
     --allow-draft` works only for inventory targets marked
     `"lab": true` and asks you to type the component name. After your
     lab run passes, a separate commit marks Docker `supported`.
  2. Mark Docker `supported` first, then test it on ubuntu24.
- **D. Transaction retention on the target: 30 days** (as built in
  4.33). The laptop copy is kept indefinitely.

## 12. Not in 4.34

- Nginx install (still deferred; its definition stays draft).
- The remaining catalog components and panels. They follow as
  definitions on this same path, one milestone at a time.
- A frontend. `main.py` stays a terminal UI.

## 13. Build notes (what differs from the draft above)

- **How the bundle reaches root.** One shell argument is limited to
  128 KB, and the apply bundle is larger. So `dssa-run.sh` first copies
  the bundle into the remote user's `~/.cache/dssa/` without sudo. Then,
  in the sudo session, root copies it to a root-only file under
  `/var/lib/dssa/stage/`, checks its SHA-256 against the digest the
  laptop sent, and runs only that copy. The staged files are removed.
- **Run folder.** `/var/lib/dssa/runs/<plan_id>/` (root-only) holds
  `bundle.sh`, `run.log` and `summary.json`. If this folder already
  exists, the target refuses the plan: a plan runs on a server at most
  once, even apart from the laptop's single-use approval. Run folders
  follow the same 30-day retention as transactions.
- **Result markers.** `DSSA_FILE <name> <base64>`, `DSSA_STATUS`, and
  `DSSA_EXIT`. `bridge/components/run.py` hides these lines and passes
  every other byte through as it arrives, so the `sudo` prompt shows at
  once. Only the six known file names are saved.
- **Hooks.** A definition can call only hooks registered in
  `DSSA_HOOKS` by `engine/hooks/*.sh`, never an arbitrary engine
  function. `precheck` hooks are refused until one is needed.
- **Draft rule in two places.** `--allow-draft` is checked against the
  inventory's `"lab": true` at plan time and again at apply time. The
  plan records `allow_draft`, and the approval binds it like every
  other byte of the plan.
- **Also removed** (their only caller was the retired setup path): the
  legacy planner `backend/linux/planning/components.sh`, and
  `bridge/planning/snapshot.py`, which imported the deleted `discovery`
  package. The 4.30 parity tests now check the definition decisions
  alone.

