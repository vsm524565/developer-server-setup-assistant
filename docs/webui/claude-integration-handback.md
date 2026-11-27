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

## Review round 2 (`docs/webui/claude-authorization-follow-up.md`)

### 1. Temporary plans made before ownership records

`App.legacy_plan_owners()` decides from this web UI's audit log. It writes
nothing. Every stored plan that has no record is classified:

| Evidence | Record | Who sees it | Actions |
|---|---|---|---|
| Inside exactly one `temp_access_created`→`removed` interval of its name (or up to `expires` if never closed), and the audit `plan` line is by that interval's owner | `{owner, connection: null, source: "legacy-audit"}` | the owner only | none: "made on an earlier temporary connection … before ownership was recorded. Make a new plan." |
| Inside an interval otherwise: no web `plan` line (e.g. a CLI plan), a different user, overlapping intervals, or a target that differs from the plan line | `{owner: null, source: "ambiguous"}` | nobody | none |
| Outside every interval, for a server that isn't saved now (including no audit at all) | `ambiguous` | nobody | none |
| Outside every interval, for a saved server | no record | everyone signed in, as before | as before |

How the upgrade runs:
- `App.upgrade_legacy_plans()` runs when the web UI starts, and on demand:

  ```bash
  python -m bridge.webui plan-owners [--dry-run]
  ```

- Each record is written with an exclusive create, 0600, and fsync'd. An
  existing record is never changed. Each one is audited
  (`plan_owner_upgraded`). A second run writes nothing.

How the rules use the records:
- `may_see` checks the record first. A plan without a record is a saved
  server's plan and is shared. It is never visible to its name's current
  temporary owner.
- `require_plan_owner` refuses `owner: null` and `connection: null`.

**Fixture run on a copy of the lab state.** The copy held the audit log,
the inventory and the 102 stored plans, and no keys. The lab itself was not
touched.

```bash
env -u DSSA_SSH_KEY DSSA_WEBUI_HOME=<copy>/home XDG_STATE_HOME=<copy>/state .venv/bin/python -m bridge.webui plan-owners --dry-run
```

The dry run found the four legacy plans and wrote nothing:

```
071b4eba-cc84-4023-828e-7429a9f84985  u26temp  legacy-audit  owner=tester
87296627-2816-444a-bc01-40cc06124f01  u26temp  legacy-audit  owner=tester
9acf2d7a-7e6e-4039-ab6c-467f61c78e7f  u26temp  legacy-audit  owner=tester
fff357fb-adaf-496c-8ca8-b568dc77b500  u26temp  legacy-audit  owner=tester
4 plan(s) would be recorded; 0 already had a record.
```

Then a real run on the copy wrote 4 records, and a second run reported
"0 plan(s) were recorded; 4 already had a record."

### 2. Ownership is bound before the plan is stored

In `make_plans`:
1. **Before planning,** under the lock, capture the temporary server's owner
   and connection tag, its inventory and its known_hosts.
2. **Plan** with exactly those.
3. **After planning,** under the lock again:
   - If the connection ended, was replaced (a different tag) or changed
     owner, the plan is discarded and nothing is written.
   - If the name became temporary meanwhile, the plan is discarded too.
   - Otherwise the binding is written (exclusive create, fsync), then
     `run.save_plan`. If saving the plan fails, the binding is removed.

Failure cases:
- If writing the binding fails, the plan isn't stored, and a partly written
  binding file is removed.
- A restart between the two writes leaves at most an orphan binding, never
  a stored, unbound temporary plan.

The consumer's `expected_operator` check and the single-use marker are
unchanged.

New audit fields: `connection` on `temp_access_created`,
`temp_access_removed` and on `plan` lines for temporary servers. They give
future evidence that the round-1 lines lacked.

### Changed paths (round 2)

- `bridge/webui/app.py`:
  - `write_plan_owner`, `bind_plan`, `unbind_plan`;
  - `legacy_plan_owners`, `upgrade_legacy_plans`;
  - `may_see`, `require_plan_owner`, `make_plans`;
  - `App(upgrade=)`, and the audit fields.
- `bridge/webui/__main__.py`: `plan-owners [--dry-run]`.
- `tests/backend/test_webui.py`:
  - `HttpBase` now uses a private `XDG_STATE_HOME`. Before, it read the
    real state folder.
  - 7 binding tests in `TemporaryServerTests`.
  - The new class `LegacyPlanTests`, with 5 tests.
- `docs/webui/claude-integration-handback.md`: this section.

### Tests (round 2)

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 108 tests, OK.

```bash
.venv/bin/python -m unittest discover -s tests/backend -p 'test_*.py' < /dev/null
```

Result: Ran 933 tests in 468.1s, OK (skipped=6). That is Codex's 921 plus the 12 new tests.

### Remaining checks for Codex

1. **Upgrade the real lab state.**
   - Run `plan-owners --dry-run` against `~/dssa-lab-4.47`; expect the
     four plans above.
   - Then start the web UI, which upgrades at start-up.
   - Check that tester sees those four plan pages and gets "before
     ownership was recorded" on Report.
2. **Name reuse live.**
   - Connect an unsaved `u26temp` as another user (for example
     `tester2`).
   - Confirm that user can't open the four plans or their runs.
3. **Planning interrupted live.**
   - Sign out (ending access) while a plan on a temporary server is
     running, and expect "ended or changed while planning".
   - Hard to time by hand; the unit tests cover it.
4. **Still open from round 1:** the password-required sudo prompt in the
   web UI needs a dedicated lab account.

## Review round 3 (Codex review of `839a377`): reading historical plans

**Finding (lab).** After the temporary connection ended, the four migrated
`u26temp` plans returned HTTP 400 for their owner. The plan page called
`App.check()` before checking visibility, and `App.check()` needs the
target's current connection.

**Fix.**
- **The plan page** now:
  1. loads the stored plan (`run.load_plan`);
  2. authorizes the viewer from its recorded owner (`may_see`);
  3. sends historical plans to a read-only page before any preflight.
- **`App.historical(plan_id, target)`** is true when a plan has an
  ownership record and its connection isn't the live one: legacy (no
  connection), ended, or replaced by another connection with the same name.
