# JSON Output — Milestone 4.40

`main.py --json COMMAND ...` makes every command machine-readable, so a web
UI or a service can drive the same steps as the command line (application
flow, decision 6). `--json` goes before the command:

```bash
.venv/bin/python main.py --json --inventory config/inventory/lab-six.json plan ubuntu24 git
```

## Streams

- **stdout:** JSON Lines and nothing else. Zero or more `event` lines, then
  exactly one `result` line, always last, also after an error.
- **stderr:** everything meant for a person: the plan table, warnings, and
  during `apply`/`report`/`resume-rollback` the server's live terminal
  (including the sudo prompt).
- **Exit codes:** unchanged (0, 2, 3, 4, 5, 255); `result.exit_code`
  repeats them.

## Documents (schema_version "1.0")

Event:

```json
{"schema_version": "1.0", "type": "event", "event": "run.started",
 "data": {"plan_id": "…", "target": "ubuntu24"}}
```

Result:

```json
{"schema_version": "1.0", "type": "result", "command": "apply",
 "ok": true, "exit_code": 0, "data": {…}, "error": null}
```

- `ok` is `exit_code == 0`.
- `error` is `null`, or `{"message": "…", "issues": ["…"]}` when the command
  was refused or could not complete. A run that finished with failed
  components (exit 3) has `ok: false`, its `summary` in `data`, and may have
  `error: null`.

## Events

| Event | Command | `data` |
|---|---|---|
| `plan.saved` | plan | `plan_id`, `target` |
| `login.check` | apply (approved plans) | `target` |
| `approval.consumed` | apply | `plan_id` |
| `run.started` | apply | `plan_id`, `target` |
| `log` | apply, report, resume-rollback | `line`: one line of the server's terminal (markers stay hidden) |
| `run.finished` | apply | `plan_id`, `exit_code` |

## `data` per command

| Command | Keys |
|---|---|
| `targets` | `targets`: the inventory targets |
| `plan` | `plan` (the plan document), `plan_path`, `plan_sha256` |
| `approve` | `plan_id`, `verification_only`, `approval` (the record, or `null`) |
| `apply` | `plan_id`, `target`, `verification_only`, `approval_used`, `run_status`, `summary`, `files`, `saved_copy` |
| `report`, `resume-rollback` | `plan_id`, `target`, `run_status`, `summary`, `files`, `saved_copy` |
| `installed` (4.41) | `target`, `record`: the install record (`schema_version`, `components`) |

## People still confirm

- Typed answers (`APPROVE`, a draft's name) are read from the terminal
  itself (`/dev/tty`), never from stdin. Without a terminal, the step is
  refused with exit 2. So `--json` cannot be used to approve from a script.
  The web UI gets its own approval screen (application flow, decision 6).
- The guided session (no command) is refused with `--json`.
- `sudo` still asks on the server's own terminal.
