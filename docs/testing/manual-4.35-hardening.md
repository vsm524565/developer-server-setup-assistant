# Manual Test — Milestone 4.35 Hardening

Changes lab VMs only temporarily: every test definition is built to
end with the VM as it started (`tree` and the test repository are
removed again). Targets: ubuntu24 (drop and crash), ubuntu22
(repository rollback).

Shorthand: `H` = `bash docs/testing/manual-4.35.sh`,
`D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.35/definitions`,
`M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_definition_permissions tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `Ran 80 tests … OK` |
| 2 | `H defs` | Test-only definitions | three files `-rw-r--r--` |
| 3 | `chmod o+w ~/dssa-manual-4.35/definitions/dssa-droptest.json`, then `D M plan ubuntu24 dssa-droptest`, then `chmod o-w` on the same file | Permission rule (C) | `ERROR: Definition path is writable by other users …`; only the read-only host-key connection happens, no plan is built |
| 4 | `H state ubuntu24` | Starting state | `tree: not installed` |
| 5 | `D M plan ubuntu24 dssa-droptest` → `D M approve ID` → `D M apply ID`; when `step 1/3` appears, in a **second terminal** run `H drop` (or type `~.` in the apply terminal) | Dropped connection (A) | terminal 2: `Cutting the laptop's SSH connection …`; terminal 1: output stops, `No result came back …`, hint to run `report`, `Exit code : 255` |
| 6 | wait ~30 s, then `D M report ID` | The run finished without the laptop | `Run state : finished`; `dssa-droptest failed_rolled_back`; `undo remove_package tree: ok` |
| 7 | `H state ubuntu24` | Clean again | `tree: not installed`; nothing staged |
| 8 | `D M plan ubuntu24 dssa-crashtest` → approve; in a **second terminal** start `H crash` first (sudo, then it waits); then apply in terminal 1 | Simulated crash (B) | terminal 2: `Install step done in run …; killing its session …`; terminal 1: `Killed`, `The run stopped before finishing …`, `Run state : interrupted` |
| 9 | `D M report ID` | Detected as unfinished | `Run state : interrupted` |
| 10 | `M plan ubuntu24 git`, then `M apply ID` (verification-only, no approval) | New runs blocked | `Unfinished transaction requires resume rollback: <crash plan ID>`; `Status: refused`; exit 2 |
| 11 | `D M resume-rollback CRASH_ID` | Undo from the journal alone | `dssa-crashtest failed_rolled_back`; `undo remove_package tree: ok`; exit 0 |
| 12 | `H state ubuntu24` | Clean again | `tree: not installed` |
| 13 | `H state ubuntu22` | Starting state | both `absent`; `apt list files for download.docker.com: 0` |
| 14 | `D M plan ubuntu22 dssa-repotest` → approve → apply | Repository rollback refreshes lists (D) | step 2 fetches `download.docker.com … jammy`; verification fails; undo `remove_file …dssa-repotest.sources`, `remove_file …dssa-repotest.asc`, `refresh_lists apt: ok` |
| 15 | `H state ubuntu22` | No stale lists | both `absent`; `apt list files for download.docker.com: 0` |

## Result

Run by the project owner on 2026-11-16/17: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 78 tests … OK` (80 after the fixes below) |
| 2 | three definitions `-rw-r--r--` |
| 3 | `ERROR: Definition path is writable by other users: …dssa-droptest.json (fix: chmod o-w)`; `Planning failed (exit 2)`; restored to `-rw-r--r--` |
| 4 | ubuntu24: `tree: not installed`, 2 Docker list files (4.34), nothing staged |
| 5 | Plan `1c23f90b…`: `H drop` cut the laptop's ssh during step 2/3 (`Killed "$SSH_BIN" …`); `No result came back …`, `Exit code : 255` |
| 6 | `report`: `Run state : finished`, `failed_rolled_back`, `remove_package tree: ok`. **The run finished on the VM without the laptop** |
| 7 | ubuntu24 clean |
| 8 | Plan `260d1c10…`: the `H crash` watcher killed the run session right after `done packages_install`; laptop journal stops at `intent package_metadata_refresh`, no result; audit `run.completed failed` exit 4 |
| 9 | `report`: `Run state : interrupted` with both next steps |
| 10 | Verification-only Git plan: `ERROR: Unfinished transaction requires resume rollback: 260d1c10…`, `Status: refused`, exit 2 |
| 11 | `resume-rollback`: `failed_rolled_back - Run was interrupted.`, `remove_package tree: ok`, exit 0 |
| 12 | ubuntu24 clean |
| 13 | ubuntu22: Docker list files `0`, `/var/lib/dssa: absent` |
| 14 | Plan `41dd28bd…`: `download.docker.com jammy` fetched; rollback `remove_file` source and key, `refresh_lists apt: ok`; existing `/etc/apt/keyrings` left in place |
| 15 | ubuntu22: Docker list files `0` again |

Found and fixed during the run:
- A killed local ssh returned 137 (looked like a plain failure). It is now
  reported as 255 (connection ended, completion unknown), and a killed run on
  the target reports 4 (needs review), so the two cannot be confused.
- After a killed ssh the terminal stayed in raw mode ("staircase" output).
  `dssa-run.sh` now runs `stty sane` after an abnormal ssh exit.
- A resume result printed `Status: unknown`; it now prints `Status: resumed`.
- Test tooling: `H drop` (cut the laptop's ssh from a second terminal)
  replaced typing `~.`; `H crash` now waits for the install step itself.
- Several plans were applied or approved twice (each command ran once
  unseen, then again when pasted). Every second attempt was refused by the
  single-use rules, and nothing ran twice on a server.
