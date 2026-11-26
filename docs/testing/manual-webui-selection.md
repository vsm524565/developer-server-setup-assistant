# Manual Test — Web UI Selection List (decision A)

What it adds (`docs/design/web-ui.md`, decision A):
- tick several applications;
- choose one action for all of them;
- get one plan, one approval and one apply.

It also covers three plan-page fixes from pass 1: the Changes and Verified
only summary, the single "nothing runnable" message, and Report shown only
after a run.

It uses **ubuntu26** (saved, lab, no panel), installs `haproxy` and `varnish`
together, then removes them together. Both are drafts that aren't in
ubuntu26's install record.

Shorthand: `W` = `env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=$HOME/dssa-lab-4.47 .venv/bin/python -m bridge.webui`

| # | Do | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_webui < /dev/null` | Automated tests | `OK` |
| 2 | `W serve --host 127.0.0.1 --port 8443 --master-key $HOME/dssa-lab-4.47/master_ed25519` (own terminal) | Server | `DSSA web UI on http://127.0.0.1:8443/` |
| 3 | Sign in; Saved servers → ubuntu26 → Applications; tick **HAProxy** and **Varnish Cache** | Selection | **Selected (2)** lists both; **Install** is chosen; "allow the drafts in this selection" appears; Update and Remove are greyed out |
| 4 | Type `git` in Search, open the **Git** row | Selection kept | Selected (2) still lists HAProxy and Varnish; the address bar has `sel=haproxy&sel=varnish` |
| 5 | Clear the search; tick **Node.js** (installed) | One action per plan | the warning "Your selection mixes installed and not installed…"; **Review plan** greyed out. Untick Node.js: the warning goes |
| 6 | Tick "allow the drafts…", **Review plan** | One plan | Changes: `haproxy, varnish`; two INSTALL rows; two name boxes; no Report or Resume rollback buttons |
| 7 | Type `haproxy` and `varnish`, tick the review box, **Approve plan**, **Apply** (sudo on the run page if asked) | One approval, one apply | `Finished, exit code 0.` |
| 8 | Back to the plan page | After a run | "Already run: status …" and **Report** |
| 9 | ubuntu26 → Applications; tick HAProxy and Varnish | Installed state | both show **Installed**; only **Update** and **Remove** are offered |
| 10 | Choose **Remove**, **Review plan**; type both names, tick, **Approve plan**, **Apply** | Removal of both | `Finished, exit code 0.` |
| 11 | On ubuntu26: `dpkg -l haproxy varnish` | Cleaned up | neither is installed (`rc` or "no packages found") |
| 12 | `grep '"plan"' $HOME/dssa-lab-4.47/audit.jsonl \| tail -2` | Audit | two plan lines, each with `"components": ["haproxy", "varnish"]` (install, then remove) |
| 13 | Stop the server (Ctrl+C) | Clean up | |

## Result

Passed 2026-11-26, steps 1–13 on ubuntu26 (59 tests OK in step 1).
- Steps 3–5:
  - Selected (2) and the action limits worked.
  - The selection survived the live filter and a row reload.
  - Remove dropped Git.
  - Adding the installed Node.js showed the mixed warning and disabled
    Review plan.
- Step 6: one plan, fa5b304d. Changes: varnish, haproxy; two INSTALL rows;
  two name boxes; no Report or Resume rollback.
- Step 7: one approval and one apply, exit 0 (HAProxy 3.2.25).
- Step 8: "Already run: status completed (varnish: installed, haproxy:
  installed)" and Report only.
- Step 9: both rows showed Installed; Update was chosen; Install and Adopt
  were greyed out.
- Step 10: remove plan dbbb5eeb, exit 0. An earlier remove plan, 758be82b,
  was made but never approved, and expires unused.
- Step 11: `dpkg -l` found neither package.
- Step 12: the audit shows plan, approve and apply for fa5b304d and
  dbbb5eeb, each with both components.

Fixed after the run: the chosen app's hint ("Tick its box…" or "In your
selection") was set when the page loaded and didn't follow the checkbox.
The script now updates it.