- **`historical_plan_page`** shows the summary, the table and the saved
  result (`runs/<plan_id>/summary.json`), plus a notice. It has no SSH, no
  preflight and no action buttons.
- **Current connections and saved-server plans** go through the existing
  preflight flow, unchanged.
- **`App.authorize_plan(user, plan_id)`** (replaced in round 4 by `App.bound_action`), used by approve, apply, report
  and resume:
  - It loads the stored plan and applies `require_plan_owner` (the plan's
    own owner and connection) before `require_owner` (the current name).
  - Only then does it call `check()`.
  - So for a legacy plan every action refuses with "… before ownership was
    recorded. Make a new plan." It no longer fails on the missing
    connection.
- **Refactor:** `plan_summary()` and `already_run()` are shared by both
  pages.

**Changed paths:**
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`:
  - a `HttpBase.stored()` helper;
  - 8 plan-page tests now also provide the stored plan;
  - 4 new tests in `LegacyPlanTests`.
- `docs/webui/claude-integration-handback.md`: this section.

**New tests** (`LegacyPlanTests`). In each, `App.check` raises if it's
called, which proves no SSH or preflight is used.
- `test_the_owner_reads_a_legacy_plan_after_its_connection_ended`: HTTP 200,
  with the saved result and the legacy notice, and no approve, apply,
  report or resume form.
- `test_a_bound_plan_is_read_only_once_its_connection_ended`: HTTP 200 with
  the "which has ended" notice.
- `test_another_user_gets_404_even_after_reusing_the_name`: Bob gets 404
  while his own `tmp1` connection is live, and again after `tmp1` becomes a
  saved server. Alice gets 200 without using Bob's connection.
- `test_report_and_other_actions_stay_blocked_for_a_legacy_plan`: POST
  report, resume, apply and approve each give HTTP 400 "before ownership
  was recorded", and nothing starts.

**Tests run:**

```bash
.venv/bin/python -m unittest tests.backend.test_webui < /dev/null
```

Result: Ran 77 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 112 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining checks for Codex on the real lab:**
1. With `u26temp` disconnected, restart the web UI as tester and open the
   four plans: expect HTTP 200, read-only, with the legacy notice.
2. Report on one of them should be refused with "before ownership was
   recorded".
3. As tester2, connect an unsaved `u26temp` and open the same four URLs:
   expect HTTP 404.
4. Still open: the web UI's sudo password prompt, which needs a lab account
   that has to type a password.

## Review round 4: actions bound to the authorized connection

**Finding.** Approve, Apply, Report and Resume authorized the plan once.
Then the preflight, the login check, approval consumption and the job each
looked up the connection again by server name. `end_temporary` only waited
for a running *job*, so access could end, and a new connection with the same
name could appear, between authorization and job start.

**Fix** (`bridge/webui/app.py`). Round 3's `App.authorize_plan` is replaced
by `App.bound_action`.

- **`App.bound_action(user, plan_id)`** (a context manager):
  - It loads the stored plan, and under the lock applies
    `require_plan_owner` and `require_owner`.
  - It captures the authorized connection: its tag, inventory, known_hosts
    and job env.
  - It registers the action in `App.actions[target]` until the action
    finishes or is refused.
- **`App.still_bound(action)`**, called under the lock before each step
  that uses the connection, refuses in these cases:
  - **ended or replaced:** a different tag, the server gone, or marked
    `ending`. The message: "… nothing was used and no approval was
    consumed".
  - **near expiry:** within `TEMP_MARGIN` (5 minutes) of expiry.
  - **a saved-server plan** whose name became temporary.
- **`end_temporary`** refuses while an action or a run is in flight. Under
  the lock it marks the connection `ending` before removing anything, so no
  action can start meanwhile. If removal fails, the mark is cleared and the
  access stays until its expiry. Sign-out and the sweeper just retry later.
- **Each action:**
  - Approve: preflight, then `still_bound`, then `approve_plan`, all under
    the lock.
  - Apply:
    1. preflight;
    2. `still_bound`;
    3. login with the captured inventory and known_hosts;
    4. under the lock: `still_bound`, `consume_approval(expected_operator)`
       and job start (captured env), together.
  - Report and Resume: under the lock, `still_bound` and job start.
- **Unchanged:**
  - the saved-server flow;
  - the round-3 historical read-only page, which uses no action;
  - the approval's `expected_operator` check and single-use marker.

**Changed paths:**
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`: 8 new tests in `TemporaryServerTests`.
- `docs/webui/claude-integration-handback.md`: this section.

**New tests** (`TemporaryServerTests`):
- `test_apply_refuses_when_the_connection_is_replaced_during_preflight`:
  no login, no consumption, no job.
- `test_apply_refuses_when_the_connection_is_replaced_during_login`
- `test_apply_refuses_when_access_ends_during_login`
- `test_access_cannot_end_while_an_action_is_in_progress`:
  - sign-out during the login returns False, and nothing is removed;
  - the apply then completes on the original connection's known_hosts and
    env, with `expected_operator`.
- `test_no_action_starts_on_an_ending_or_nearly_expired_connection`: apply,
  report and approve are all refused before any preflight.
- `test_approve_refuses_when_the_connection_is_replaced_during_preflight`
- `test_report_and_resume_use_the_authorized_connection`
- `test_a_saved_server_plan_refuses_if_its_name_turns_temporary_mid_action`

Every refusal test also checks that `App.actions` is empty afterwards.

**Tests run:**

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 30 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 120 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **Interleavings not tested live.** The interleavings are simulated in
   unit tests. On the real lab, signing out during an apply's login should
   leave access in place until the run ends, then remove it.
2. **Approval consumed before the job starts.** If `Job.start()` itself
   fails after `consume_approval` (for example, the pty or process can't be
   created), the approval is used and no run starts. This is unchanged from
   before; the plan has to be made again.
3. **The sudo password prompt in the web UI** is still open; it needs a
   dedicated lab account.

## Review round 5: temporary-access cleanup bound to its connection

