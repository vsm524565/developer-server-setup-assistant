# Manual Test — Milestones 4.41–4.46 Combined, Through the Web UI

One run through the web UI covers the install record (4.41), uninstall and
adopt (4.42), updates (4.43), all or nothing (4.44) and the web UI itself
(4.46): the UI calls the same steps as the command line. 4.40 passed on
its own. 4.45 (managed files) is tested later with Nginx.

The UI runs on the laptop on 127.0.0.1, with its own state folder and its
own test master key. Adding ubuntu24 and ubuntu22 puts that key into
dssa-test's `authorized_keys` there; the last step removes it. On the VMs,
only `hello` and two test units change, and all of it is undone.

Shorthand:
- `H` = `bash docs/testing/manual-4.46.sh`
- `H37` = `bash docs/testing/manual-4.37.sh`
- `W` = `env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=$HOME/dssa-manual-4.46 DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.46/definitions .venv/bin/python -m bridge.webui`
- UI = http://127.0.0.1:8443/ (log in as `tester`)

| # | Do | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_webui` | Web UI tests | `Ran 18 tests … OK` (15 when first run) |
| 2 | `H setup`, then `W init --master-key $HOME/dssa-manual-4.46/master_ed25519`, then `W adduser tester` | Definitions, master key, UI user | `dssa-crashtest.json dssa-locktest.json git.json`; the key's fingerprint; `Added tester.` |
| 3 | In a second terminal: `W serve --host 127.0.0.1 --port 8443 --master-key $HOME/dssa-manual-4.46/master_ed25519` (leave it running) | 4.46 serve | `DSSA web UI on http://127.0.0.1:8443/` |
| 4 | UI: log in; **Add server** ubuntu24 (192.168.56.110, user dssa-test); the fingerprint must be `SHA256:3Hu43IczxoaEQFNFx0G4dtbFJug9Ih74FaXLlktK/AM`; confirm; dssa-test's password | 4.46 enrolment | the ubuntu24 page; `H keys ubuntu24`: `1` |
| 5 | UI: Servers → ubuntu24 + `dssa-locktest`, install → Make plan → Approve (`APPROVE`) → Apply; type the sudo password in the run page when asked | Install through the UI, sudo relayed | run page ends `Finished, exit code 0.`; the ubuntu24 page lists `dssa-locktest installed … hello 2.10-3build1` (4.41) |
| 6 | Terminal: `.venv/bin/python main.py --inventory config/inventory/lab-six.json installed ubuntu24` | CLI sees the same record | `dssa-locktest installed …`, `packages: hello …` |
| 7 | UI: ubuntu24 + `dssa-locktest`, **remove** → Make plan; approve (type `dssa-locktest`, then `APPROVE`); Apply | 4.42 removal | plan: `REMOVE`, `Removes: hello …`, `Reversible: …`; run exit 0; ubuntu24 page: nothing recorded; `bash docs/testing/manual-4.38.sh hello ubuntu24`: `hello: not installed` |
| 8 | UI: ubuntu24 + `git`, **adopt** → plan; approve (type `git`, `APPROVE`); Apply | 4.42 adopt | `ADOPT`; exit 0; ubuntu24 page: `git adopted` |
| 9 | UI: ubuntu24 + `git`, **update** → Make plan (do not approve) | 4.43 | `UPDATE` with exact versions, or `SKIP` "Up to date …" |
| 10 | `H37 units ubuntu24`; UI: ubuntu24 + `dssa-locktest` + `dssa-crashtest`, install, **all or nothing** → plan, approve, apply | 4.44 | plan shows `ALL OR NOTHING`; run exit 3; `dssa-crashtest failed_rolled_back`, `dssa-locktest undone_all_or_nothing`; `hello: not installed`; then `H37 cleanup ubuntu24` |
| 11 | UI: **Add server** ubuntu22 (192.168.56.111); then Servers → ubuntu22 + ubuntu24 + `git`, install → Make plan → Approve batch → Apply | 4.46 batch (decision 7) | one page with both plans and their hashes; both runs `exit code 0` one after the other; batch state `finished` |
| 12 | `cat $HOME/dssa-manual-4.46/audit.jsonl` | Audit | login, server_added (with fingerprints), plan, approve, apply lines with user `tester`; no password |
| 13 | Stop the UI (Ctrl+C), then `H unkey ubuntu24 ubuntu22` and `H keys ubuntu24 ubuntu22` | Clean up | `test key removed` twice, then `0` for both |

git stays recorded as adopted on ubuntu24 (record only; harmless).

## Result

Run by the project owner on 2026-11-20/21: **all 13 steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 15 tests … OK` |
| 2 | definitions set up; master key `SHA256:FZDr9g…`; `Added tester.` (two short passwords refused first) |
| 3 | `DSSA web UI on http://127.0.0.1:8443/`; login page |
| 4 | ubuntu24 added with `SHA256:3Hu43I…`; `H keys ubuntu24`: `1`. **Finding:** the server page then dropped the connection (method name clash with the HTTP handler); fixed, UI restarted |
| 5 | plan 39cb3008 installed `hello` through the UI, exit 0, logging in with the UI's own key (`from the inventory`) |
| 6 | CLI `installed ubuntu24`: `dssa-locktest … plan 39cb3008`, `hello 2.10-3build1` |
| 7 | plan 59c15b47 removed `hello`, exit 0; `hello: not installed`. **Finding:** apt downloaded the kept file outside its `_apt` sandbox; fixed |
| 8 | plan f60cdfdd adopted git, exit 0; record `git adopted … git 1:2.43.0-1ubuntu7.4` |
| 9 | plan 1424e97e: `git SKIP`, "Up to date …" (no update on the lists; the UPDATE path is covered by automated tests only) |
| 10 | plan 2491045a (ALL OR NOTHING): `dssa-crashtest failed_rolled_back`, `dssa-locktest undone_all_or_nothing`, exit 3; `hello: not installed`; record only `git adopted`; units removed afterwards |
| 11 | ubuntu22 added (`SHA256:9PEUTZ…`); batch of plans 0dd1837b (ubuntu24) and 1e84e899 (ubuntu22): both exit 0 in turn, state `finished` |
| 12 | 23 audit lines, user `tester`, fingerprints included, no password; audit, inventory and users files mode 600. **Finding:** the plan page still offered Run verification after the plan had run (a second run is refused by the server); fixed: "Already run" with Report only |
| 13 | test key removed: `ubuntu24: 0`, `ubuntu22: 0`; `/var/lib/dssa` is `drwx--x--x root` |

Not covered in the lab:
- **sudo relay:** `dssa-test` runs sudo without a password on the lab VMs (`sudo -n true` succeeds), so no run page asked for one. Typing the sudo password into the page is covered by the automated job test only.
- **4.43 apply:** no update was available, so only the plan side ran.
- **4.45:** managed files are tested with the Nginx component.
