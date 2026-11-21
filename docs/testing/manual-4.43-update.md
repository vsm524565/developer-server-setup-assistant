# Manual Test — Milestone 4.43 Updates

What 4.43 adds: a normal plan reports available updates as information;
`plan --update` plans and applies exact versions, keeping the old package
files so a failure goes back.

Whether an update exists depends on the lab VMs' package lists, so step 2
looks first. ubuntu24 and alma10.

Shorthand: `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_unified_apply` | Automated tests | `OK` |
| 2 | `for t in ubuntu24 alma10; do M plan $t git docker; done` | Info only | each component `SKIP`; where the lists have a newer version: `Update available: … (plan --update to apply it).` |
| 3 | `M plan --update ubuntu24 git` | Update plan | `UPDATE` with `Update: git A -> B.` and `Reversible: …`, or `SKIP` with `Up to date …` (then skip step 4) |
| 4 | `M approve ID` (`APPROVE` only: git runs no service), `M apply ID` | Exact update | `git updated`, exit 0; the run log shows `git version B` |
| 5 | `M plan --update ubuntu24 docker` | A running service | `UPDATE` with `Restarts running service: docker.service; you must type the name to approve.`, or `SKIP`/`BLOCKED` (major version). **Do not approve**: Docker stays as it is |

## Result

Not run yet.
