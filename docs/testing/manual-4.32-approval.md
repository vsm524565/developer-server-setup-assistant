# Manual Test — Milestone 4.32 Approval Records

Interactive test, run one step at a time with the helper
`docs/testing/manual-4.32.sh`. Each step is one self-contained command.

- Nothing changes a server. `plan` is a read-only plan on rocky10
  (192.168.56.115), run as `dssa-test` without sudo.
- Approval files and audit events go only to `$HOME/dssa-manual-4.32`.
- Run from the repository root. Approving needs a real terminal, because
  you type `APPROVE`.
- The approval window is 30 minutes from the plan's `created_at`.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `bash docs/testing/manual-4.32.sh setup` | Test folder | Empty `$HOME/dssa-manual-4.32` |
| 2 | `… plan` | Read-only plan on rocky10 | git `INSTALL`, `runnable: ["git"]` |
| 3 | `… approve` (type `APPROVE`) | Preflight, confirmation, record | `Approved: ['git']`; expiry = `created_at` + 30 min; `drwx------` / `-rw-------` |
| 4 | `… check` | All seven gates | `READY`, all `True` |
| 5 | `… check expired` | freshness | `BLOCKED`, only `freshness False` |
| 6 | `… check wrong-key` | identity | `BLOCKED`, only `identity False` |
| 7a | `… tamper && … check` | binding | `BLOCKED`, only `binding False` |
| 7b | `… restore && … check` | binding restored | `READY` |
| 8 | `… approve` (type `APPROVE`) | One record per plan | `REFUSED: This plan already has an approval record.` |
| 9a | `… plan plan2` | Second read-only plan | New `plan_id` |
| 9b | `… approve plan2` (type `yes`) | Exact confirmation | `REFUSED: Approval not confirmed; no approval written.` |
| 10 | `… consume` (after the first plan's window) | Expired claim | `REFUSED … expired`, no `consumed` file |
| 11a | `… approve plan2` (type `APPROVE`) | Fresh approval | `Approved: ['git']` |
| 11b | `… consume plan2` twice | Single use | First `Consumed`, second `REFUSED … already been used`; `consumed` is `-rw-------` |
| 12a | `jq -c '{timestamp, event, status, actor, target_id, plan: .request_id[0:8]}' ~/dssa-manual-4.32/audit/security.jsonl` | Audit trail | Two `approval.granted`, one `approval.consumed`; no events for refusals |
| 12b | `… cleanup` | Clean up | Removes `$HOME/dssa-manual-4.32` |

`…` stands for `bash docs/testing/manual-4.32.sh`.

## Result — 2026-11-15

Run by the project owner on rocky10: **12/12 passed**.

- Plan 1 `ef7a48ee` approved at 06:34:55Z. READY; expired, wrong-key and
  tamper each failed only their own gate; restore returned to READY;
  second approval refused; consume at 07:02:15Z refused as expired with
  no marker written.
- Plan 2 `f278ac59`: `yes` refused; `APPROVE` approved at 07:03:27Z;
  consumed once at 07:04:24Z; second consume refused.
- Audit log: exactly three events, none for refusals.

Two helper-script bugs were found and fixed during the run (stdin used
for the prompt; `/dev/tty` opened in a non-seekable read-write mode).
Neither was in DSSA itself.
