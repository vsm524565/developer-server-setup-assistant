# Manual Test — Web UI Items 2 and 3 (temporary access, single approval)

What it adds (`docs/design/webui-temporary-access-and-approval.md`):
- connecting a server for this setup only, with a key that expires;
- approving with a ticked review box, applied only by the approver.

The test uses **ubuntu26** without saving it, and installs and then
removes `haproxy` (not in its record). ubuntu24 has CyberPanel since
4.48, which would hold haproxy. It needs a second web UI user for the
approver-only check.

Shorthand: `W` = `env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=$HOME/dssa-lab-4.47 .venv/bin/python -m bridge.webui`

| # | Do | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests` | Automated tests | `OK` |
| 2 | `W adduser tester2` (12+ characters, twice) | Second user | `Added tester2.` |
| 3 | `W serve --host 127.0.0.1 --port 8443 --master-key $HOME/dssa-lab-4.47/master_ed25519` (own terminal) | Server | `DSSA web UI on http://127.0.0.1:8443/` |
| 4 | Sign in as your usual user. **Add server**: name `u26temp`, host `192.168.56.112`, user `dssa-test`, tick lab, leave **Save** unticked. Compare the fingerprint, then enter dssa-test's password | Temporary connection | the Applications page says "This setup only, access ends HH:MM UTC" |
| 5 | On ubuntu26: `tail -1 ~/.ssh/authorized_keys` | The key line | starts `expiry-time="…",from="<laptop's address>",no-port-forwarding,…` and ends `dssa-temp-…` |
| 6 | `jq -r '.servers[].target_id' $HOME/dssa-lab-4.47/inventory.json` | Not saved | no `u26temp` |
| 7 | Choose **HAProxy**, action **Install**, tick "allow this draft", **Review plan** | Plan page | Server u26temp; Runs: haproxy; the table: haproxy INSTALL |
| 8 | Type `haproxy` in its name box but leave the review box unticked; **Approve plan** | Tick required | the browser refuses (the box is required) |
| 9 | Tick the box, **Approve plan** | Approval | "Approved by <you>." and an **Apply** button |
| 10 | In a private window, sign in as tester2; open the same plan URL; press **Apply** if shown | Owner and approver only | no Apply button, or "u26temp is another user's temporary server"; sign tester2 out |
| 11 | Back as yourself: **Apply**; type the sudo password if asked | Apply | `Finished, exit code 0.` |
| 12 | On ubuntu26: `grep -c dssa-temp- ~/.ssh/authorized_keys` | Removed after a successful run | `0` |
| 13 | Workspace | Gone | no "This setup only" list |
| 14 | Add `u26temp` again (unsaved). Choose HAProxy, action **Remove**, **Review plan**; type `haproxy`, tick, **Approve plan**, **Apply** | Removal with a typed name | `Finished, exit code 0.`; `dpkg -l haproxy` on ubuntu26 shows it is not installed (`rc` or no entry) |
| 15 | Add `u26temp` once more (unsaved), then **Sign out**; on ubuntu26 run step 12 again | Removed at sign-out | `0` |
| 16 | `grep temp_access $HOME/dssa-lab-4.47/audit.jsonl \| tail -6` | Audit | `temp_access_created`, and `temp_access_removed` with reasons `run finished` and `signed out`; no password |
| 17 | Stop the server (Ctrl+C); `W deluser tester2` | Clean up | `Removed tester2.` |

## Result

Passed 2026-11-26, steps 1–17 on ubuntu26 as `u26temp` (62 tests OK in step 1).
- Step 5: `expiry-time="202611260416",from="192.168.56.1",no-port-forwarding,
  no-agent-forwarding,no-X11-forwarding … dssa-temp-e17912c05db78693`.
- Steps 8–9: Approve was refused unticked, then "Approved by tester".
- Step 10: tester2 got no Apply; Report said "u26temp is another user's
  temporary server"; Applications said "No saved server named u26temp".
  **Gap:** tester2 could still view the plan page. It is fixed below.
- Steps 11 and 14: install plan 071b4eba and remove plan 87296627 both
  finished with exit 0; `dpkg -l haproxy` found no package.
- Steps 12 and 15: the key count was 0 after each run, and 1 then 0 around
  sign-out. The first attempt at step 15 skipped the reconnect; the audit
  log showed it, and the step was redone.
- Step 16: three created/removed pairs (run finished ×2, signed out); no
  password in the audit log.

Fixed after the run:
- A temporary server's plans and runs (plan page, run page, poll, Runs
  list) are visible only to its owner, also after its access has ended.
- Found while fixing that: any signed-in user could type into another user's
  run terminal (its sudo and confirmation prompts). Now only the user who
  started the run can type into it.
- Log out is audited.
- The Add server card no longer says the server is always saved.