**Findings.**
- Logout, the sweep, manual end and run-completion cleanup removed access
  by server **name**. So a stale caller could remove or forget a newer
  connection with the same name.
- Two removals could overlap.
- A job that failed to start stayed "running" forever, which blocked
  cleanup.
- Preflight still looked up the connection by name.

**Fix.**
- **`App.end_temporary(user, target, reason, connection)`** now requires
  the caller's connection tag.
  - A caller whose tag is no longer the live one gets True ("yours is
    already gone") and touches nothing.
  - If the connection is already `ending`, a second caller backs off with
    False, so one removal runs at a time.
  - After removal, the entry is forgotten only if the dict still holds
    *that* connection (`is meta`).
  - The audit lines carry `connection`.
- **Every caller passes the tag it holds:**
  - logout: each of the user's connections, by its own tag;
  - the sweep: the tag it inspected;
  - manual end: the tag of the owner's current connection;
  - run completion: the tag captured by the bound action.
- **New connections:** `App.connecting` allows one connection attempt per
  name at a time. A name stays taken while its old connection is being
  removed.
- **`Job.start()`** catches start failures (`openpty`, `Popen`, or the
  reader thread). It closes the file descriptors, kills a half-started
  process and leaves the job finished: exit 127, with the reason in its
  output. It then raises `JobStartError` (an `OSError`, so the batch code
  still catches it). A failed job is still listed, never blocks
  `running_on`, and refuses input.
- **Apply.** If the start fails after the approval was consumed, the
  approval stays used (single use) and the user sees "The approval was
  used, but the run could not start (…). Nothing ran on the server. Make a
  new plan to try again." The audit gets `apply_start_failed` with
  `approval_used`.
- **Report and Resume** report "… could not start (…)" and audit
  `report_start_failed` or `resume_start_failed`.
- **`App.check(plan_id, action)`:** approve and apply now run preflight
  with the host and known_hosts captured by `bound_action`, never whatever
  holds the name now. The plan page's own preflight is unchanged.

**Changed paths:**
- `bridge/webui/app.py`
- `bridge/webui/jobs.py`: `JobStartError`, `Job.fail`, failure handling in
  `Job.start`.
- `tests/backend/test_webui.py`:
  - 4 existing calls now pass the tag;
  - 9 new tests.
- `docs/webui/claude-integration-handback.md`: this section.

**New tests** (`TemporaryServerTests`):
- `test_overlapping_cleanup_removes_once`:
  - while one removal is blocked inside key removal, a second removal backs
    off and a new connection with that name is refused;
  - the key is removed exactly once.
- `test_stale_cleanup_never_touches_a_replacement`: after Bob reuses the
  name, late run-finished, signed-out and expiry calls with Alice's old tag
  remove nothing.
- `test_a_removal_never_forgets_a_connection_that_replaced_it`
- `test_one_connection_attempt_per_name_at_a_time`
- `test_run_completion_cleanup_leaves_a_replacement_alone`
- `test_a_job_that_cannot_start_is_left_finished`: a real `Job` with a
  missing executable ends finished, exit 127, still listed, refusing input.
- `test_a_failed_start_after_consumption_is_reported_and_blocks_no_cleanup`:
  - `Popen` fails after consumption: the message is clear, the approval is
    consumed once, and the audit has `approval_used: true`;
  - nothing is left running, and cleanup succeeds.
- `test_a_failed_report_start_is_reported`
- `test_preflight_uses_the_captured_connection`

**Tests run:**

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 39 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 129 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **Concurrency is unit-tested only.** It uses blocked fakes, not a live
   lab. On the lab: sign out while the sweep is due, and end access by hand
   while a run finishes. Each should remove the key exactly once (one
   `temp_access_removed`).
2. **A failed start is simulated** (`Popen` or `openpty` raising); it hasn't
   been reproduced live.
3. **The sudo password prompt in the web UI** is still open; it needs a
   dedicated lab account.

## Review round 6: failure paths in cleanup, End, enrolment and job start

Rounds 3–5 are kept; this round only changes their failure paths.

**1. Cleanup is exception-safe** (`App.end_temporary`).
- The removal, the expired-folder discard and the audit line run inside
  `try/finally`. The `finally` block always settles the state under the lock:
  - removed or expired: forget this connection (only if it is still the
    same `meta`);
  - otherwise: clear `ending`, so the connection stays usable until its
    expiry and a later removal can retry.
- An exception from `enrol.end_temporary_access` (an unreadable `meta.json`,
  say) counts as "not removed"; `temp_access_remove_failed` gets its `error`.
- If the audit line fails after the key was removed, the connection is still
  forgotten and the caller gets "Access to X was removed, but the audit log
  could not be written".

**2. Manual End is bound to the rendered connection.**
- The workspace's End access form carries a hidden `connection` field with
  that row's tag.
- `POST /servers/<t>/end` ends only that tag, and only for its owner:
  - no field (an old page): "This page is out of date; reload it…";
  - the tag is no longer the user's current connection to that name: "That
    access to X has already ended, so nothing was removed. Any newer
    connection with this name was left as it is." The same message is shown
    to another user, so it reveals nothing.

**3. One name reservation for saved and temporary enrolment**
(`App.reserve_name`).
- It refuses a name that is connected for a setup or being enrolled, then,
  while holding the reservation, a name that is saved.
- `add_temporary` and the new `App.add_saved` (moved out of `post_enrol`)
  both use it. A saved enrolment can no longer race a temporary connection
  of the same name, or another saved enrolment.

**4. A failed job start always ends finished** (`Job.start`).
- Teardown moved to `Job._teardown`: kill the process group, kill the
  process, wait (10 s), close stdout, close both pty descriptors. Each step
  runs even if an earlier one raises.
- `fail()` runs in a `finally` after the teardown, so the job is finished
  (exit 127) even if the teardown raises.

**5. Pre-launch versus post-launch failure.**
- `JobStartError.launched`:
  - False: no process was created, so nothing ran (confirmed);
  - True: the process was created, then stopped because it could not be
    followed, so the outcome is uncertain.
- The messages differ:
  - not launched: "The run could not start (…). Nothing ran on the server.";
  - launched: "The run was started, but DSSA could not follow it and
    stopped it (…). Part of it may have run on the server: open Report to
    see the server's state."
- The same applies to Report and Resume rollback (`start_remote`).

**6. Every failure says whether the approval was used** (`App.start_failed`,
`App.approval_consumed`).

| Failure | Stage in the audit line | Approval reported as |
|---|---|---|
| `consume_approval` raises `ApprovalError` (not valid, wrong approver, already used) | — | passed on unchanged; no failure line |
| `consume_approval` raises anything else | `claiming the approval` | read from the `consumed` marker: used, not used, or unknown ("treat it as used and make a new plan") |
| Reading the approved plan, building the job or writing the `apply` audit line fails | `preparing the run` | used; nothing is started (no audit line, no run) |
| `Jobs.start` raises `JobStartError` | `starting the run` | used; plus `launched` |

- The `*_start_failed` line now has `stage`, `launched` and, for apply,
  `approval_used`.
- If that line cannot be written either, the message adds "This failure
  could not be written to the audit log either (…)".
- Verification-only plans are labelled "verification run" and use no
  approval.
- Approvals stay single use: the marker is never removed.
- `approve`: if the web UI's `approve` audit line fails after the approval
  was recorded, the error says so ("The approval is recorded and has not
  been used").

**Changed paths (round 6):**
- `bridge/webui/app.py`
- `bridge/webui/jobs.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests** (`TemporaryServerTests`):
- `test_a_failing_removal_never_strands_the_connection_in_ending`
- `test_a_failing_audit_never_strands_the_connection`
- `test_manual_end_only_ends_the_connection_its_form_showed`
- `test_saved_enrolment_takes_the_same_name_reservation`: a saved enrolment
  of a connected name is refused, a temporary connection is refused while a
  saved enrolment of that name runs, and an already-saved name is refused.
- `test_a_job_whose_teardown_fails_is_still_left_finished`: `killpg`,
  `kill`, `wait` and `stdout.close` all raise; the job still ends finished,
  exit 127, with `launched=True`.
- `test_a_job_that_never_launched_says_so`
- `test_an_uncertain_launch_is_reported_as_uncertain`
- `test_a_claim_that_fails_after_its_marker_reports_the_approval_used`
- `test_a_claim_that_fails_before_its_marker_reports_the_approval_unused`
- `test_a_failed_apply_audit_starts_nothing_and_says_so`
- `test_a_refused_claim_is_passed_on_unchanged`
- Updated: `test_end_access_and_sign_out_remove_the_key` (reads the tag from
  the form), `test_only_the_owner_sees_and_uses_it` (Bob is refused even with
  Alice's tag) and the round 5 failed-start test (new wording).

**Tests run:**

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 50 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 140 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **All failure paths are simulated** with mocks (raising removal, audit,
   `Popen`, thread start, teardown and claim). None was reproduced live.
2. **Concurrency is unit-tested only.** On the lab: End access from a page
   rendered before a reconnect of the same name; save a server while a
   temporary connection with that name is being made.
3. **An uncertain launch** kills the process group of `dssa-run.sh`. Whether
   that also stops the remote command depends on ssh noticing the closed
   session; Report is the advised check.
4. **The sudo password prompt in the web UI** is still open; it needs a
   dedicated lab account.

## Review round 7: the three round-six findings

Rounds 3–6 are kept.

**1. Approval claim failures after the marker** (`App.claim_approval`,
`App.approval_consumed`).
- `approval_consumed` now uses `os.lstat` and returns three states:
  - present;
  - absent (`FileNotFoundError` only);
  - unreadable (any other `OSError`, with the error).

  Python 3.14's `Path.exists()` returns False for every `OSError`, so before
  this an unreadable marker read as "not used".
- The marker is read before and after `consume_approval`:
  - **Unreadable before:** `consume_approval` is not called. The message is
    "checking the approval failed (its single-use marker cannot be read…).
    … The approval was not used".
  - **Absent before and after, or present before:** a real `ApprovalError`
    (not valid, another approver, used earlier) is passed on unchanged.
  - **Absent before, present after:** this call wrote the marker, so any
    error, including an `ApprovalError` from the approval audit, is reported
    as "claiming the approval failed (…) … The approval was used and cannot
    be used again" with `approval_used: true`.
  - **Absent before, unreadable after:** "…the marker cannot be read… DSSA
    could not tell whether the approval was used; treat it as used", with
    `approval_used: null`.
- In every case no job starts and the marker is never removed.

**2. Leftover temporary folders are recoverable** (`App.clear_leftover`).
- After a key is removed or has expired, `end_temporary` deletes the
  folder. If anything remains, it moves the folder to
  `<home>/temporary-leftover/<name>-<random>/` with one rename on the same
  file system, then tries deleting it there.
  - That folder is never loaded as a connection.
  - The audit line gets a `leftover` note: where it went, or that it could
    not be deleted or moved aside.
- `add_temporary`, while holding the name reservation, finds a folder for a
  name that is not connected. That folder can only be a leftover, so it is
  cleared the same way:
  - success: `temp_leftover_cleared`, and the connection goes ahead;
  - failure: `temp_leftover_blocked` and a clear error ("A folder left from
    an earlier connection to X could not be cleared: … Remove it on the web
    UI host, then connect again"). The reservation is released, so a later
    attempt retries.
- A failed removal (key not removed, not expired) still keeps its folder in
  place for the retry, as before.

**3. Job start: `launched`, terminal state and "stopped"** (`Job.start`,
`Job._teardown`).
- The teardown call is wrapped, and so is `fail()`.
  - If either raises, the job is still finished (rc 127).
  - `JobStartError` is still raised with the original `launched`.
- `_teardown` returns whether the process was seen to exit. `stopped` is
  True only when `wait(timeout=10)` returned with a `returncode`.
  - `JobStartError.stopped`: True or False for a launched process, None
    otherwise.
  - Messages say "so DSSA stopped it" only when the stop was confirmed.
    Otherwise they say "DSSA tried to stop it but could not confirm that it
    stopped" (job output) or "could not confirm that it stopped" (web UI
    error).
  - The `*_start_failed` audit line has `stopped` when `launched`.
- The `process.stdout.close` lookup moved inside its own `try` (before, it
  was evaluated outside any `try`).

**Changed paths (round 7):**
- `bridge/webui/app.py`
- `bridge/webui/jobs.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests** (`TemporaryServerTests`):
- `test_an_approval_audit_error_after_the_marker_reports_it_used`
- `test_an_approval_used_earlier_is_refused_unchanged`
- `test_an_unreadable_marker_before_the_claim_uses_nothing`: the approvals
  folder is mode 000.
- `test_an_unreadable_marker_after_a_failed_claim_counts_as_maybe_used`
- `test_a_folder_that_cannot_be_deleted_is_moved_aside`: the folder is moved
  aside, never reloaded, and the name connects again.
- `test_a_leftover_folder_blocks_nothing_once_it_can_be_cleared`: rename
  fails, enrolment is refused and the reservation released, then it
  succeeds once clearing works.
- `test_a_failed_removal_keeps_its_folder_for_the_retry`
- `test_a_confirmed_stop_is_reported_as_stopped`
- `test_a_raising_teardown_keeps_launched_and_the_finished_state`:
  `_teardown` and `fail` both raise.
- `test_an_unconfirmed_stop_is_never_called_stopped`
- Updated: `test_a_job_whose_teardown_fails_is_still_left_finished` (wait
  fails, so it is never called stopped) and
  `test_an_uncertain_launch_is_reported_as_uncertain` (`stopped=True`).

**Tests run:**

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 60 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 150 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **All failure paths are simulated** with mocks and a mode-000 folder.
   None was reproduced live.
2. **The unreadable-marker tests depend on file permissions,** so they need
   a non-root test user.
3. **Leftover folders are not cleaned up automatically.** Those in
   `temporary-leftover/` (which may hold a dead private key) are only
   reported in the audit line, never deleted on a schedule.
4. **The sudo password prompt in the web UI** is still open, and the
   concurrency checks from rounds 5–6 are still unit-tested only.

## Review round 8: restart after a stuck folder; a concurrent consumer's refusal

Rounds 3–7 are kept.

**1. An ended connection is never reloaded as active after a restart.**
- New `App.mark_ended(tag)` writes `<home>/temporary-ended/<tag>` (0600,
  fsync). This is outside the connection's folder, so it works when that
  folder cannot be changed.
- `end_temporary` writes it first, as soon as the key is removed or has
  expired, before clearing the folder. If the record cannot be written, the
  audit line gets `ended_record_error`.
- New `App.ended_connections()`: the tags in `temporary-ended/`, plus the
  `connection` of every `temp_access_removed` / `temp_access_expired` audit
  line. That second source covers a record that could not be written.
- `load_temporary` skips a folder whose tag is in that set. The folder stays
  where it is until a same-name enrolment clears it (round 7's
  `clear_leftover`).
- A failed key removal (`temp_access_remove_failed`) writes no ended record,
  so after a restart it is reloaded and retried as before.

**2. A concurrent consumer that wins keeps "already used".**
- `bridge/components/approval.py`: in `consume_approval`, anything raised
  after this call wrote the `consumed` marker (reading `approval.json` or
  the approval audit) now carries `error.approval_consumed = True`.
  Behaviour is otherwise unchanged; the command line only sees the extra
  attribute.
- `App.claim_approval`:
  - a flagged error is reported as "the approval was used";
  - any other `ApprovalError` is a refusal and passed on unchanged. That
    includes "already used" from a concurrent consumer that wrote the marker
    first, and "not valid" when it won before the check;
  - other unflagged errors (a failed marker write, say) still use the marker
    read before and after the call.
- Approvals stay single use, no job starts in either case, and the marker
  is never removed.

**Changed paths (round 8):**
- `bridge/components/approval.py`
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`
- `tests/backend/test_approval_records.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests:**
- `TemporaryServerTests`:
  - `test_a_restart_never_reloads_an_ended_connection_with_a_stuck_folder`:
    real permissions, with the folder and its parent at mode 0500.
  - `test_without_its_ended_record_the_audit_line_still_keeps_it_ended`
  - `test_after_a_restart_and_repaired_permissions_the_name_connects_again`:
    still stuck after the restart (refused, `temp_leftover_blocked`);
    permissions repaired with chmod 0700; then it connects, logs
    `temp_leftover_cleared`, and the new connection reloads on the next
    restart.
  - `test_a_restart_keeps_a_failed_removal_for_its_retry`
  - `test_a_concurrent_consumer_that_wins_keeps_already_used`
  - Updated `test_an_approval_audit_error_after_the_marker_reports_it_used`
    (the error carries the flag, as `consume_approval` now sets it).
  - Updated `test_an_unreadable_marker_after_a_failed_claim_counts_as_maybe_used`
    (an unflagged `OSError` from the marker write).
- `ConsumeTests`:
  - `test_a_failed_consume_audit_is_flagged_as_consumed`: a real failing
    audit script; the marker stays.
  - `test_a_refusal_is_never_flagged_as_consumed`: a concurrent marker, and
    the wrong host key.
- Mutation check: with the startup skip disabled, the three restart tests
  fail; `app.py` was restored afterwards.

**Tests run** (as `vivek`, uid 1000, not root):

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 65 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 157 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **Ended records are never pruned.** `temporary-ended/` keeps one small
   file per ended connection, and `temporary-leftover/` is still not cleaned
   up automatically.
2. **The restart path was tested in-process** (a new `App` on the same
   home), not with a real `serve` restart on the lab.
3. **The sudo password prompt** and the rounds 5–6 race checks are still not
   lab-tested.

## Review round 9: a stop between removing the key and recording it

Round 8's approval fix and rounds 3–8 are kept.

**The window.** The server removes the key, then DSSA stops (killed,
`SystemExit` or `KeyboardInterrupt`) before it writes the ended record or
the audit line. The original folder, with its `meta.json`, is still there,
so the next start used to load the connection as active.

**The fix: the intent is saved before the server is asked.**
- The removal step is now `App._remove_connection`, shared by
  `end_temporary` and the resumed cleanup. It first calls
  `App.mark_cleanup(tag, target)`, which writes
  `<home>/temporary-cleanup/<tag>` (0600, fsync; target, tag, time).
  - If the intent cannot be saved, the server is not asked and nothing is
    removed. The connection stays active, and `temp_access_remove_failed`
    says "the cleanup could not be recorded before removing".
- Once the removal is done, or the key has expired:
  1. ended record (round 8);
  2. clear the folder (round 7);
  3. audit line;
  4. drop the intent.
- When the server answers "not removed" (or was not asked), the intent is
  dropped. The key may still work, so the connection stays active and
  retryable, after a restart too, as before.
- The intent is dropped only once the outcome is known. A stop during the
  removal leaves it in place.
- **At startup** (`load_temporary`):
  1. tag in an ended record or audit line: skipped (round 8);
  2. tag with a cleanup intent: goes into `App.cleanup_pending`, not
     `App.temporary`;
  3. otherwise: active.

  Intents whose tag has already ended are deleted.
- **`App.resume_cleanup`** runs at the start of every sweep. It retries
  each pending removal with the same step:
  - the remote removal is idempotent: the `grep -v` succeeds when the line
    is already gone;
  - success, or expiry: removed and audited with `"resumed": true` and
    reason "cleanup resumed after a restart";
  - failure: it stays pending, with its intent kept, and is retried at the
    next sweep.
- `reserve_name` refuses a name in `cleanup_pending` ("…is still being
  removed; try again in a few minutes"). The name can't be reused until the
  cleanup has finished.

**Changed paths (round 9):**
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests** (`TemporaryServerTests`):
- `test_the_cleanup_intent_is_saved_before_the_server_is_asked`
- `test_without_a_saved_intent_nothing_is_removed`
- `test_a_removal_interrupted_after_the_key_went_is_retried_after_a_restart`:
  this is the regression test.
  1. The fake server removes the key, then a `BaseException` stops DSSA. The
     original folder and `meta.json` are still present; there is no ended
     record and no audit line.
  2. On restart, the connection is not active and is pending; a same-name
     enrolment is refused.
  3. The sweep removes the key again, deletes the folder, writes the ended
     record and the resumed audit line, and drops the intent.
  4. The same name then connects, and the new connection is active after
     the next restart.
- `test_a_resumed_removal_that_fails_stays_pending_and_retryable`: it is
  still pending after another restart, then succeeds, and the same name
  connects.
- `test_a_pending_removal_whose_key_expired_is_discarded`
- Updated `test_a_restart_keeps_a_failed_removal_for_its_retry`: no intent
  is left behind, so it stays active.
- Mutation checks, with `app.py` restored after each:
  - startup ignoring intents: 3 tests fail;
  - no intent during the removal: 4 tests fail.

**Tests run** (as `vivek`, uid 1000, not root):

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 70 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 162 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **The stop is simulated** with a `BaseException` from the fake removal,
   in-process. A real `kill -9` of `serve` mid-removal was not done on the
   lab.
2. **A pending cleanup is invisible to its owner.** The connection is not
   shown on the workspace while pending; only the audit log and the
   "still being removed" refusal show it.
3. **The state folders are never pruned.** `temporary-ended/`,
   `temporary-cleanup/` (normally empty) and `temporary-leftover/` are not
   cleaned up automatically.

## Review round 10: the three remaining cleanup-intent paths

Rounds 3–9 are kept.

**1. A confirmed refusal versus an unknown outcome**
(`bridge/webui/enrol.py`).
- The remote removal command now ends by printing its outcome:
  - `DSSA-KEY-REMOVED <tag>`;
  - `DSSA-KEY-PRESENT <tag>` (exit 1) when the line is still there.
- `end_temporary_access` returns:
  - True only on the REMOVED marker;
  - False only on the PRESENT marker (a confirmed refusal);
  - otherwise it raises the new `RemovalUnknown`. That covers a timeout,
    a dropped connection, and a refused login (after a removal the
    temporary key itself no longer logs in, so exit 255 proves nothing).
- In `App._remove_connection`:
  - `RemovalUnknown` gives the outcome "pending": the connection moves
    from `App.temporary` to `App.cleanup_pending`, the intent is kept, and
    the audit line is `temp_access_remove_unknown` with `"pending": true`;
  - a confirmed refusal (or a local error, so the server was not asked)
    stays active and drops the intent, as before;
  - unknown and past expiry counts as expired.

**2. The intent is settled under the same lock.**
- The `finally` block now calls `App._settle(...)` under `self.lock`. It
  drops or keeps the intent and updates the tables before it clears
  `ending`.
- Before, the intent was dropped after `ending` was cleared and the lock
  released. A retry of the same tag could start in between and write its
  own intent, which the first caller then deleted.

**3. Unrecorded retirement with the original folder left.**
- If the key was removed (or expired) but the ended record cannot be
  written and the original folder is still there, the outcome is
  "pending": the intent is kept and the connection goes to
  `cleanup_pending`, so its name stays reserved.
- `load_temporary` now checks intents before ended tags. A folder with an
  intent is pending even when a `temp_access_removed` audit line exists.
  Intents whose folder is gone are dropped.
- A resumed cleanup whose tag is already ended (record or audit line)
  doesn't ask the server again, because the key no longer logs in. It only
  retries the retirement: ended record, then clearing the folder.
- If the ended record fails but the folder was cleared, it is done; nothing
  is left to reload.

**Changed paths (round 10):**
- `bridge/webui/enrol.py`
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests:**
- `TemporaryAccessTests` (the real remove command, run by bash in a
  scratch home):
  - `test_a_confirmed_refusal_is_told_apart`: `authorized_keys` mode 0400,
    so the line stays; returns False.
  - `test_a_lost_reply_after_a_real_removal_is_unknown`: the line is gone,
    the reply lost; `RemovalUnknown`, folder kept.
  - `test_a_timeout_is_unknown`
  - Updated `test_failed_removal_keeps_the_folder_for_a_retry`: a refused
    login is now `RemovalUnknown`.
- `TemporaryServerTests`:
  - `test_a_lost_reply_keeps_the_cleanup_pending`: not active, name
    refused, still pending after a restart, then finished by the sweep;
    the same name connects.
  - `test_a_confirmed_refusal_stays_active_and_retryable`
  - `test_the_intent_is_settled_before_a_retry_can_start`: while the first
    caller settles, under the lock with `ending` still set, a retry of the
    same tag is started and must not reach the server. It runs afterwards,
    and its own intent exists during its removal.
  - `test_overlapping_retries_of_a_pending_cleanup_remove_once`
  - `test_unrecorded_retirement_with_its_folder_left_keeps_the_name_across_restart`:
    real permissions (mode 0500); the intent wins over the audit line. After
    the repair, the sweep finishes without asking the server again, and the
    same name connects.
  - `test_unrecorded_retirement_without_a_folder_left_is_done`
- Mutation checks, with `app.py` restored after each:
  - unknown treated as refused: 2 tests fail;
  - unrecorded retirement treated as done: 1 fails;
  - `ending` cleared before settling: 1 fails.

**Tests run** (as `vivek`, uid 1000, not root):

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryServerTests tests.backend.test_webui.TemporaryAccessTests < /dev/null
```

Result: Ran 84 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 171 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **A refused login always counts as unknown.** The cleanup then stays
   pending until the key's expiry, and its name stays reserved for up to
   60 minutes. This is safe, but slower than needed when the key really is
   gone.
2. **The new markers were checked only with bash locally.** The remote
   command still needs checking on the lab VMs' shells (dash on
   Debian/Ubuntu `sh`, bash on Rocky/Alma).
3. **The rounds 9 gaps remain:** a pending cleanup is not shown to its
   owner, the state folders are not pruned, and there is no real `kill -9`
   lab test.

## Review round 11: the three round-ten findings

Rounds 3–10 are kept.

**1. REMOVED only after the line is confirmed absent**
(`enrol._remove_command`, POSIX sh).
- **Rewrite:** `authorized_keys` is rewritten only when all of these hold:
  - a temporary file could be created first (`umask 077; : > tmp`);
  - `grep -v` read the whole file (exit 0 or 1);
  - the temporary file is readable.

  A failed read or write therefore never truncates `authorized_keys`.
  Before, a write-only file or an unwritable `~/.ssh` could leave it empty.
- **Check:** `grep -q` then decides:
  - exit 0: `DSSA-KEY-PRESENT` (exit 1);
  - exit 1: `DSSA-KEY-REMOVED`;
  - exit 2 (a read error): REMOVED only when the file provably does not
    exist (in a readable, searchable `~/.ssh`), or `~/.ssh` itself does not
    exist (in a searchable home). Otherwise `DSSA-KEY-UNKNOWN` (exit 2).
- `end_temporary_access` raises `RemovalUnknown` ("the server could not
  check whether the key line is gone…") on UNKNOWN, as it already did with
  no marker.

**2. Unreadable connection metadata at startup** (`load_temporary`).
- A `meta.json` that exists but cannot be read or parsed is no longer
  skipped. The folder becomes a `cleanup_pending` placeholder:
  - `unreadable: true`;
  - tag, owner, expiry and `removed` from its intent, when there is one.

  Its name stays reserved, and it is never active.
- A missing `meta.json` is still a leftover (round 7). Only a confirmed
  removal or an expiry deletes it.
- Intents are never dropped when they cannot be read. They are dropped
  only when readable and their folder is gone.
- If the intent folder exists but cannot be listed, every connection loads
  as pending.
- The intent now also stores `owner` and `expires`.
- `App.reread_pending` runs in each sweep:
  - when `meta.json` can be read again (and the tag matches), the real
    record replaces the placeholder and the normal retry runs;
  - until then the server is not asked, and the name stays reserved.

**3. A confirmed removal or expiry is kept in the pending state.**
- When the retirement can't be finished (no ended record, original folder
  left):
  - the pending entry gets `retire_only: true`;
  - the intent is rewritten with `removed: true` (if that fails, the audit
    line gets `intent_error`).
- The resumed cleanup checks `retire_only` (as well as the ended record
  and the audit line) before contacting the server. A sweep then only
  retires the connection locally, even when the ended record and the audit
  line both failed, and also after a restart, because `load_temporary`
  reads `removed` from the intent.

**Changed paths (round 11):**
- `bridge/webui/enrol.py`
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New tests:**
- `TemporaryAccessTests`: each scenario runs the real command under
  **bash and dash**.
  - `test_an_unreadable_authorized_keys_is_unknown_and_left_intact` (mode
    0200)
  - `test_an_unsearchable_ssh_folder_is_unknown` (`~/.ssh` 0600)
  - `test_a_failed_rewrite_is_a_confirmed_refusal_and_truncates_nothing`
    (`~/.ssh` 0500)
  - `test_a_missing_file_or_folder_is_a_confirmed_removal`
  - `test_a_normal_removal_is_confirmed_in_both_shells`
- `TemporaryServerTests`:
  - `test_an_unreadable_meta_keeps_its_intent_and_name_across_restart`:
    `meta.json` 0000 after an interrupted removal. It is reserved and not
    active, the intent is kept, there is no SSH while unreadable, and it
    stays so after another restart. After the repair, the sweep removes it
    and the same name connects.
  - `test_an_unreadable_meta_without_an_intent_is_reserved_not_active`
  - `test_an_intent_that_cannot_be_read_is_never_dropped`
  - `test_a_confirmed_removal_is_retired_without_ssh_when_records_fail`:
    the ended record and the audit line both fail. It is retired after a
    restart and repair with no second SSH call, and the same name connects.
- Mutation checks, with the files restored after each:
  - an unverified check printing REMOVED: the 2 unreadable tests fail;
  - startup skipping an unreadable `meta.json`: 2 fail;
  - `retire_only` ignored: 1 fails.

**Tests run** (as `vivek`, uid 1000, not root):

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryAccessTests tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 93 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 180 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **An unreadable placeholder never expires by itself.** It is reserved
   until its `meta.json` can be read again, even past the key's expiry.
2. **A double failure falls back to SSH.** If the intent rewrite
   (`removed: true`) fails as well as the ended record and the audit line,
   a restart retries SSH. That gives an unknown outcome, so the cleanup
   stays pending until expiry: safe, but slower.
3. **The command was tested locally only** (bash and dash), not on the lab
   VMs. A rewrite that fails part-way through `cat tmp > file` (disk full)
   can still damage `authorized_keys`; this risk existed before this round.
4. **Round 9 gaps remain:** a pending cleanup is not visible in the web UI,
   the state folders are not pruned, and there is no real `kill -9` test.

## Review round 12: the three round-eleven findings

Rounds 3–11 are kept.

**1. An atomic replacement of `authorized_keys`** (`enrol._remove_command`).
- **Which files are rewritten:** only a regular file (not a symlink) owned
  by this user that holds the tagged line. Anything else is never changed;
  the check that follows reports PRESENT or UNKNOWN.
- **The staged write:**
  1. `mktemp "$f.dssa.XXXXXX"` makes a private 0600 file in `~/.ssh`, on
     the same file system.
  2. `grep -v` writes the other lines into it.
  3. The staged copy must be complete before it is used:
     - `grep` read everything (exit 0 or 1);
     - it has exactly the original's count of other lines (`grep -c -v`
       against `wc -l`);
     - the tagged line is not in it.
  4. It gets the original's mode (`stat -c %a`, then `chmod`), so a 0400
     file stays 0400 and can now be cleaned.
  5. `mv -f` replaces `authorized_keys` in one rename. `restorecon` runs
     where it exists, for SELinux.
- **On any failure** (`mktemp`, `stat`, the write, a count mismatch,
  `chmod`, `mv`), `authorized_keys` keeps its contents and inode, and the
  staged file is removed.
- **Enrolment fix found by these tests:** `temporary_access` now adds a
  missing final newline before appending the temporary key line. Before, a
  file whose last line had no newline merged it with the new line, and a
  later removal would have deleted the user's own key.

**2. Unreadable intents are reread before any retry**
(`App.intent_allows_retry`, called in `resume_cleanup` before the retry).
- An intent that exists but cannot be read or parsed blocks the retry:
  - there is no SSH call;
  - it is never overwritten (`mark_cleanup` is not reached).
- An intent whose existence cannot be determined (`lstat` fails) also
  blocks the retry.
- Once it can be read, the retry runs. If it says `removed: true`, the
  retry only retires the connection locally.

**3. Invalid `meta.json` is invalid metadata** (`App.valid_meta`).
- A record is valid only when all of these hold:
  - it is a JSON object;
  - its `target_id` is the folder's name;
  - its tag matches `TAG_RE`;
  - its `owner` is a string;
  - its `expires` is a number;
  - its `entry` is an object.
- Anything else (a list, a string, a number, a record of another folder,
  a partial record) becomes an `unreadable` `cleanup_pending` placeholder
  at startup. Before, startup crashed or freed the name.
- `reread_pending` uses the same check, so the name stays reserved through
  every sweep until the record is valid again.

**Changed paths (round 12):**
- `bridge/webui/enrol.py`
- `bridge/webui/app.py`
- `tests/backend/test_webui.py`
- `docs/webui/claude-integration-handback.md`

**New or changed tests:**
- `TemporaryAccessTests`: the real command, under bash and dash.
  - `test_a_read_only_file_is_replaced_atomically_with_its_mode_kept`:
    unrelated lines are kept (including a last line without a newline),
    mode 0400 is kept, the inode changes, and no staged file is left.
  - `test_a_failed_step_leaves_authorized_keys_untouched`: a failing `mv`,
    `chmod`, `mktemp` or `stat`, or a lying `wc`, each put first on PATH.
    The result is a confirmed refusal; the contents and inode are
    unchanged and no staged file is left.
  - Changed `test_a_confirmed_refusal_is_told_apart`: it now uses a
    symlinked `authorized_keys`, which is never replaced. A 0400 file is
    now cleaned.
- `TemporaryServerTests`:
  - `test_an_unreadable_intent_blocks_ssh_and_stays_intact` (mode 0000)
  - `test_a_broken_intent_is_reread_and_can_turn_the_retry_local`: broken
    JSON gives no SSH call and the content is kept. After the repair to
    `removed: true`, it is retired locally, still with no SSH call.
  - `test_a_non_object_meta_keeps_the_name_through_startup_and_sweep`:
    `[]`, `"tmp1"`, `42`, another folder's record and a partial record.
    There is no crash; it is reserved through startup and sweep, and
    cleaned once the record is repaired.
- Mutation checks, with the files restored after each:
  - writing in place instead of renaming: 3 fail;
  - no reread of the intent: the broken-JSON test fails (the mode-0000
    case is also blocked by `mark_cleanup` failing to open it);
  - no `meta.json` validation: all 5 variants fail.

**Tests run** (as `vivek`, uid 1000, not root):

```bash
.venv/bin/python -m unittest tests.backend.test_webui.TemporaryAccessTests tests.backend.test_webui.TemporaryServerTests < /dev/null
```

Result: Ran 98 tests, OK.

```bash
.venv/bin/python -m unittest tests.backend.test_webui tests.backend.test_unified_apply.WebFlowTests tests.backend.test_approval_records < /dev/null
```

Result: Ran 185 tests, OK.

The full backend suite was not run this round; it is left to Codex.

**Remaining gaps:**
1. **The command needs `mktemp` and `stat -c`.** Both are in GNU coreutils
   and BusyBox; it was not tested on the lab VMs. `restorecon`'s effect was
   not checked on Rocky or Alma with SELinux enforcing.
2. **A file not owned by the user is never rewritten,** for example a
   root-owned `authorized_keys` in the user's home. Its line stays until
   the key expires (reported as PRESENT).
3. **Placeholders and unreadable intents wait for a repair.** They stay
   reserved indefinitely, even past the key's expiry.
4. **Round 9 gaps remain:** a pending cleanup is not shown in the web UI,
   the state folders are not pruned, and there is no real `kill -9` test.
