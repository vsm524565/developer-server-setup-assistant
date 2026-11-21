# Manual Test — Milestone 4.42 Uninstall and Adopt

What 4.42 adds:
- `plan --remove` removes only what DSSA installed (the install record),
  keeps data, and keeps the removed versions' package files for undo.
- `plan --adopt` records an existing install, so DSSA may later remove it.
- Approving either needs the component's name typed, then `APPROVE`.

Run after the 4.41 test. ubuntu24 only. `hello` is installed and removed
again; git is adopted (recorded only, nothing changes).

Shorthand:
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.37/definitions`

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `OK (skipped=1)` |
| 2 | `M installed ubuntu24` | Starting point | `dssa-locktest` listed (from 4.41; hello was purged by hand) |
| 3 | `D M plan --remove ubuntu24 dssa-locktest` | Packages already gone | `Operation : REMOVE`; `dssa-locktest REMOVE`; `Its packages are already gone; only the record and DSSA files are removed.` |
| 4 | `D M approve ID` (type `dssa-locktest`, then `APPROVE`), then `D M apply ID` | Typed name; record cleaned | `removed`, exit 0; `M installed ubuntu24`: `Nothing recorded yet.` |
| 5 | `D M plan ubuntu24 dssa-locktest`, approve, apply | Fresh install, recorded | `dssa-locktest installed`; `M installed ubuntu24` lists `packages: hello …` |
| 6 | `D M plan --remove ubuntu24 dssa-locktest` | A real removal plan | `Removes: hello 2.10-3build1.`; `Reversible: the installed package files are kept before removal.` |
| 7 | `D M approve ID` (type `dssa-locktest`, `APPROVE`), `D M apply ID` | Removal | `dssa-locktest removed`, exit 0; `bash docs/testing/manual-4.38.sh hello ubuntu24`: `hello: not installed` |
| 8 | `M plan --remove ubuntu24 git` | Not installed by DSSA | `git BLOCKED`, `Not installed by DSSA on this server …` |
| 9 | `M plan --adopt ubuntu24 git`, approve (type `git`, `APPROVE`), apply | Adopt | `git ADOPT`; `adopted`, exit 0; `M installed ubuntu24`: `git adopted …` |
| 10 | `M plan --remove ubuntu24 git` | An adopted component can be removed | `git REMOVE` with `Removes: git …` (**do not approve**: git stays) |

## Result

Not run yet.
