# Change Journal and Rollback (Milestone 4.33)

Status: **Implemented in 4.33** (`backend/linux/engine/journal.sh`, `tests/backend/test_journal_rollback.py`), manually verified on ubuntu24 and rocky10 2026-11-15. This is the first milestone that changes
servers. Builds on 4.30–4.32. Apply (4.34) wires it into `main.py`.

## 1. Goal and agreed rules

When a component fails, DSSA undoes **only what it changed for that
component**, returning it to the state recorded before the run.
Components that already succeeded stay installed. Independent
components may continue (4.31).

Already decided:
- Rollback is automatic, for the failed component only (2026-11-14).
- It restores the pre-run snapshot: packages, services and files absent
  before are removed; anything present before is left alone or
  restored (2026-11-15).
- The journal is written on the target, with a copy returned to the
  control machine (2026-11-15).
- InfraGuard pattern: record pre-state before every change; unknown
  pre-state means refuse; restore files before services; never restart
  a service after an incomplete rollback.

## 2. Scope split between 4.33 and 4.34

| 4.33 (this milestone) | 4.34 |
|---|---|
| Pre-run snapshot | Unified `main.py`: plan → approve → apply |
| Write-ahead journal | `consume_approval` before apply |
| Journaled executors for each allowlisted step type | Per-component drift re-check |
| Verification executor | Final report and laptop copy of the journal |
| Rollback of one component from its journal | Retire the legacy setup path |
| Run lock, failure injection, lab-VM rollback tests | Live Docker install on ubuntu24 |

The step executors live in 4.33 because rollback can only be tested
against the real changes they make.

## 3. Layout on the target

```text
/var/lib/dssa/                          root 0700
├── run.lock                            flock: one DSSA run per server
└── transactions/<plan_id>/             root 0700
    ├── snapshot.json                   pre-run state (§4)
    ├── journal.jsonl                   append-only, fsync per entry (§5)
    ├── backups/                        copies of files DSSA will replace
    └── result.json                     final component outcomes
```

`plan_id` names the run. Approvals are single-use, so a plan runs at
most once.

## 4. Pre-run snapshot

Taken once, after the lock is acquired and before any change:

| Item | Source |
|---|---|
| Every installed package with version | `dpkg-query -W` / `rpm -qa` |
| Every systemd unit: load state, unit-file state, active state | `systemctl list-unit-files`, `list-units --all` |
| Every path a plan component names: exists, type, owner, mode | definitions: `created_paths`, repo/key/source paths, `managed_file` paths, conflict paths |
| Repository files | APT `sources.list(.d)`, `/etc/apt/keyrings`, DNF `/etc/yum.repos.d` listing |
| OS identity | `os-release` (parsed) |

Scope: whole server (decision C).

## 5. Write-ahead journal

Every change is recorded **before** it happens (`intent`) and again
after (`done` or `failed`). Each line is written and fsynced before the
next action:

```json
{"seq": 7, "component": "docker", "step": 3, "type": "apt_vendor_repository",
 "phase": "intent", "pre": {"source_path": "absent", "key_path": "absent"}}
{"seq": 8, "component": "docker", "step": 3, "phase": "done"}
```

- `pre` holds exactly what rollback needs: was-present or absent, a
  backup path, was-enabled, was-active, the package set before the step.
- If a run is interrupted (crash, SSH drop, reboot), an `intent` with no
  `done` is treated as **possibly applied**. Every undo action is
  idempotent, so undoing something that never happened is safe.
- Unknown or unreadable pre-state means the step is refused before it
  runs (InfraGuard rule).

## 6. Step executors and what each journals

| Step type | Before acting, journal | Undo |
|---|---|---|
| `package_metadata_refresh` | nothing | nothing |
| `packages_install` | full package set before the step | remove packages **new** since that set (§7) |
| `apt_vendor_repository` | key and source files: absent, or backed up | delete if absent before, otherwise restore the backup |
| `dnf_vendor_repository` | repo file: absent, or backed up | same |
| `service_enable` | unit-file state | restore enabled/disabled |
| `service_start` | active state | restore active/inactive |
| `managed_file` | absent, or backed up with owner and mode | delete, or restore with owner and mode |
| `hook` | the hook journals through the shared journal functions | the hook's declared undo |

