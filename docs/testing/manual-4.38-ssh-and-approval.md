# Manual Test — Milestone 4.38 SSH Key and Approval Fixes

What 4.38 changes:

- **SSH key per target:** DSSA picks the key from `DSSA_SSH_KEY` if set,
  else from the target's `"ssh_key"` in the inventory, else
  `~/.ssh/id_ed25519`. When a key is refused, the error names it.
- **Login check first:** `apply` logs in once, without sudo, before it
  uses the approval. A refused key or an unreachable server no longer
  uses up the approval.
- **Exit 5:** a failed bundle upload exits 5 ("nothing was run there")
  instead of 255 ("completion unknown"). This is covered by the
  automated tests only, since it can't be triggered safely on a VM.

The only change to a VM is that the `hello` package gets installed on
ubuntu24, and step 9 removes it again. **You** edit the inventory in
step 4.

Shorthand:
- `H` = `bash docs/testing/manual-4.38.sh`
- `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`
- `D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.37/definitions` (the 4.37 test definitions)

Each plan, approve and apply is run **once**, one step at a time.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_unified_apply` | Automated tests | `Ran 41 tests … OK` |
| 2 | `H env` in the terminal you use for DSSA | Why DSSA worked from there | shows whether `DSSA_SSH_KEY` is set there; `XDG_STATE_HOME=unset` |
| 3 | `env -u DSSA_SSH_KEY .venv/bin/python main.py --inventory config/inventory/lab-six.json plan ubuntu24 git` | A refused key is named | `ERROR: SSH key /home/vivek/.ssh/id_ed25519 (default) was refused by ubuntu24 (dssa-test@192.168.56.110).` plus the `Set "ssh_key"…` hint; `Planning failed (exit 3)` |
| 4 | You add `"ssh_key": "~/.ssh/devops_lab_ed25519"` to each of the six targets in `config/inventory/lab-six.json` (keep it mode 600); then `H keys` | Inventory key | `inventory mode: 600`; all six show `~/.ssh/devops_lab_ed25519  exists` |
| 5 | Step 3's command again | The inventory key works with no variable set | `Host key presented by 192.168.56.110: …`, a plan, exit 0 |
| 6 | `env -u DSSA_SSH_KEY D M plan ubuntu24 dssa-locktest` → approve | An install plan to apply | `dssa-locktest INSTALL`; approved |
| 7 | Apply with a wrong key forced: `DSSA_SSH_KEY=$HOME/.ssh/id_ed25519 D M apply ID` | Refused login keeps the approval | `SSH key … (from DSSA_SSH_KEY) was refused…`, then `Apply refused: cannot log in to the server. The approval was not used.`, exit 2 |
| 8 | `env -u DSSA_SSH_KEY D M apply ID` (same ID) | The same approval still works | `Login to ubuntu24 … works with /home/vivek/.ssh/devops_lab_ed25519 (from the inventory).`; `dssa-locktest installed`, exit 0 |
| 9 | `H hello ubuntu24`, then `H purge ubuntu24` | Clean up | `hello: install ok installed …`, then `hello removed` |

## Result

Run by the project owner on 2026-11-20: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 41 tests … OK`; full suite 738/738, 1 skipped |
| 2 | Owner's terminal: `DSSA_SSH_KEY=/home/vivek/.ssh/devops_lab_ed25519` (the app's `!` shell: unset). This explains 4.37's first finding: DSSA only worked where the variable was set |
| 3 | `SSH key /home/vivek/.ssh/id_ed25519 (default) was refused by ubuntu24 (dssa-test@192.168.56.110).` plus the hint; `Planning failed (exit 3)` |
| 4 | `inventory mode: 600`; all six targets `~/.ssh/devops_lab_ed25519  exists` |
| 5 | plan cc6379a8 with no variable set: host key read, git `SKIP` (already installed) |
| 6 | plan 878203a8: `dssa-locktest INSTALL`; approved |
| 7 | `… (from DSSA_SSH_KEY) was refused …`, `Apply refused: cannot log in to the server. The approval was not used.`; approval record had no `consumed` marker afterwards |
| 8 | same approval: `dssa-locktest installed`, exit 0 |
| 9 | `hello: install ok installed 2.10-3build1`, then `hello removed` |
