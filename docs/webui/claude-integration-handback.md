# Claude Code handback: DSSA Web UI integration (for Codex review)

Reply to `docs/webui/claude-integration-handoff.md`, 2026-11-26. Branch:
`architecture/layered-platform`.
- Pass 1 and requirements items 2–3: commit `1aef69c`, pushed.
- The selection list (decision A): the commit that adds this file (after `1aef69c`).

## What was built

1. **Pass 1, as handed off.**
   - Sign-in lands on a Workspace with **Saved servers** and **Add server**.
     Enrolment is unchanged.
   - Each saved server has a catalog with search, categories, descriptions
     and the installed state from DSSA's install record. Rows are
     keyboard-operable links.
   - Planning goes through the existing planner, and the backend's
     held/blocked reasons are shown as-is.
2. **Requirements items 2 and 3** (follow-up assignments, decided by the
   owner 2026-11-25; `docs/design/webui-temporary-access-and-approval.md`).
   - **Server saving is optional.** Without it, DSSA installs a temporary
     key: `expiry-time` 60 minutes, `from=` the master, no forwarding,
     tagged `dssa-temp-…`. The key is scoped to its owner and is removed
     after a successful run, at sign-out, or by the sweeper.
   - **Approval** is a review tick plus typed names for risky and draft
     actions. Only the approver may apply. The plan-hash and host-key
     binding, the 30-minute expiry and single use are unchanged.
3. **Selection list (decision A, owner, 2026-11-26).**
   - Several applications, one action, one plan, one approval, one apply.
   - **This deliberately changes the handoff's "exactly one server,
     component, and operation".** It is still one server and one
     operation per plan; only the component count grows, capped at 25.
   - The selection is kept in the URL, so it survives filtering and opening
     a row.
   - Only actions that fit the selection are offered, and a mixed
     installed/not-installed selection is blocked.
4. **Fixes found in the lab runs.**
   - A temporary server's plans and runs are visible to its owner only
     (`App.may_see`).
   - **Only the user who started a run can type into its terminal.**
     Before, any signed-in user could answer another run's sudo or
     confirmation prompts, on any server.
   - Log out is audited.
   - Plan page: the summary shows **Changes** and **Verified only** (it
     used to list SKIP rows as runs); the "nothing runnable" message
     appears once; Report and Resume rollback appear only after a run.
5. **Definitions.** An optional `description` field (at most 160 characters)
   is validated in Bash and Python, and filled in for all 39 components.

## Changed paths

Code:
- `bridge/webui/app.py`: pages, routes, temporary servers, `may_see`,
  `make_app_plan`, plan page.
- `bridge/webui/enrol.py`: `temporary_access`, `end_temporary_access`,
  `discard_temporary_access`.
- `bridge/webui/auth.py`: `Sessions.active_users()`.
- `bridge/webui/static/app.js` and `style.css`.
- `bridge/components/run.py`: a `known_hosts` override; `DSSA_SSH_KEY` is
  dropped for temporary servers.
- `bridge/components/contracts.py` and `backend/linux/engine/definitions.sh`:
  `description`.
- `definitions/components/*.json`: 39 descriptions.

Tests:
- `tests/backend/test_webui.py`
- `tests/backend/test_component_definitions.py`
- `tests/backend/test_unified_apply.py`: only `WebFlowTests`. The
  `RemoveAdoptTests` hunks in the worktree are the owner's own and are not
  part of this work.

Docs:
- `docs/design/web-ui.md` (section 5, decision A)
- `docs/design/component-definitions.md`
- `docs/design/webui-temporary-access-and-approval.md`
- `docs/testing/manual-webui-pass1.md`
- `docs/testing/manual-webui-items-2-3.md`
- `docs/testing/manual-webui-selection.md`

Left untouched, as the handoff asked:
- the owner's `journal.sh`, `remove.sh`, `REQUIREMENTS.md`, `panels.md`,
  `test_components.py` and `test_panels.py` edits;
- the `RemoveAdoptTests` hunks;
- `secret-share/`.

## Tests

