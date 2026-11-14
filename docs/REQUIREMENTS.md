# Project Requirements — V1 goals and current evidence

**Project:** Developer Server Setup Assistant (DSSA)  
**Source baseline:** `architecture/layered-platform` at `46f8637` (2026-11-12).  
**Status:** V1 still in development. Requirements and implementation status are intentionally separated.

## Objective and intended user

Provide a guided tool for developers who already have a Linux server, SSH credentials, sudo privileges and application requirements, but may not have the Linux administration expertise to prepare it manually.

The goal is infrastructure preparation, not application deployment.

## Core functional requirements

| ID | Requirement | Implementation / verification status |
|---|---|---|
| F01 | Accept server host/IP, SSH port, sudo username, credentials | Implemented in interactive `main.py`; input validations exist |
| F02 | Verify SSH connection, authentication and host key | Implemented, tested in lab; strict host-key decisions preserved |
| F03 | Verify sudo privileges | Implemented, positive and negative lab checks recorded |
| F04 | Discover hostname/OS/kernel/architecture/CPU/RAM/disk/swap | Implemented in interactive and/or Linux backend discovery |
| F05 | Detect existing control panels | aaPanel discovery exercised; panel installation not certified |
| F06 | Select components and resolve dependencies/conflicts | Implemented in interactive planning; fixed Bash Git/Nginx selections |
| F07 | Detect installed/absent/unmanaged packages and runtimes | Read-only inventory and state discovery implemented |
| F08 | Install missing approved components | Git live APT/DNF validated; Nginx fresh-install and Docker not yet certified |
| F09 | Verify existing or installed components | Git live verification passed; Docker API checks remain future active-install validation |
| F10 | Inspect swap and recommend safe capacity | Discovery/planning present; actual swap creation/activation unverified |
| F11 | Produce final deployment-readiness report | Partial readiness/reporting available; full V1 deployment readiness not complete |
| F12 | Report errors, completed steps and partial state | Structured Bash setup failure frame and Python parsing implemented; recovery automation absent |

## Required discovery fields

Hostname; operating system and version; kernel; architecture; CPU count; total and available memory; total and available disk; active swap; recognized control panel(s); installed web servers, database and cache packages; observed ports. Each result must be marked unknown or review-required when unavailable, rather than fabricated.

## Safety and non-functional requirements

- Follow **Detect → Decide → Change → Verify**.
- Require verified SSH identity and sudo authorization for privileged operations.
- Strict host-key checking; never disable validation to simplify testing.
- Reject unsupported OS/architecture/package-manager combinations from the relevant installer. Discovery support and execution support differ.
- Do not silently reinstall a healthy package or replace an unmanaged executable.
- Plan-only workflows must not install, refresh repositories, start services or delete files.
- Do not interpret passive service/socket presence as successful Docker daemon API verification.
- Before live modification, show an eligible allowlisted plan and explicitly require operator approval; treat stale/mismatched plans and incomplete discovery conservatively.
- Keep Python draft recipes `planning_only=true`, `execution_allowed=false` until a separate execution authorization mechanism is actually implemented and verified.
- Return deterministic machine-readable results and fail closed on malformed response/evidence.
- On failures, preserve completed results; warn that partial changes may remain; do not promise transactional rollback.
- Use fixed component mappings and package-manager functions instead of arbitrary user-provided shell commands.
- Minimize sensitive logs and audit data; never commit credentials, private keys, user inventory or server-specific secrets.
- Validate on disposable lab VMs first; do not modify production infrastructure as part of testing/documentation.

## Platform scope

Initial historical V1 proposal targeted Ubuntu 22.04/24.04 on x86_64; later read-only test coverage included Ubuntu 26, Debian 13, AlmaLinux 10 and Rocky 10. These test environments do **not** imply supported installation targets. Explicitly observed Git installation targets were Debian 13 (APT) and AlmaLinux 10 (DNF). Uncommitted Docker work is scoped to Ubuntu 24.04 amd64 only.

## Component inventory and priorities

Candidate applications: Git, Docker Engine, Docker Compose, Nginx, Apache, MySQL/MariaDB, PostgreSQL, Redis, PHP, Node.js, Python runtime, Certbot, swap management and optional control panels. The application catalog contains 23 definitions, many of which are read-only drafts. Implement one component at a time.

Current build order: Git (published validated) → Docker Engine Ubuntu 24.04 (in progress) → Docker Compose (planned). Nginx fresh-install validation, swap activation and control-panel installers remain incomplete.

## V1 exclusions

- Deploying the developer's application.
- A web frontend or public REST API.
- Multi-user RBAC.
- Becoming a monitoring platform or replacing InfraGuard.
- Any guarantee of automatic recovery/rollback.

An inventory file is used by the current Linux backend despite the original requirement's initial exclusion of a *centralized server inventory*; an operator-managed local lab inventory is not a centralized inventory service.

## Acceptance criteria for completed V1

- All supported selections have explicit platform/version policy.
- Fresh installation, existing-installation, idempotency, conflicts and break/fix tested against declared platforms.
- Required discovery is complete or conservatively classified.
- Approval, execution and post-install verification are bound to the same reviewed operation.
- Partial changes and errors are surfaced safely.
- Security review, documentation and complete regression pass.
- Supported end-to-end setup outcomes verified on lab targets and published with reproducible evidence.

See [PROJECT_STATE](PROJECT_STATE.md) for latest checkpoint and [TESTING](TESTING.md) for evidence.
