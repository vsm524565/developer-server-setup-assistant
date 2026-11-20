# Manual Test — Milestone 4.39 Engine Preflight Checks

What 4.39 changes:

- **Clock:** `plan` compares the server's clock with this computer's.
  - Over 60 s apart, or NTP not synchronized: a warning, and the plan
    still works.
  - Over 300 s apart: the plan is refused (exit 3).
- **Required commands:** `plan` and `apply` first check that the server
  has the commands DSSA needs, and name any that are missing.
- **State folder:** `apply` refuses `/var/lib/dssa` if a folder on its
  path is a symlink, or if it is not owned by root or is writable by group
  or others. DSSA never repairs it.
- **Version output:** an `executable` check may set `version_pattern`.
  The first line of the version output must match it; exit 0 alone is no
  longer enough. git and docker now carry patterns.

Only git is planned and verified (verification-only plans: no approval,
nothing installed). The VM changes in steps 6–11 are temporary, and each
is undone in the same or the next step: the clock, jq's name, and the mode
of `/var/lib/dssa`.

Shorthand:
- `H` = `bash docs/testing/manual-4.39.sh`
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.39/definitions`

Run every DSSA command in your own terminal.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_definition_planner tests.backend.test_component_definitions tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `Ran 184 tests … OK (skipped=1)` |
| 2 | `H defs` | Test definition | `dssa-gitbad.json` |
| 3 | `for t in ubuntu24 alma10; do M plan $t git; done` | Clock line, apt and dnf | each: `Clock on <vm>: … ; NTP synchronized: yes`, no `WARNING`; git `SKIP`, verification-only |
| 4 | `M apply ID` for each plan ID from step 3 | The git pattern matches real output | the run log shows `git version 2.…`; `verified`, exit 0 |
| 5 | `D M plan ubuntu24 dssa-gitbad`, then `D M apply ID` | A wrong pattern fails | run log: `Version output of git does not match "^git version 999\.": git version 2.…`; `verification_failed`, exit 3; nothing changed |
| 6 | `H skew ubuntu24 120`, then `M plan ubuntu24 git` | Warning | `WARNING: The clock on ubuntu24 is 1xx s ahead of this computer …` and `… not NTP-synchronized.`; the plan is still made |
| 7 | `H skew ubuntu24 -400`, then `M plan ubuntu24 git` | Refusal | `ERROR: The clock on ubuntu24 is ~400 s behind this computer (limit 300 s) …`; `Planning failed (exit 3)` |
| 8 | `H restore ubuntu24`, then `H clock ubuntu24` | Clock back | about `+0 s`, NTP `yes` (it may take a minute to show `yes`) |
| 9 | `M plan ubuntu24 git` (keep its ID), then `H hidejq ubuntu24`, then `M plan ubuntu24 git` again | Missing command at plan time | the second plan: `ERROR: Missing on this server: jq. Install it, then try again. Nothing was changed.`; `Planning failed (exit 3)` |
| 10 | `M apply ID` with the first ID from step 9, then `H restorejq ubuntu24` | Missing command at apply time | `Missing on this server: jq …`; exit 2 (refused before any change); then `restored: /usr/bin/jq` |
| 11 | `H statemode ubuntu24 775`, `M plan ubuntu24 git`, `M apply ID`, then `H statemode ubuntu24 700` | Unsafe state folder | `ERROR: Unsafe state folder: /var/lib/dssa is not root-only (uid mode: 0 775); nothing was run.`; exit 2; then `uid 0 mode 700` |
| 12 | `H state ubuntu24`, `H clock ubuntu24` | Lab clean | `uid 0 mode 700`, `jq: present`, about `+0 s`, NTP `yes` |

## Result

Run by the project owner on 2026-11-20: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 184 tests … OK (skipped=1)`; full suite 761/761, 1 skipped |
| 2 | `dssa-gitbad.json` written |
| 3 | `Clock on ubuntu24: same time as this computer`, `Clock on alma10: 1 s behind this computer`, both NTP `yes`, no warning; git `SKIP` |
| 4 | ubuntu24 `git version 2.43.0`, alma10 `git version 2.52.0`: both `verified`, exit 0 |
| 5 | plan 26bb83bb: `Version output of git does not match "^git version 999\.": git version 2.43.0`; `verification_failed`, exit 3 |
| 6 | `119 s ahead of this computer; NTP synchronized: no`, both warnings, plan b235e251 made |
| 7 | `The clock on ubuntu24 is 401 s behind this computer (limit 300 s)`; `Planning failed (exit 3)` |
| 8 | restored: `+1 s vs this computer, NTP synchronized: yes` |
| 9 | plan da7ada46 made; jq hidden; next plan: `Missing on this server: jq …`, exit 3 |
| 10 | apply da7ada46: `Missing on this server: jq …`, exit 2; jq restored |
| 11 | mode 775; apply b5a53ce4: `Unsafe state folder: /var/lib/dssa is not root-only (uid mode: 0 775); nothing was run.`, exit 2; mode 700 again |
| 12 | `uid 0 mode 700`, `jq: present`, `+0 s`, NTP `yes` |

Found during the run: when apply is refused on the server (exit 2, no
result file), `main.py` still prints "If the connection dropped, the run
may still be going on the server", which is misleading for a refusal.

## 4.39.1 — Clear text after a refused run

One check on ubuntu24, reusing step 11:

| # | Command | Expected |
|---|---|---|
| 1 | `H statemode ubuntu24 775`, `M plan ubuntu24 git`, `M apply ID`, then `H statemode ubuntu24 700` | after `Unsafe state folder …`: `The server refused the run before any change; the reason is shown above.` and `Nothing was run there. Fix the cause. Plan again, then apply.`; no `may still be going`; exit 2; then `uid 0 mode 700` |

### Result

Run by the project owner on 2026-11-20: **passed**. Plan da1b3998
(verification-only): `Unsafe state folder: /var/lib/dssa is not root-only
(uid mode: 0 775); nothing was run.`, then `The server refused the run
before any change; the reason is shown above.` and `Nothing was run there.
Fix the cause. Plan again, then apply.`; no "may still be going"; exit 2;
mode 700 restored. Full suite 763/763, 1 skipped.