| Command | Result |
|---|---|
| `.venv/bin/python -m unittest tests.backend.test_webui < /dev/null` | Ran 59 tests, OK |
| `.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_component_definitions tests.backend.test_unified_apply.WebFlowTests < /dev/null` | Ran 91 tests, OK (before the selection list) |
| `.venv/bin/python -m unittest discover -s tests/backend -p 'test_*.py' < /dev/null` | Ran 918 tests, OK (skipped=6) |

The handoff asked for these checks; each is covered by unit tests:
- sign-in and navigation;
- populated and empty inventory;
- keyboard selection;
- the planner request;
- blocked-plan handling.

**Lab runs on the owner's VMs**, beyond the handoff's local-fixture
requirement, all passed 2026-11-26:
- `manual-webui-pass1.md`: 12 steps.
- `manual-webui-items-2-3.md`: 17 steps, ubuntu26 as an unsaved
  `u26temp`. It covered:
  - the key line's options;
  - the inventory left unchanged;
  - approver-only apply;
  - the key removed after the run and at sign-out;
  - the audit pairs.
- `manual-webui-selection.md`: 13 steps. haproxy and varnish were
  installed, then removed, together in one plan each.

## Gaps and points for review

1. **Decision A versus the handoff.** Please review the multi-component
   path as a deliberate scope change: `post_single_plan`, `make_app_plan`
   and `MAX_SELECTED`.
2. **The sudo prompt in the web UI** is covered by automated tests only.
   The lab user has passwordless sudo.
3. **Temporary access edge paths are unit-tested only:**
   - removal failing (`temp_access_remove_failed`, kept until expiry);
   - the sweeper ending access near expiry;
   - access ending after a web UI restart.
4. **Visibility.** Plans and runs on saved servers are shared with every
   signed-in user, which is the existing behaviour. Typing into a run is
   now restricted to the user who started it.
5. **Without JavaScript**, all four actions are offered for any selection,
   and the planner decides (SKIP or hold). Mixed selections are blocked
   only in the script.
6. **Not in this work:**
   - several servers in the new flow (the old form stays at `/batch`);
   - TLS or deployment on a real master server;
   - the panel access requirements (`docs/design/panels.md`, queued
     separately).
7. **Sessions live in memory.** A restart signs everyone out, and the
   sweeper then ends any temporary access.

## Commits

- `1aef69c`: workspace, catalog, temporary access, tick approval and lab
  fixes (pushed).
- The commit that adds this file: the selection list, plan-page fixes, the selection guide and
  this handback.

## Review round 1 (Codex, 2026-11-26): both findings fixed

1. **Apply could race with approval.**
   - `consume_approval` takes `expected_operator`. It reads the record's
     `approved_by` right before writing the single-use marker, and refuses
     without writing it when the operator differs.
   - Records are written once (exclusive `mkdir` and creates) and never
     rewritten, so nothing changes between that check and the marker.
   - The web UI passes the signed-in user. The CLI is unchanged (`None`).
   - Tests:
     - `ConsumeTests.test_only_the_expected_operator_claims_the_approval`:
       wrong operator refused, marker absent, right operator claims it;
     - `TemporaryServerTests.test_a_successful_run_ends_access_and_a_failed_one_keeps_it`:
       asserts `expected_operator`.
2. **Visibility followed the current server name.**
   - A plan made on a temporary server is recorded in
     `<home>/temporary-plans/<plan_id>.json` (0600, exclusive create).
     The record holds `{owner, connection, target}`, where `connection` is
     that connection's unique tag (`dssa-temp-<16 hex>`). These records are
     reloaded at start.
   - `may_see` checks this record first, before the current name; a run is
     judged by its plan.
   - `require_plan_owner` guards approve, apply, report and resume. A plan
     acts only through the same connection, and a saved server's plan never
     acts through a temporary connection that has the same name.
   - Tests:
     - `test_a_reused_name_never_inherits_plans_runs_or_approvals`: Bob
       reuses Alice's name; Bob is refused the plan page, run page, Runs
       list, approve, apply and report, and no login is attempted. After a
       restart Alice still owns the plan but can't act on it through
       another connection.
     - `test_a_saved_server_plan_never_acts_through_a_temporary_connection`.

Focused run after the fixes:

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 96 tests, OK.

Not run by Claude this round: the full suite and the lab guides. The owner
has moved that testing to Codex/ChatGPT (2026-11-26).
