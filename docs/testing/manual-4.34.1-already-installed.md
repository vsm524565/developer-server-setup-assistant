# Manual Test — 4.34.1 Already-Installed Packages Are Left Untouched

The finding from 4.34: `apt-get install` on prerequisites that were
already installed (`ca-certificates`, `curl`, `gnupg`) marked them
"manually installed". That mark is not in the snapshot, so rollback
could not restore it.

The fix: the install step asks apt or dnf only for **missing** packages.
Packages already present are listed in the journal (`already_installed`)
and left untouched: not re-marked, not upgraded.

`H` stands for `bash docs/testing/manual-4.34.1.sh`. Target: ubuntu24.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_journal_rollback -v` | Automated tests | `Ran 33 tests … OK` |
| 2 | `H marks` | Damage from the 4.34 run | the three packages show `manual` |
| 3 | `ssh -t dssa-test@192.168.56.110 'sudo apt-mark auto ca-certificates curl gnupg'` | Your decision: restore the original marks | three `… set to automatically installed.` lines |
| 4 | `H marks` | Restored | all three `auto`; `tree: not installed` |
| 5 | `H defs` | Test-only definition | `dssa-testpkg.json` written |
| 6 | `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.34.1/definitions .venv/bin/python main.py --inventory config/inventory/lab-six.json plan ubuntu24 dssa-testpkg` | Plan | `dssa-testpkg INSTALL` |
| 7 | the same `DSSA_DEFINITIONS_DIR=…` prefix, then `approve PLAN_ID`, then `apply PLAN_ID` | The fix, then rollback | `Already installed, left untouched: curl`; apt installs only `tree`; verification fails → `failed_rolled_back`, undo `remove_package tree` |
| 8 | `H marks` | Nothing else changed | `curl: auto` still; `tree: not installed` |

## Result

Run by the project owner on 2026-11-16: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 33 tests … OK` (5 new `AlreadyInstalledTests`) |
| 2 | `ca-certificates`, `curl`, `gnupg`: `manual` (left by the 4.34 Docker run); `tree: not installed` |
| 3 | Owner restored them: three `… set to automatically installed.` |
| 4 | All three `auto`; `tree: not installed` |
| 5 | `dssa-testpkg.json` written to `~/dssa-manual-4.34.1/definitions` |
| 6 | Plan `6714efcb…`: same host key `3Hu43Icz…`; `dssa-testpkg INSTALL` |
| 7 | `Already installed, left untouched: curl`; apt installed only `tree` (no "manually installed" line); verification failed on purpose → `failed_rolled_back`, `remove_package tree: ok`, exit 3. Journal intent: `already_installed: ["curl"]` |
| 8 | `curl: auto` (unchanged), all three `auto`; `tree: not installed` |

Noted for later: the helper's test definition was written `-rw-rw-r--`
(shell umask). DSSA does not yet check definition file permissions;
a possible hardening item.
