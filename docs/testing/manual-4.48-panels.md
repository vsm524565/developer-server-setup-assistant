# Manual Test — Milestone 4.48 Control Panels (fresh VMs)

What 4.48 adds: DSSA installs a control panel with the vendor's own
installer, on a fresh server only (docs/design/panels.md). Every panel is a
draft: plan with `--allow-draft` on a lab target, type the panel's name,
then `APPROVE`.

**Needs fresh VMs.** The fresh-server check blocks a panel on the current
lab VMs, because they already have Docker, git and other software. Use a
clone or a snapshot of a clean install per panel (a panel cannot be
removed), add it to the inventory with `"lab": true`, and give it a fully
qualified host name (`hostname -f` with a dot) for HestiaCP and cPanel.

Take a VM snapshot before each install: there is no undo.

Shorthand: `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`

| # | Do | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_panels` | Automated tests | `Ran 10 tests … OK` |
| 2 | On a current lab VM: `M plan --allow-draft ubuntu24 aapanel` | Fresh-server check | `BLOCKED`: "DSSA has already installed components here …" and/or "Already installed, a panel needs a fresh server: …" |
| 3 | Fresh Ubuntu 24.04 VM: `M plan --allow-draft <vm> aapanel` | Plan on a fresh server | `INSTALL` with "Fresh-server checks passed." and "Not reversible: take a VM snapshot first …" |
| 4 | `M approve ID` (type `aapanel`, then `APPROVE`), `M apply ID` | Install | the run log shows `Vendor installer … bytes, sha256:…`, the installer output with `password: [hidden by DSSA]`, `aapanel installed`, exit 0; `bt default` on the VM shows the login |
| 5 | `M installed <vm>` and `M plan --remove <vm> aapanel` | Record and no removal | `aapanel installed`; removal `BLOCKED`: "… Reinstall the OS or restore the VM snapshot." |
| 6 | On the panel VM: `M plan --allow-draft <vm> nginx`, then `M plan --allow-draft <vm> swap git` | Owner split | nginx `REVIEW`: "Managed by aapanel; use its own tools …"; swap and git `INSTALL` (or SKIP) |
| 7 | Repeat 3–4 per panel on its own fresh VM: cyberpanel (Ubuntu 24 / Alma 10), hestia (Debian 13 or Ubuntu 24; password in `/root/.dssa/hestia-admin-password`), cloudpanel (Debian 13; create the admin in the browser at once), cpanel (Alma 10; 25 GiB free), plesk (Ubuntu 24 or Alma 10; 1 GiB swap), directadmin (Alma 10; 4 GiB RAM + 4 GiB swap; finish in its web page) | Each vendor installer unattended | installed, verified, exit 0. Note any installer that prompted or failed: its flags need adjusting |
| 8 | Restore each VM's snapshot | Lab back to its start | — |

Also confirm in steps 4 and 7: aaPanel's `-y`, and DirectAdmin's
`DA_WEB_INSTALL=yes` (both read from secondary sources).

## Result

Not run yet.