All executor output goes to stderr. Each executor prints one JSON
result on stdout (the 4.29 bug class is designed out).

## 7. Package rollback

- **Remove exactly the new packages.** Compare the package set after the
  failed step with the set recorded before it. Packages that appeared,
  including dependencies pulled in, are removed. Nothing that existed
  before is removed.
- **Never `autoremove`.** It would remove unrelated packages that apt
  already marked auto-installed.
- **Config files.** New packages are purged (`apt-get purge`,
  `dnf remove`), because their config files did not exist before the run.
- **Upgraded packages.** If installing upgraded an existing package
  (e.g. a shared library), rollback cannot reliably downgrade, since the
  old version may no longer be downloadable. They are reported, not reverted (decision A).
- **Other package activity.** The journal records the set before and
  after **each** DSSA step, so packages that something else (e.g.
  unattended-upgrades) installed between DSSA steps are not removed.

## 8. Rollback order (one component)

Walk the component's journal in reverse, then:

1. Stop the component's units that were not active before.
2. Remove new packages (§7).
3. Delete repository/key/source files that were absent, or restore
   backups.
4. Delete `created_paths` that were absent before. Never follow symlinks,
   and refuse protected paths (`/`, `/etc`, `/var`, `/usr`, `/home`,
   `/root`, `/boot`, `/opt` and any path with fewer than two parts).
5. Restore managed files from backup with owner and mode.
6. `systemctl daemon-reload`.
7. **Only if steps 1–6 all succeeded:** restore the recorded
   enabled/active state of units that existed before.

The component's outcome is `rolled_back` or `rollback_incomplete`, with
each undo action's result listed.

## 9. What triggers rollback

- Any step executor fails.
- A precheck hook fails.
- Verification after install fails (decision D).
- The per-component drift re-check is part of 4.34. A drifted component
  never starts, so there is nothing to roll back.

## 10. Interrupted runs

If the SSH session drops, the run on the target continues to its end,
because the bundle is started under `setsid` with its output also
written to the transaction folder. If the target itself crashes or
reboots, the next DSSA connection finds an unfinished journal (an
`intent` with no closing `result.json`). DSSA then refuses new runs on
that server until the operator chooses **resume rollback** from the
journal. Rollback works from the journal alone, so this is possible
without the original session.

## 11. Testing

- **Hermetic:** mocked `apt-get`/`dpkg`/`rpm`/`dnf`/`systemctl` on `PATH`
  and a temporary `DSSA_ROOT`. Failure injection at every step and every
  undo action, interrupted journals (intent without done), protected
  path refusal, symlink refusal.
- **Lab VMs (changes servers):** a test-only definition installing a
  small harmless package (e.g. `tree`), forced to fail at verification,
  then checked: the package is gone, the package set equals the
  snapshot, units are unchanged. Run on one APT VM and one DNF VM.
  Before this, please take VM snapshots (VirtualBox) of the lab VMs so
  any mistake is reversible.

## 12. Decisions (2026-11-15)

- **A. Upgraded packages: report only.** A package that existed before
  and was upgraded during install is left upgraded and listed as
  "upgraded, not reverted" with old and new versions.
- **B. Rollback failure stops the whole run.** On `rollback_incomplete`
  no further components run; the report lists every undo action's
  result and the server needs operator review before the next run.
- **C. Snapshot: whole server.** All installed packages and all
  systemd units, plus every path the plan's components name.
- **D. Verification failure triggers rollback**, like any other
  component failure.

## 13. Retention (proposal)

Transaction folders stay on the target for 30 days, then are removed
at the start of a later run. Laptop copies are kept indefinitely.
