# Web UI — Temporary Access and Single Approval (draft for decision)

Status: decided with the project owner, 2026-11-25: temporary key (A),
60-minute window, tick plus typed risky names, apply by the approver only.
These are `docs/REQUIREMENTS.md`, "Web UI Integration Requirements",
items 2 and 3. Built 2026-11-26 with unit tests; the lab check is
`docs/testing/manual-webui-items-2-3.md`.

Built as proposed, plus:
- A key that can't log in right after install is removed through the
  same password session, before the password is dropped.
- `DSSA_SSH_KEY` is ignored for temporary servers (it would override their
  key).
- A temporary server is its owner's only, for planning, approval, apply,
  report and resume.
- After a failed run, access stays until the window or session ends, so
  Report and Resume rollback still work.
- After the lab run (2026-11-26):
  - A temporary server's plan and run pages are its owner's only.
  - Only the user who started a run can type into its terminal; this
    applies to saved servers too.
  - Log out is audited.

## Item 2 — Optional server saving

The requirement:
- Collect SSH details and verify the host key, as Add server does today.
- Save the server only when the user ticks **Save server**.
- Otherwise use an expiring connection, scoped to that user, for plan and
  apply. It must create no inventory entry and leave no authorized key on
  the target.

Today, enrolment uses the password once, through SSH_ASKPASS, to install
the master's key. It then saves the server to the inventory. Every later
step logs in with that key (`dssa-run.sh`: `BatchMode`, a pinned host key,
`ControlMaster=no`).

### Ways to get a temporary connection

| | How | For | Against |
|---|---|---|---|
| **A. Temporary key** (proposed) | Make a new ed25519 key for this setup only. Install it with the password once, the way enrolment does. The `authorized_keys` line carries `expiry-time="<now + window>"` and `from="<master address>"`. Plan and apply use a temporary inventory entry kept under the user's session folder. When the run ends, the user cancels or the window passes, the line is removed and the private key deleted. | `dssa-run.sh` stays unchanged. The password is used once and dropped, as in enrolment. If cleanup fails, OpenSSH itself refuses the key after the window. | The key exists on the server for the run's duration. It needs OpenSSH 8.2 or newer on the target; every supported OS has it. |
| B. Shared SSH connection | Log in once with the password and keep the connection open (`ControlMaster`/`ControlPersist`) for the window. Plan and apply reuse it. | No key is ever written on the server. | Changes `dssa-run.sh`'s SSH core, which disables connection sharing on purpose. A long apply can outlive the connection. Report and resume need it still open. |
| C. Keep the password | Hold the password in memory and give it to every SSH call. | Simple. | Breaks the rule that the web UI never holds credentials. |

### Proposed details for A

- **Window:** 60 minutes from the host-key check, and never past the end
  of the user's session. A run that is still going keeps the key until it
  ends; the expiry-time on the line is the hard limit.
- **Scope:** the temporary server belongs to the user who made it. It is
  not in Saved servers, and other users can't see or use it.
- **Cleanup:** remove only the exact line DSSA added, matched by its
  unique comment. Then delete the private key and the temporary entry.
  The audit log gets `temp_access_created` and `temp_access_removed`.
  When removal fails, it gets `temp_access_remove_failed` and the page
  shows the expiry time.
- **Save server ticked:** enrolment runs exactly as today, with no
  temporary key.

**Decision 2a (2026-11-25):** A, the temporary key.
**Decision 2b (2026-11-25):** a 60-minute window.

## Item 3 — Plan approval and jobs

The requirement:
- Show the target, the planned action and the backend's findings.
- Require one explicit approval for the reviewed plan, with Apply as a
  separate action.
- Keep the plan and host-key binding, expiry, single-use approval and sudo
  verification.
- Use the existing jobs for progress, results and logs.

Most of this exists:
- The approval binds to the plan file's hash and the host key, and lasts
  30 minutes. It can be used only once.
- Apply is separate, logs in before it uses the approval, and sudo asks on
  the server.

What changes is the plan page's layout (target, action and findings first,
as in the design preview) and how approval is confirmed.

| | Confirming the approval | Typed names for risky actions |
|---|---|---|
| **Keep** | type `APPROVE`, as today | yes (decision 4) |
| **Proposed** | tick "I reviewed the target, operation and warnings", then **Approve plan** | yes: remove, adopt, risky updates, panels, drafts |
| Lightest | tick and Approve | no |

The command line keeps typing `APPROVE`, unchanged. The web UI would pass
the same confirmation to `approve_plan` once the box is ticked, so the
binding, expiry and single-use checks stay identical.

Tests for this change:
- an approval for a plan that changed is refused;
- an expired approval is refused;
- a second Apply of a used approval is refused;
- an approval made by one user can be applied only by that user (new: the
  approval record already stores the operator).

**Decision 3a (2026-11-25):** Proposed: tick, plus typed names for risky actions.
**Decision 3b (2026-11-25):** only the user who approved may apply.
