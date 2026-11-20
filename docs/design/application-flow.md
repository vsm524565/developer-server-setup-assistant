# Application Flow — Decisions (draft)

Status: all seven decided, written up one by one with the project owner, starting
2026-11-20. The final flow document is written from these once all are
settled. Current behaviour (as of e0572c3) is plan → approve → apply for
installs, with per-component rollback.

## 1. Updates — decided 2026-11-20: option C

- A normal `plan` reports an available update for an installed component
  as **information only** (the component stays SKIP: verify only).
- `plan --update TARGET COMPONENT...` makes a separate update plan with the
  exact target versions written in. It is approved and applied like any
  plan, through the same engine (snapshot, journal, rollback).
- Limits follow the Patch Updater pattern (details in the update design):
  exact versions, no branch change, no package removals, keep edited config
  files, restart only services that were running before.
- How an update is rolled back (downgrade to the exact old version, or
  not) is settled under decision 5.

## 2. Uninstall — decided 2026-11-20: option B, with explicit adopt

- `plan --remove TARGET COMPONENT...` makes an uninstall plan, approved and
  applied like any plan (snapshot, journal).
- Only components **DSSA installed** can be removed. Provenance comes from
  a new permanent install record per server (`/var/lib/dssa/installed.json`,
  written after each successful install; not subject to transaction
  retention). Anything else is BLOCKED: "not installed by DSSA".
- **Packages only.** Data folders (e.g. `/var/lib/docker`, web roots) are
  never deleted by an uninstall; the plan lists what stays behind.
- Refused while an installed component depends on the one being removed,
  unless the dependents are named in the same plan.
- **Adopt:** a planned, approved `adopt` verifies an existing install and
  records it as DSSA-managed; only then can DSSA remove it. (Needed for
  installs made before the record existed, e.g. Docker on the lab VMs.)
- The install record is built before uninstall itself.
- Undoing an uninstall (reinstalling the exact old versions) is settled
  under decision 5.

## 3. Configuration ownership — decided 2026-11-20: option B

"DSSA creates, you own."
- `managed_file` (already in the contract) becomes an allowed step. It
  writes **new** files from templates, at install only.
- **DSSA's own file names only** (e.g. `/etc/nginx/conf.d/dssa-default.conf`).
  DSSA never edits a package's main config or a panel's files.
- An existing file at that path is never overwritten: the plan is BLOCKED.
- The written file's SHA-256 goes into the install record; after install
  the file is the operator's to edit.
- **Test before keeping:** the service's own config test (e.g. `nginx -t`)
  must pass before a reload; a failure rolls back (the new file is removed).
- Uninstall removes a DSSA file only if unchanged; an edited file is kept
  and listed.
- Moving later to "DSSA keeps owning" (re-render, drift = REVIEW) stays
  possible because the SHA-256 is recorded.

## 4. Approval granularity — decided 2026-11-20: option C

- One `APPROVE` per plan, as today: bound to the plan's hash, single use,
  30 minutes. To leave a component out, plan again without it.
- **Typed component names** in addition, for risky actions:
  - remove (any component);
  - update that restarts a service running now;
  - adopt;
  - draft (as today).

## 5. Rollback — decided 2026-11-20: 5a option C, 5b option i

**5a. Scope**
- Default as today: a failed component is rolled back, its dependents are
  skipped, independent components continue.
- `plan --all-or-nothing` writes that mode into the plan (so it is
  approved knowingly): any failure rolls back every change of the run, the
  finished components in reverse order.

**5b. Undoing an update or uninstall**
- Before changing anything, the engine downloads the package files of the
  currently installed versions (`.deb`/`.rpm`) into the transaction folder;
  undo reinstalls from those files, independent of the repository.
- Checked at plan time: if a package file cannot be obtained, the plan
  marks the action "not reversible", which needs the typed component name
  (decision 4).
- The kept files live as long as the transaction (retention period).

## 6. Interface — decided 2026-11-20: CLI + JSON now, web UI on a master server next

- **Local device:** the command line, with a `--json` output mode for every
  command, so other front ends reuse the same steps.
- **Web UI:** installed on a **master server**, served on a subdomain. The
  operator enters a server's credentials there, and the master manages
  that server over SSH, the way the CLI does from the laptop.
- **Order:** `--json` and the flow changes (decisions 1–5) on the CLI
  first; the web UI is built on the same steps right after.
- **For the web UI's own design** (decided there, not now):
  - Privilege on managed servers: the UI cannot type sudo at a server's
    prompt, so it needs a non-interactive model (for example a dedicated
    user whose sudo allows only the DSSA runner).
  - Credentials: today's rule is that Python never holds credentials. The
    UI taking credentials as input changes that, so the design must say
    what is entered, how long it is kept, and where. One option: the
    password is used once to set up key login and the restricted sudo
    rule, then discarded.
  - The UI's own login, HTTPS on the subdomain, and the audit of who did
    what.

## 7. Several servers — decided 2026-11-20: one server per plan, batch on top

- The engine keeps **one server per plan** (host-key binding, snapshot
  and rollback stay per server).
- A **batch** layer on the laptop or master plans several servers
  together, shows them together, takes one `APPROVE` that lists each
  plan's hash, and applies server by server, **stopping at the first
  failed server** (the rest are not started).
- Built with the web UI (decision 6), not before the components.

## Order of work (from these decisions)

1. `--json` output for every command (decision 6).
2. The permanent install record (decision 2); needed by uninstall, adopt
   and config ownership.
3. Flow changes on the CLI: `--update` (1), `--remove` and `adopt` (2),
   typed names for risky actions (4), `--all-or-nothing` and kept package
   files for undo (5), `managed_file` (3).
4. Web UI on the master server, with its privilege and credentials design,
   and batches (6, 7).
5. New components (Nginx first) on the finished flow.

Order confirmed by the project owner on 2026-11-20.
