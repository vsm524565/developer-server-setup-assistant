# Manual Test — Milestone 4.34 Unified Plan → Approve → Apply

**This test changes lab VMs**: Git on rocky10, Docker on ubuntu24. The
VMs can be reset afterwards.

Run everything from the repository root, in a real terminal (the remote
`sudo` prompt needs it). `INV` below stands for
`--inventory config/inventory/lab-six.json`, and `S` stands for
`bash docs/testing/manual-4.34.sh status`.

| # | Command | Checks | Expected |
|---|---|---|---|
| 0 | Edit `config/inventory/lab-six.json`: add `"lab": true` to **ubuntu24** and **rocky10** only | You decide which targets are lab targets | `S ubuntu24` shows `lab flag in inventory: true` |
| 1 | `.venv/bin/python -m unittest tests.backend.test_unified_apply -v` | Automated tests | `Ran 25 tests … OK` |
| 2 | `S rocky10` | Starting state | note whether `git` is installed |
| 3 | `.venv/bin/python main.py INV plan rocky10 git` | Read-only plan; the host key comes from the live session | Plan shown with its ID; Git `INSTALL` (or `SKIP` if already installed); `Plan saved` |
| 4 | `.venv/bin/python main.py approve PLAN_ID` | Approval (4.32) | Type `APPROVE` → `Approved … (single use)`. A SKIP-only plan says no approval is needed |
| 5 | `.venv/bin/python main.py apply PLAN_ID` | One sudo prompt, journaled apply, laptop copy | `sudo` asks on rocky10; live log; `git installed` (or `verified`); `Exit code : 0`; `Saved copy: …/runs/PLAN_ID` |
| 6 | `.venv/bin/python main.py apply PLAN_ID` again | Single use | `Apply refused: … already been used` |
| 7 | `.venv/bin/python main.py report PLAN_ID` | Fetch the result later | `Run state : finished`, same results |
| 8 | `S rocky10` | Outside view | `git` installed; nothing staged |
| 9 | `.venv/bin/python main.py INV plan ubuntu24 docker` | Draft is held | Docker `REVIEW` (draft), `Runnable : none` |
| 10 | `.venv/bin/python main.py INV --allow-draft plan ubuntu24 docker` | Lab-only draft, typed name | Docker `INSTALL`; type `docker`; `Plan saved` |
| 11 | approve, then apply that plan | Live Docker install with vendor repo | `docker installed`; `verify_docker_daemon` prints the server version; `Exit code : 0` |
| 12 | `S ubuntu24` | Outside view | `docker-ce … installed`, `docker.service: active` |
| 13 | Reset the VMs (or keep Docker on ubuntu24 if you want it for later milestones) | Clean lab | — |

After step 11 passes, a separate commit marks Docker `supported`
(decision C).

## Result

Run by the project owner on 2026-11-15: **all steps passed**.

| # | Outcome |
|---|---|
| 0 | `"lab": true` added to ubuntu24 and rocky10 by the owner |
| 1 | `Ran 25 tests … OK` |
| 2 | rocky10: `git: not installed`, `/var/lib/dssa: absent` |
| 3 | Plan `dd0f2efb…`: presented key `SHA256:S/NtBX…` bound; `git INSTALL`, `Runnable : git` |
| 4 | `Approved by vivek … (single use)` |
| 5 | One sudo prompt; `git version 2.52.0`; `Status: completed`, `git installed`, exit 0. Laptop copy: 5 files `-rw-------`, journal 8 entries. **Finding:** the parent `runs/` folder was `drwxrwxr-x`; fixed (each level made private; test runs under umask 002) |
| 6 | `Apply refused: … Approval has already been used.` (no connection) |
| 7 | `report`: `Run state : finished`; `runs/` now `drwx------` |
| 8 | `git-2.52.0-1.el10.x86_64`; `/var/lib/dssa` root-only; nothing staged |
| 9 | ubuntu24 Docker plan without `--allow-draft`: `REVIEW`, `Runnable : none`, `Held : docker`, no conflicts |
| 10 | With `--allow-draft`: `INSTALL` with the operator reason. Typing `DRAFT` discarded the plan; typing `docker` saved plan `2de41193…` |
| 11 | Approved; apply: key fingerprint checked, `download.docker.com` repo added, `0 upgraded, 7 newly installed`, `Docker version 29.9.3`, `docker: daemon answering, server version 29.9.3`, exit 0; a second apply refused |
| 12 | `docker-ce 5:29.9.3-1~ubuntu.24.04~noble`, `docker.service: active`, nothing staged |
| 13 | Kept on purpose: Docker on ubuntu24 and Git on rocky10, as a base for the next milestones |

Findings for later:
- `apt-get install` of already-installed prerequisites (`ca-certificates`,
  `curl`, `gnupg`) marked them "manually installed". The packages are
  unchanged, but apt's auto/manual mark is not in the snapshot, so a
  rollback would not restore it. Possible fix: skip already-installed
  packages in the install step (needs the owner's decision).
- Wording fixed after the run: the draft prompt now names the exact word
  to type, and `report` / `resume-rollback` print their own exit meaning.
