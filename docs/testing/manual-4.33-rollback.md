# Manual Test — Milestone 4.33 Journal and Rollback

**This test changes lab VMs.** Take VirtualBox snapshots of
**DSSA-Ubuntu24** (192.168.56.110) and **DSSA-Rocky1** (192.168.56.115)
first, so every change can be fully undone.

The helper `docs/testing/manual-4.33.sh` installs only one harmless
package, `tree`, through a test-only definition (never added to
`definitions/components`). Root steps run under `sudo` on the VM, which
asks for the `dssa-test` password itself. Run everything from the
repository root, in a real terminal.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | VirtualBox: snapshot both VMs | Safety net | Snapshots named e.g. `before-4.33` |
| 2 | `.venv/bin/python -m unittest tests.backend.test_journal_rollback -v` | Automated tests | `Ran 28 tests … OK` |
| 3 | `… status ubuntu24` | Read-only starting state | `tree: not installed`, `/var/lib/dssa: absent` |
| 4 | `… fail ubuntu24` | Install, failed verification, rollback | `failed_rolled_back`; tree removed |
| 5 | `… status ubuntu24` | Back to the snapshot | `tree: not installed` again |
| 6 | `… show ubuntu24` | Journal and snapshot on the VM | intent/done per step, `rollback_begin`/`rollback_end`, root-only files |
| 7 | `… install ubuntu24` | Successful journaled install | `installed`, verification `passed` |
| 8 | `… status ubuntu24` | Installed state | `tree: install ok installed` |
| 9 | `… fail rocky10` then `… status rocky10` | Same on DNF | `failed_rolled_back`; `tree: not installed` |
| 10 | VirtualBox: restore both snapshots | Clean lab | VMs back to `before-4.33` |

`…` stands for `bash docs/testing/manual-4.33.sh`.

## Result

Run by the project owner on 2026-11-15: **all steps passed**.

| # | Outcome |
|---|---|
| 2 | `Ran 28 tests … OK` |
| 3 | ubuntu24: `tree: not installed`, `/var/lib/dssa: absent` |
| 4 | Plan `caa7d819…`: `failed_rolled_back`; `remove_package tree: ok`, `daemon_reload: ok`, no issues |
| 5 | `tree: not installed`; `/var/lib/dssa` is `drwx------ root` |
| 6 | 10 journal entries (intent/done per step, `rollback_begin`/`rollback_end`); snapshot 706 packages, 555 units. **Finding:** `journal.jsonl` was `-rw-r--r--` under the default umask |
| fix | DSSA's own files are now created private with a per-file `umask 077` (the process umask is unchanged, so package scripts are unaffected); tests force `umask 022` and assert 0600/0700 |
| 7 | Plan `eb96f010…`: `installed`, verification `passed`, `upgraded_not_reverted: []` |
| 8 | `tree: install ok installed 2.1.1-2ubuntu3.24.04.2` |
| 8b | New transaction: `journal.jsonl`, `snapshot.json`, `result.json` `-rw-------`; `backups/`, `pkgsets/` `drwx------`; 8 journal entries, result `installed` |
| 9 | rocky10 plan `e19c41d1…`: DNF installed `tree-2.1.0-8.el10`, rollback removed only `tree.x86_64` (`Remove 1 Package`); `failed_rolled_back`, no issues; afterwards `tree: not installed` |
| 10 | Cleaned by hand (no snapshots): ubuntu24 `apt-get purge tree`, both `rm -rf /var/lib/dssa`; both show `tree: not installed`, `/var/lib/dssa: absent` |

Notes: journal step numbers are 0-based (`step 0`), the console counts
from 1 (`step 1/2`). The verification result is recorded in
`component_end`, not as a separate entry.
