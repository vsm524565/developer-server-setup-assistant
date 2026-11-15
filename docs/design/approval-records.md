# Approval Records (Milestone 4.32)

Status: **Implemented in 4.32** (`bridge/components/approval.py`, `tests/backend/test_approval_records.py`). Apply (4.34) calls `consume_approval`. Builds on `multi-component-planning.md` (4.31).

## 1. Goal

`main.py` runs **plan → approve → apply** as three distinct steps
(your decision, 2026-11-14). The approve step writes an **approval
record** bound to one exact plan. Apply (4.34) refuses to start unless
a matching, unexpired, unused approval exists.

4.32 builds the record, the approve step's checks (preflight), and the
validation apply will call. It does not run anything on a server.

## 2. What exists and what is reused

| Existing (`planning/`, original `main.py` flow) | 4.32 |
|---|---|
| `approval_binding.py`: SHA-256 of target + host key + snapshot + plan; `hmac.compare_digest` | Same idea: binding by hash with a constant-time compare |
| `approval_session.py`: approval kept **in memory** only | Approval **persisted** as a record, so a separate apply step can check it |
| `preflight.py`: old plan format (`VERIFY_EXISTING`, `dependencies`) | New preflight for the 4.31 plan document (§5) |
| `execution_readiness.py` (uncommitted): three gates (preflight, state consistency, approval) | Same three-gate shape for apply readiness (§7) |

The old modules stay untouched for the original `main.py` flow until
4.34 replaces it.

## 3. What an approval binds to

The record stores the **SHA-256 of the exact plan file bytes**, not a
re-serialized hash. Python and jq serialize JSON differently (key
order, escaping, numbers), so hashing the stored bytes avoids any
cross-language mismatch. Because the plan document already contains:

- `plan_id`, `created_at`
- `target.target_id` and `target.host_key_sha256`
- a SHA-256 for every definition used
- every step, `runnable` and `held`

any change to any of these breaks the binding.

## 4. Record layout

```text
~/.local/state/developer-server-setup-assistant/approvals/   (0700)
└── <plan_id>/                                                (0700)
    ├── plan.json          the plan exactly as the backend returned it (0600)
    ├── approval.json      the approval record (0600)
    └── consumed           created atomically when apply starts (§6)
```

```json
{
  "schema_version": "1.0",
  "approval_id": "<uuid>",
  "plan_id": "<uuid from the plan>",
  "plan_sha256": "sha256:<hex of plan.json bytes>",
  "target_id": "ubuntu24",
  "host_key_sha256": "SHA256:...",
  "approved_components": ["git"],
  "approved_by": "<local OS user>",
  "approved_at": "2026-11-16T10:05:00Z",
  "expires_at": "2026-11-16T10:35:00Z",
  "single_use": true
}
```

- `approved_components` equals the plan's `runnable` list. Held
  components can never be approved. To change what runs, re-plan.
- `approved_by` records the local operator for audit.
- Approval and consumption are both written to the JSONL audit log
  (`backend/linux/logging/audit.sh`).

## 5. Approve step (preflight)

Before writing a record, the approve step checks:

1. **Plan is valid**: `validate_plan_document` (4.31) passes.
2. **Plan is fresh**: `created_at` is within the approval window.
3. **Definitions unchanged**: `changed_definitions(plan)` is empty.
4. **Target identity**: the plan's host-key fingerprint equals the
   one in the operator's `known_hosts` for that target right now.
5. **Something to approve**: `runnable` contains at least one INSTALL.
   A plan whose runnable entries are all SKIP needs no approval (§8).
6. **Operator confirmation**: the plan summary is shown (runnable, held
   with reasons, auto-added dependencies), and the operator types the
   `APPROVE` (decision D).

Any failure means no record is written, and the reason is shown.

## 6. Single use

Apply consumes the approval by atomically creating `consumed` (an
exclusive create, so two applies can't both claim it). A consumed
approval is never valid again. Re-running needs a new plan and a new
approval, because the server has changed (decision C).

## 7. Apply readiness (validation 4.34 will call)

`check_approval(plan_id)` returns READY only when all gates pass:

| Gate | Check |
|---|---|
| Record | `approval.json` and `plan.json` exist, are regular files (not symlinks) owned by the user with mode 0600, and parse |
| Binding | `sha256(plan.json)` equals `plan_sha256` (constant-time compare); `plan_id`, `target_id` and `host_key_sha256` match the plan |
| Freshness | now is before `expires_at` |
| Unused | no `consumed` marker |
| Scope | `approved_components` equals the plan's `runnable` |
| Definitions | `changed_definitions(plan)` is still empty |
| Identity | the host key is unchanged in `known_hosts` (SSH also enforces this with `StrictHostKeyChecking=yes`) |

On the server, the per-component re-check and drift handling from
4.31 §6 still apply on top of this.

## 8. Verification-only plans

If every runnable entry is SKIP, apply only runs verification checks,
which are read-only. Proposal: **no approval needed** for such plans,
matching the original `main.py` behaviour ("verification-only plan").
Apply still requires a fresh, valid plan.

## 9. Security limits (stated honestly)

An approval record shows operator intent and binds integrity to one
plan. It is **not** cryptographic authorization against someone who
already controls the operator's laptop account: they could write a
record. Protection there comes from file permissions and the audit
log. Signing records with a key (as InfraGuard does) can be added later
if DSSA gets multiple operators.

## 10. Decisions (2026-11-15)

- **A. Location: on the machine DSSA runs from.** Records and logs live
  on the control machine running DSSA (today the laptop), never on the
  target servers. The path is the per-user state directory,
  `~/.local/state/developer-server-setup-assistant/approvals/`, next to
  the audit log. If DSSA later runs from another machine, its records
  live there.
- **B. Window: 30 minutes** from the plan's `created_at`.
- **C. Single use.** Apply consumes the approval when it starts.
- **D. Confirmation: type `APPROVE`.** The plan's short hash is still
  shown on screen for reference; the record binds to the full hash
  regardless of what is typed.

## 11. Tests (planned)

- Record round trip; permissions 0700/0600; symlink and wrong-owner
  rejection.
- Every gate fails independently: tampered plan byte, wrong target or
  host key, expired, consumed, scope mismatch, changed definition,
  changed `known_hosts`.
- Exclusive consumption: second consume fails.
- Preflight refusals: invalid, stale, nothing to install,
  verification-only.
- Audit events written for approve and consume.
