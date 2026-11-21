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

In progress (2026-11-20), steps 1–7 passed:

| # | Outcome |
|---|---|
| 1 | `Ran 15 tests … OK` |
| 2 | definitions set up; master key `SHA256:FZDr9g…`; `Added tester.` (two short passwords refused first) |
| 3 | `DSSA web UI on http://127.0.0.1:8443/`; login page |
| 4 | ubuntu24 added with `SHA256:3Hu43I…`, `H keys ubuntu24`: `1`. **Finding:** the server page then dropped the connection (method name clash); fixed, UI restarted |
| 5 | plan 39cb3008 installed `hello` through the UI, exit 0; logged in with the UI's own key (`from the inventory`) |
| 6 | CLI `installed ubuntu24`: `dssa-locktest … plan 39cb3008`, `hello 2.10-3build1` |
| 7 | plan 59c15b47 removed `hello`, exit 0; `hello: not installed`. **Finding:** apt downloaded the kept file outside its `_apt` sandbox; fixed |
