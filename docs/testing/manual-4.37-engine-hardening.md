# Manual Test — Milestone 4.37 Engine Hardening

What 4.37 changes in the engine on the target:

- **apt lock timeout:** every `apt-get` gets `-o DPkg::Lock::Timeout=60`.
  When another process holds dpkg's lock, the step fails after 60 s and
  rolls back, instead of waiting forever.
- **Refresh errors:** `apt-get update` runs with `--error-on=any`. If a
  repository's signature can't be verified, the refresh step fails,
  instead of apt quietly installing from the old package lists.
- **Service stability check:** after `service_start`, the engine watches
  the unit for 15 s. The unit must stay active/running with the same main
  process and an unchanged restart count. Otherwise the step fails, the
  unit's last 20 journal lines go into the run log, and the run rolls
  back.
- **Log location:** the audit logs on the laptop follow `XDG_STATE_HOME`,
  the same way the approvals already do.

Only test units and the small `hello` package are used. All test units
are removed in the last step.

Shorthand:
- `H` = `bash docs/testing/manual-4.37.sh`
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.37/definitions`

Each plan, approve and apply is run **once**, one step at a time.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_definition_planner tests.backend.test_component_definitions tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `Ran 152 tests … OK (skipped=1)` |
| 2 | `H aptcheck ubuntu22`, then `ubuntu24`, `ubuntu26`, `debian13` | Every apt VM accepts the new flags | apt version line, then `refresh exit: 0` on all four |
| 3 | `H defs` | Test definitions | three files: `dssa-stabletest`, `dssa-crashtest`, `dssa-locktest` |
| 4 | `H units ubuntu24` | Test units created | `dssa-stable.service: inactive`, `dssa-crash.service: inactive` |
| 5 | `D M plan ubuntu24 dssa-stabletest` → approve → apply | Stability check on a healthy service | apply pauses about 15 s after the start; `dssa-stabletest` installed, verification passed, exit 0 |
| 6 | `H state ubuntu24` | Outside view | `dssa-stable.service: active (restarts 0)` |
| 7 | `D M plan ubuntu24 dssa-crashtest` → approve → apply | A crash loop is caught | run log: `Service dssa-crash.service is not stable after …s: …` and `Recent journal for dssa-crash.service:` with its lines; rollback stops the unit; `failed_rolled_back`, exit 3 |
| 8 | `H state ubuntu24` | Rolled back | `dssa-crash.service: inactive` (or `failed`), not `active` |
| 9 | `D M plan ubuntu24 dssa-locktest` → approve. Then, in a **second terminal**, `H lock ubuntu24 150`. Once it prints `holding …`, apply in the first terminal | apt gives up instead of hanging | apt waits on `/var/lib/dpkg/lock-frontend` for about 60 s, then fails with `Could not get lock`; `failed_rolled_back`, exit 3; the lock holder is still holding when apply ends |
| 10 | `H state ubuntu24` | Nothing installed | `hello: not installed` |
| 11 | `H cleanup ubuntu24` | Lab clean again | `test units removed` |

## Result

Run by the project owner on 2026-11-19/20: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 152 tests … OK (skipped=1)`; full suite 729/729, 1 skipped |
| 2 | `refresh exit: 0` on ubuntu22 (apt 2.4.15), ubuntu24 (2.8.4), ubuntu26 (3.2.1), debian13 (3.0.3) |
| 3–4 | definitions written; both test units `inactive` |
| 5 | plan e68dca36: start done after 16 s, journal `{"stable": true, "seconds": 15, "main_pid": "7498", "restarts": 0}`, exit 0 |
| 6 | `dssa-stable.service: active (restarts 0)` |
| 7 | plan bebd6539: `not stable after 4s: state activating/auto-restart`, unit journal in the log, undo `stop dssa-crash.service: ok`, exit 3 |
| 8 | `dssa-crash.service: inactive` |
| 9 | plan 2ce5ab77: apt waited 63 s on `lock-frontend`, then `Unable to acquire the dpkg frontend lock`; rolled back, exit 3 |
| 10 | `hello: not installed` (`dssa-stable` inactive: ubuntu24 had restarted overnight; the unit was started, never enabled) |
| 11 | `test units removed` |

Found during the run, outside 4.37's scope:
- DSSA's SSH login works only from the owner's own terminal. Through the
  app's `!` shell, `dssa-run.sh` (`-i ~/.ssh/id_ed25519`,
  `IdentitiesOnly=yes`) is refused, while plain ssh logs in with the agent's
  `devops-lab` key.
- That refused apply (plan 62a84594) still consumed the single-use approval,
  although nothing was staged.
