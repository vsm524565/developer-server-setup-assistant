# Hardening Before the Catalog (Milestone 4.35)

Status: **Implemented 2026-11-16** (decisions in §6; build notes in §9), manually verified by the project owner on ubuntu24 and ubuntu22 (2026-11-17).
Builds on 4.33 (journal and rollback) and 4.34 (unified apply).

## 1. Goal

Close three gaps before more components are added:

1. **Dropped connections, proven live.** 4.34 runs each apply detached
   (`setsid`), so it should finish even if SSH drops, and `report` /
   `resume-rollback` should handle what happens next. Only the mock
   tests prove this so far.
2. **Definition file permissions.** Definitions decide what runs as
   root. Anyone who can write them can change that, but DSSA does not
   check their permissions today. The inventory file is already checked
   (`inventory.sh` refuses group/other-writable files).
3. **Stale apt lists after a vendor-repository rollback.** Rolling back
   Docker removes `docker.sources`, but apt keeps the downloaded
   `download.docker.com` package lists until the next `apt-get update`.

Plus one small leak found while reviewing the 4.34 runner (§5).

## 2. Dropped connection: what should happen

| Moment the connection drops | Expected result |
|---|---|
| During apply, run healthy | The run finishes on the target. The laptop prints "No result came back". `main.py report` later shows `finished` with the full result and saves the laptop copy |
| `report` while the run is still going | `Run state : running`; partial log returned |
| Run process killed (simulated crash) | `report` shows `interrupted`. A new apply on that server is refused ("Unfinished transaction requires resume rollback"). `resume-rollback` undoes the components that began but never ended. Then new applies work again |

A test-only definition (as in 4.33/4.34.1) installs a harmless package.
Its first step is `apt-get update`, which gives a window of about 10
seconds to drop the connection.

**Code changes expected:** probably none, since this is a live check
of 4.34 behaviour. If the test finds a gap, the fix is part of 4.35.
One known gap to decide on is in §6 B: after a real crash in the middle
of a `dpkg` operation, the package database can be left half-configured.

## 3. Definition file permissions

Checked everywhere a definition is read: planning (`build_plan_request`),
apply (`dssa-run.sh` reads the texts to send), and the Python loader
(`load_component_definition`, `changed_definitions`).

| Check | Rule |
|---|---|
| Type | regular file, not a symlink (already enforced in Bash) |
| Owner | the user running DSSA, or root |
| Other users | never writable by others (`o+w` refused) |
| Group | writable only if the group is the owner's private group with no other members (§6 C) |
| Folder | the same owner and write rules as the files, because a writable folder lets someone replace a file |

A refusal names the file and the fix (`chmod go-w …`). Nothing is read
from a refused folder.

## 4. Stale apt lists after rollback

When rollback removes or restores an apt repository source file:

- the repository's entry in the rollback report already says `ok`
- **new:** after the repository files are undone, the rollback refreshes
  apt's lists once with `apt-get update` (§6 D), so apt stops
  offering packages from a repository that no longer exists

DNF has no vendor-repository step yet, so this applies to apt only.

## 5. Small fix: staged bundle left behind on refusal

If the target refuses a plan because it already ran ("This plan has
already been run on this server"), the root-only staged copy in
`/var/lib/dssa/stage/` is never removed. Fix: delete it in that branch,
and remove stage files older than the retention period at the start of
each run.

## 6. Decisions (2026-11-16)

- **A. How to drop the connection in the live test.**
  1. **Chosen:** type the SSH escape `~.` (Enter, then `~`, then `.`)
     during step 1. The local ssh client closes the connection at
     once, like a network drop, and nothing else is affected.
  2. Close the terminal window.
  3. Disconnect the VM's virtual network cable in VirtualBox. This also
     breaks the run's own downloads, so the result would be a rollback,
     not a finish.
- **B. Simulated crash, and dpkg left half-configured.**
  1. **Chosen:** simulate the crash with `sudo kill -9` on the run's
     process from a second terminal, between steps. `resume-rollback` does
     **not** repair dpkg by itself. If removing a package fails because
     dpkg was interrupted, the result is `rollback_incomplete`, with a
     hint to run `sudo dpkg --configure -a`.
  2. The same crash test, but `resume-rollback` runs
     `dpkg --configure -a` (apt) or `dnf history` checks automatically
     before undoing.
  3. Also test a hard VirtualBox power-off mid-install (strongest test,
     may need `dpkg` repair by hand).
- **C. Group-writable definition files** (yours are `0664` and `0775`;
  your `vivek` group has no other members).
  1. **Chosen:** refuse other-writable always. Allow group-writable
     only when the group is your own private group with no other
     members (the usual Ubuntu/RHEL setup). Your repository works
     unchanged, and a shared group is refused.
  2. Strict, like the inventory: refuse any group- or other-writable
     file or folder. You run `chmod -R go-w definitions` once, but new
     files from `git pull` under umask 002 would be refused again
     until you fix them.
  3. Refuse other-writable only.
- **D. Refreshing apt lists after a repository rollback.**
  1. **Chosen:** run `apt-get update` once after the repository
     undo. If it fails (for example, no network), the rollback still
     counts as complete, and the report notes "lists not refreshed"
     (the lists are a cache, not installed state).
  2. Delete only that repository's list files from `/var/lib/apt/lists/`
     (no network, but depends on apt's internal file naming).
  3. Leave as is and keep it documented as a known limit.

## 7. Tests (planned)

- Hermetic: permission rules for files, folders, owner and group cases
  in Bash and Python. Apt list refresh after a repository undo,
  including a failed refresh. Stage cleanup on refusal.
- Lab, with you running each step: drop during apply → `report`. A
  killed run → `report` shows `interrupted`, a new apply is refused, then
  `resume-rollback` and a clean state. A Docker-style repository rollback
  on ubuntu24 with a test-only definition, then apt no longer lists the
  repository's packages. Every test leaves the VMs as they started.

## 8. Not in 4.35

New components. Docker on other platforms. Nginx. Those follow on this
base.

## 9. Build notes

- **Permission rule:** `dssa_check_definition_path` (`engine/plan.sh`)
  and `check_definition_path` (`bridge/components/loader.py`) apply the
  same rule. Planning checks the folder and every `*.json` in it. Apply
  checks the folder and each definition it sends. The Python loader
  checks the folder and each file. "Private group" means the group has
  the owner's name, has no listed members, and is nobody else's primary
  group.
- **Interrupted, not finished:** found while building. When the run
  process dies before writing `summary.json`, `runner_start` now returns
  `DSSA_STATUS interrupted` (before, it said `finished`), and `main.py`
  prints the next two commands.
- **List refresh:** the `refresh_lists apt` undo action is `ok` or
  `not refreshed`. A failed refresh adds a note but leaves the
  rollback `complete`.
- **dpkg hint:** added to the issues when the apt removal command fails.
- **Stage cleanup:** the staged copy is removed when the target refuses
  a plan that already ran, and staged files older than the retention
  period are removed at the start of each run.
- **Mock crash rule** (`crash_on_install`): kills the detached run's
  session, but only when the session leader is a DSSA run bundle, so a
  test can never kill the test runner.

