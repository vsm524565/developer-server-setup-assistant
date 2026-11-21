# Manual Test — Milestone 4.40 JSON Output

What 4.40 adds: `main.py --json COMMAND` prints JSON Lines on stdout
(events, then one `result` line), and the usual text on stderr. Design:
`docs/design/json-output.md`.

Only verification-only git plans are used: nothing is installed.

Shorthand: `M` = `.venv/bin/python main.py --json --inventory config/inventory/lab-six.json`

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_unified_apply` | Automated tests | `OK` |
| 2 | `M targets 2>/dev/null \| jq -c '{command, ok, n: (.data.targets \| length)}'` | stdout is JSON only | `{"command":"targets","ok":true,"n":6}` |
| 3 | `M plan ubuntu24 git 2>/dev/null \| tail -1 \| jq -r '.data.plan.plan_id, .data.plan.runnable[0]'` | The plan in `data`; the table not on stdout | a plan ID, then `git` |
| 4 | `M approve ID < /dev/null` with the ID from step 3 | Typed answers never come from stdin | the plan table, `Verification-only plan: no approval is needed.`; last line has `"ok": true` and `"verification_only": true` |
| 5 | `M apply ID \| jq -c 'select(.type == "event" and .event != "log") \| .event'` | Events on stdout, live log (and any sudo prompt) on the terminal via stderr | the usual run text and `Exit code : 0` on the terminal; jq prints `"run.started"`, `"run.finished"` |
| 6 | `M plan nope git 2>/dev/null \| jq -c '{ok, exit_code, error: .error.message}'` | Errors end with a result | `{"ok":false,"exit_code":2,"error":"Planning failed (exit 3)."}` |

## Result

Run by the project owner on 2026-11-20: **all steps passed**.

| # | Outcome |
|---|---|
| 1 | `Ran 96 tests … OK` |
| 2 | `{"command":"targets","ok":true,"n":6}` |
| 3 | plan 687f36e7, `git` |
| 4 | table on stderr, `{"ok":true,"vo":true}` with stdin from /dev/null |
| 5 | `"run.started"`, `"run.finished"` on stdout; run text on the terminal; `git verified`, exit 0 |
| 6 | `{"ok":false,"exit_code":2,"error":"Planning failed (exit 3)."}` |
