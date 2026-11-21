# Manual Test — Milestone 4.46 Web UI

What 4.46 adds: the web UI (`docs/design/web-ui.md`). This first test runs
it on the laptop on 127.0.0.1 with a separate state folder, against the
lab VMs. Running it on a real master server with a certificate comes
later, when you say so.

The UI gets its own master key in a test folder, so your own keys and
inventory are not touched. Adding ubuntu24 installs that test key into
dssa-test's `authorized_keys` on ubuntu24; step 9 removes it again.

Shorthand: `W` = `env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=$HOME/dssa-manual-4.46 .venv/bin/python -m bridge.webui`

(`-u DSSA_SSH_KEY`: your terminal sets it, and it would override the UI's own master key.)

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests` | Automated tests | `OK` |
| 2 | `W init --master-key $HOME/dssa-manual-4.46/master_ed25519` | State folder and test master key | `State folder: …/dssa-manual-4.46`, `Master key  : …/master_ed25519`, its fingerprint |
| 3 | `W adduser tester` (a password of 12+ characters, twice) | UI user | `Added tester.` |
| 4 | `W serve --host 127.0.0.1 --port 8443 --master-key $HOME/dssa-manual-4.46/master_ed25519` (own terminal; leave running) | Plain HTTP on loopback only | `DSSA web UI on http://127.0.0.1:8443/` |
| 5 | Open `http://127.0.0.1:8443/`, log in as tester | Login | the Servers page, empty |
| 6 | **Add server**: name `ubuntu24`, host `192.168.56.110`, user `dssa-test`; compare the fingerprint with `SHA256:3Hu43IczxoaEQFNFx0G4dtbFJug9Ih74FaXLlktK/AM`; confirm; enter dssa-test's password | Enrolment | the ubuntu24 page: "Nothing recorded yet." or the record |
| 7 | Servers page: tick ubuntu24 and `git`, operation install, **Make plan**; then **Run verification** | Plan and a terminal run | `git SKIP`; the run page shows the log and `Finished, exit code 0.` (type the sudo password in the page if asked) |
| 8 | `cat $HOME/dssa-manual-4.46/audit.jsonl` | Audit | lines for login, server_added (with the fingerprint), plan, apply; no password anywhere |
| 9 | Stop the server (Ctrl+C). Then remove the test key: `ssh dssa-test@192.168.56.110 "sed -i '/dssa-master\$/d' ~/.ssh/authorized_keys"` | Clean up | `grep -c dssa-master ~/.ssh/authorized_keys` on ubuntu24 prints `0` |

## Result

Not run yet.
