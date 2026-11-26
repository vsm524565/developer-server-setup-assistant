# Manual Test — Web UI Integration Pass 1 (workspace and catalog)

What it adds: after sign-in, a Workspace with **Saved servers** and
**Add server**; a searchable catalog per server; one application and one
action sent to the planner (`docs/design/web-ui.md`, section 5).

Everything here is read-only on the servers: it reads install records
and makes plans. Nothing is approved or applied.

Since 4.48, ubuntu24, debian13 and alma10 each have a control panel
(CyberPanel, CloudPanel, cPanel), so steps 6–9 use ubuntu26, which keeps
its 4.47 record (caddy, java, swap, nodejs).

It reuses the 4.47 web UI state folder (`~/dssa-lab-4.47`: its user,
inventory of the six lab VMs and master key).

Shorthand: `W` = `env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=$HOME/dssa-lab-4.47 .venv/bin/python -m bridge.webui`

| # | Do | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_component_definitions` | Automated tests | `OK` |
| 2 | `W serve --host 127.0.0.1 --port 8443 --master-key $HOME/dssa-lab-4.47/master_ed25519` (own terminal; leave running) | Server | `DSSA web UI on http://127.0.0.1:8443/` |
| 3 | Open `http://127.0.0.1:8443/`, sign in | Sign-in lands on the workspace | **Workspace** with two cards: Saved servers, Add server |
| 4 | **Add server** card | Enrolment kept | the familiar Name / Host / Port / SSH user form (don't submit) |
| 5 | **Saved servers** | Populated inventory | the six lab VMs, each with "Applications →" |
| 6 | ubuntu26 → Applications | Real installed state | "Installed state: from DSSA's install record…"; Caddy, Java, Swap file and Node.js show **Installed**; nothing shows "Managed by" (no panel on ubuntu26) |
| 7 | On ubuntu26, keyboard only: click into Search, type `maria`, press Tab until MariaDB, Enter | Search and keyboard selection | only MariaDB matches; after Enter it is highlighted, focus is on its name, the search still says `maria`, actions are **Install** and **Adopt** (not installed) |
| 8 | Category **Control panels** | Category filter | only the seven panels, each marked Draft |
| 9 | On ubuntu26 choose Node.js, action **Update**, **Review plan** | One operation reaches the planner | the plan page for ubuntu26, one row: nodejs, UPDATE or SKIP with the planner's reason |
| 10 | Saved servers → debian13 → Applications → `mongodb`, **Install**, tick "allow this draft", **Review plan** | Blocked plan | one row: mongodb BLOCKED, "MongoDB 8.0 requires: … detected debian 13"; the notice "Nothing in this plan can run on debian13"; no Approve or Apply button |
| 11 | `tail -3 $HOME/dssa-lab-4.47/audit.jsonl` | Audit | `plan` lines for steps 9 and 10, each with one component |
| 12 | Stop the server (Ctrl+C) | Clean up | nothing was approved or applied |

## Result

Passed 2026-11-26, steps 1–12 (78 tests OK in step 1).
- Step 6 first showed ubuntu24's 4.48 state: only CyberPanel recorded, and the
  other apps "Managed by CyberPanel". This was correct, so the guide moved to
  ubuntu26. It also showed the tag "Installed (installed)", now fixed to
  **Installed** / **Installed (adopted)**.
- Step 9: nodejs SKIP ("Up to date…"), so the page offered verification only.
- Step 10: mongodb BLOCKED on debian 13, with the notice; no Approve or Apply.
- Step 11: plan lines 8b05b796 (ubuntu26, nodejs) and f9a61163 (debian13,
  mongodb).

Follow-ups for the selection-list change (decision A):
- "Runs" in the summary lists SKIP rows.
- The red "Nothing in the plan is runnable." repeats the notice.
- Report and Resume rollback show on plans that never ran.
