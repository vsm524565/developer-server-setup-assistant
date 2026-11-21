# Manual Test — Milestone 4.44 All or Nothing

What 4.44 adds: `plan --all-or-nothing` makes any failure undo every change
of the run, the last one first.

ubuntu24, the 4.37 test definitions. `dssa-locktest` installs `hello`;
`dssa-crashtest` (a unit that crash-loops) fails its stability check, so
the run must undo `hello` too.

Shorthand:
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.37/definitions`
- `H37` = `bash docs/testing/manual-4.37.sh`

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_unified_apply` | Automated tests | `OK` |
| 2 | `H37 units ubuntu24` | Test units (re)created | `dssa-crash.service: inactive` |
| 3 | `D M plan --all-or-nothing ubuntu24 dssa-locktest dssa-crashtest` | Mode in the plan | `Mode      : ALL OR NOTHING …`; both `INSTALL` |
| 4 | `D M approve ID`, `D M apply ID` | The whole run is undone | `dssa-crashtest failed_rolled_back`; `dssa-locktest undone_all_or_nothing`; exit 3 |
| 5 | `bash docs/testing/manual-4.38.sh hello ubuntu24`, `M installed ubuntu24` | Nothing left | `hello: not installed`; no `dssa-locktest` in the record |
| 6 | `H37 cleanup ubuntu24` | Clean up | `test units removed` |

## Result

Not run yet.
