# Multi-Component Planning (Milestone 4.31)

Status: **Implemented in 4.31** (planner, request integrity, Python plan validator). Apply-time behaviour in §6 is built in 4.33–4.34. Builds on `component-definitions.md` (4.30).

Implementation: `backend/linux/engine/plan.sh`, `bridge/components/plan.py`, `tests/backend/test_multi_component_plan.py`.

## 1. Goal

Turn a selection of components into one ordered, dependency-aware plan
for one server, using the 4.30 single-component planner for each
entry. The plan is what you approve in 4.32 and what apply runs in 4.34.

Your rule (2026-11-14): all selected components run in one go. If one
cannot run, its dependents are skipped; independent components
continue; everything is reported.

## 2. What changes from the legacy planner

| | Legacy (`build_component_plan`) | 4.31 |
|---|---|---|
| Rules | Hard-coded per component | Definitions (4.30 engine) |
| Order | Selection order | Dependencies first, then selection order |
| Dependencies | None | `depends_on` from definitions |
| One REVIEW item | `eligible: false`: **nothing runs** | Only that item and its dependents are held |
| Output | `steps[]` with `action` | `steps[]` plus `runnable`, dependency links and definition hashes |

## 3. Inputs

- **Selected component IDs**, validated against `definitions/components/`
  before connecting (unknown ID → error, as today).
- **Discovery facts** gathered once: package manager, panels, ports.
- **Definitions** for the selection plus their dependencies; only these
  are sent to the target.

## 4. Steps

```text
selection ──► resolve dependencies ──► order ──► plan each (4.30 engine)
                                                   │
                       propagate holds to dependents ◄┘
                                                   │
                                        plan document (JSON)
```

1. **Resolve.** Add required dependencies that were not selected
   (decision A). Reject unknown IDs and cycles (already checked by the
   loader).
2. **Order.** Topological order: every dependency comes before its
   dependents. Ties keep selection order (decision C).
3. **Plan each** with `plan_component_from_definition`. This gives each
   entry its own `decision` and `action`.
4. **Propagate.** Walk in order. If a **required** dependency's action is
   `REVIEW` or `BLOCKED`, the dependent's action becomes `BLOCKED`, with
   `blocked_by: ["<id>"]` and a reason. This is transitive. Optional
   dependencies affect order only.
5. **Summarize.** `runnable` lists the entries whose action is `INSTALL`
   or `SKIP` (SKIP still runs verification).

## 5. Plan document

```json
{
  "schema_version": "1.0",
  "plan_id": "<uuid>",
  "created_at": "2026-11-16T10:00:00Z",
  "target": { "target_id": "ubuntu24", "host_key_sha256": "..." },
  "package_manager": "apt",
  "selection": ["docker-compose"],
  "definitions": { "docker": "sha256:...", "docker-compose": "sha256:..." },
  "steps": [
    {
      "component": "docker",
      "added_as_dependency": true,
      "depends_on": [],
      "variant_id": "ubuntu24-amd64-vendor",
      "decision": "INSTALL",
      "action": "INSTALL",
      "reasons": ["..."]
    },
    {
      "component": "docker-compose",
      "added_as_dependency": false,
      "depends_on": ["docker"],
      "decision": "INSTALL",
      "action": "INSTALL",
      "blocked_by": [],
      "reasons": ["..."]
    }
  ],
  "runnable": ["docker", "docker-compose"],
  "held": [],
  "planning_only": true,
  "execution_allowed": false
}
```

- `definitions` records a SHA-256 for every definition used. 4.32 binds
  the approval to the plan hash, so a changed definition invalidates an
  approval.
- `target.host_key_sha256` ties the plan to one server's identity.
- `held` lists every entry not in `runnable`, with its reason.

## 6. Apply-time behaviour this plan enables (built in 4.33/4.34)

Defined now so the plan carries the data apply needs:

- Components run in plan order.
- **Re-check before each component.** Its single-component plan is
  re-run on the target right before it executes. If the decision has
  changed since approval (drift), that component does not run
  (decision D).
- **On failure:** roll back that component only (4.33). Its required
  dependents become `not_attempted` with `blocked_by`; independent
  components continue.
- The final report lists every step as `installed`, `verified`
  (SKIP), `failed_rolled_back`, `held` or `not_attempted`, with reasons.

## 7. Where it runs

The ordering and propagation run in jq on the target, in
`backend/linux/engine/plan.sh`, alongside the 4.30 engine. The
laptop-side script only resolves the dependency closure, so it knows
which definition files to bundle. Python validates and displays the
plan but never re-decides it (the architecture rule).

## 8. Decisions (2026-11-15)

- **A. Missing dependency: auto-add.** Required dependencies that were
  not selected are added, marked `added_as_dependency: true`, and shown
  in the plan you approve.
- **B. Partial plans: run the rest.** A plan with held entries can be
  approved. Only `runnable` entries run; held ones and their dependents
  are reported.
- **C. Tie order: selection order**, after dependencies are placed
  first.
- **D. Drift at apply: skip and continue.** A component whose re-checked
  decision differs from the approved plan does not run, nor do its
  required dependents. Independent components continue. All of it is
  reported as drift.

## 9. Tests (planned)

- Pure jq tests for ordering, tie-breaks, transitive propagation,
  optional dependencies, auto-added dependencies and `runnable`/`held`.
- Test-only definitions (e.g. `alpha` → `beta` → `gamma`) in a temp
  directory, so dependency rules are tested before real components
  declare dependencies.
- Legacy parity: for Git, Nginx and Docker (no dependencies), each
  step's action equals the 4.30 single-component result. `eligible`
  (legacy) equals `held` being empty.
- Six-VM read-only plan of `docker git nginx` together.
