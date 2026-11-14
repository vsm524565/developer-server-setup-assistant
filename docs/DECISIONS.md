# Engineering decisions and rationale

This is a record of decisions supported by published source and the latest recorded lab work. It is not a mandate to redesign the application.

## ADR-001 — Python coordinates, Bash owns Linux operations

**Status:** Adopted. Python handles user interaction, evidence interpretation and reporting. The Linux Bash backend handles inventory-backed SSH, discovery and allowlisted setup. Targets are agentless; avoid introducing a permanent agent as a prerequisite.

**Why:** Fit a systems-operation workflow that already uses SSH/Bash and supports multiple package managers.

## ADR-002 — Separate read-only application assessment from mutation

**Status:** Adopted. `bridge/applications/` recipes and workflow coordinator are **DRAFT, planning-only**. The operational Bash component runner has its own allowlist and an interactive `APPLY` gate. Do not connect them implicitly or claim that a read-only recipe authorizes execution.

**Known gap:** No unified, durable evidence-bound authorization covering the legacy installer. Interactive `main.py` has a plan-binding demonstration without execution.

## ADR-003 — Package-manager-first, conservative installation

**Status:** Adopted. Prefer APT on appropriate Debian/Ubuntu targets and DNF on supported RPM-family targets, with fixed package mappings. Git was the first complete real installation lifecycle, verified on Debian 13 (APT) and AlmaLinux 10 (DNF).

Do not silently replace unmanaged binaries, conflict packages or existing service data. Classify uncertain state as `REVIEW`, not `INSTALL`.

## ADR-004 — Make operation outcomes evidence-backed

**Status:** Adopted. Results are structured JSON; a setup failure has nonzero exit status and `COMPONENT_FAILED`, preserves completed results and distinguishes failed from not attempted. Package installation is not transactional, so retain partial-state warnings.

## ADR-005 — Verification must match application semantics

**Status:** Adopted. Git skip must run `git --version` verification. Docker Engine cannot be declared healthy from package presence, CLI presence or passive socket/service state alone; an active daemon/API check belongs to separately approved verification.

## ADR-006 — Docker vendor repository and platform policy

**Status:** Active, uncommitted development decision. Initial live target is Ubuntu 24.04 amd64 with vendor APT repository and packages `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`. Compose plugin installation is separate and depends on Engine. Check conflicts, package ownership and prior repository/data/service evidence *before* modification.

**Known safety issue addressed locally:** Generic component planner initially admitted Docker `INSTALL` on unsupported OSs. Dedicated dispatch fixed it; six-VM plan checks then correctly selected Ubuntu 24 only. Those modifications still require publication.

## ADR-007 — Fail rather than bypass package authentication

**Status:** Adopted for uncommitted Docker installer. Verify vendor signing-key fingerprint and preserve existing repository state. A failed signing-key download or mismatch blocks installation; no unauthenticated repository fallback.

## ADR-008 — Finish one operational component before abstraction

**Status:** Adopted. Scope progression: Git installation and idempotency → Docker Engine installation lifecycle → Docker Compose plugin lifecycle → reuse patterns only after demonstrated success. Avoid architecture churn during an unfinished application milestone.

## ADR-009 — Keep laboratory evidence and credentials out of Git

**Status:** Adopted. Inventory is local and access-controlled; strict known-host checking remains enabled. Never stage `.venv-pre-upgrade-backup/`, `config/inventory/lab-six.json.bak`, `planning/execution_readiness.py` as part of this docs effort, and never publish SSH keys, usernames tied to production servers, IPs or sensitive logs.

## ADR-010 — Documentation is independent of unfinished source work

**Status:** Adopted for 2026-11-14. The requested documentation snapshot is published on a separate branch `docs/engineering-history-20261114` created at the confirmed development commit `46f8637`; it does not overwrite the user's uncommitted Docker changes or merge into `main`. Reconcile on the workstation before integrating docs with the active development branch.

See [PROJECT_STATE](PROJECT_STATE.md) for the exact next task and [ARCHITECTURE](ARCHITECTURE.md) for data flow.
