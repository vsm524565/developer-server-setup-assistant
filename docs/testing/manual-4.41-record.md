# Manual Test — Milestone 4.41 Install Record

What 4.41 adds: after DSSA installs and verifies a component, the server's
`/var/lib/dssa/installed.json` records it. `main.py installed TARGET` shows
the record without sudo. `/var/lib/dssa` becomes mode 0711.

Uses the 4.37 test definition `dssa-locktest` (installs `hello`) on
ubuntu24; step 6 removes `hello` again.

Shorthand:
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.37/definitions`

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `OK (skipped=1)` |
| 2 | `M installed ubuntu24` | Empty record, no sudo | `Nothing recorded yet.` (no sudo prompt) |
| 3 | `D M plan ubuntu24 dssa-locktest`, then `D M approve ID` | Install plan | `dssa-locktest INSTALL`; approved |
| 4 | `D M apply ID` | Install is recorded | `dssa-locktest installed`, exit 0 |
| 5 | `M installed ubuntu24`, then `ssh dssa-test@192.168.56.110 'stat -c "%a %U %n" /var/lib/dssa /var/lib/dssa/installed.json'` | The record and its modes | `dssa-locktest  installed …`, `packages: hello 2.10-3build1`; `711 root /var/lib/dssa`, `644 root /var/lib/dssa/installed.json` |
| 6 | `bash docs/testing/manual-4.38.sh purge ubuntu24` | Clean up | `hello removed` |

After step 6 the record still lists `dssa-locktest` (hello was removed by
hand, not by DSSA). That entry stays until DSSA's own uninstall (4.42)
removes it; it does no harm.

## Result

Not run yet.
