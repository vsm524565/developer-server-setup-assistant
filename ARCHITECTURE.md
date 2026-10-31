# Developer Server Setup Assistant — Architecture

## Purpose

Developer Server Setup Assistant is a modular application
for discovering, planning, verifying, and eventually
performing controlled server configuration operations.

## Architecture

Frontend → Python Bridge → Central Linux Bash Backend
                                      ↓
                                Agentless SSH
                                      ↓
                              Destination Servers

## Frontend

Responsibilities:
- Present server information
- Display installation plans
- Collect operator input and approvals
- Display execution results and audit history

The frontend does not execute system commands.

## Python Bridge

Responsibilities:
- Accept component selections and operator input
- Validate request structure
- Invoke the Bash backend through the local JSON interface
- Present backend-generated plans for review
- Return approval responses to the backend
- Display results and audit information

Restrictions:
- No destination SSH connections or credentials
- No direct destination-server commands
- No duplicated dependency, compatibility or installation rules

## Central Linux Bash Backend

Responsibilities:
- Resolve targets and manage SSH credentials
- Verify connectivity, server identity and sudo access
- Discover OS, package manager, panels, components and ports
- Resolve component dependencies and installation conflicts
- Generate installation plans
- Validate approval against the operation context
- Install missing components through allowed package methods
- Verify results without changing existing services implicitly
- Record operational and security events

Implementation:
- Shared functions provide reusable discovery and package operations.
- Component functions contain application-specific steps.
- An operation coordinator processes selected components.
- Discovery and verification remain read-only.
- Package installation failures do not trigger another package manager.

## Communication

The Python bridge communicates with the backend
through a restricted local invocation interface.

Requests and responses use versioned JSON contracts.

The backend validates operations independently.

## Security

- SSH host identity verification
- Backend-controlled credential access
- Operation allowlisting
- Parameter validation
- Restricted execution privileges
- Approval binding and state consistency checks
- Audit logging
- No secret values in logs

## Logging

Operational and audit records use JSON Lines.

Each event includes:
- Timestamp
- Event ID
- Request ID
- Source
- Operation
- Target identifier
- Status
- Error or result metadata

Logs persist beyond application execution.

Rotation and retention policies are configurable.


## Common Server Lifecycle Layer — Milestone 4.10

The Common Server Lifecycle Layer provides reusable pre-operation
and post-operation assessment for Linux servers.

The current implementation is read-only. It does not authorize
application installation, privileged changes, remediation, or remote cleanup.

### Lifecycle Workflow

1. Collect controller-side discovery evidence.
2. Validate evidence freshness, target identity, and discovery operation.
3. Assess common server readiness.
4. Accept and preserve a pre-operation system baseline.
5. Establish a read-only operation boundary.
6. Collect independent post-operation evidence.
7. Compare pre-operation and post-operation system baselines.
8. Evaluate post-operation health and cleanup status.
9. Generate a structured lifecycle report.

### Common Discovery

The Linux backend provides:

- `discovery.bootstrap` — common command availability.
- `discovery.common_health` — identity, privileges, and time health.
- `discovery.system` — operating system and resource baseline.

The Python controller collects evidence using the existing
agentless Linux backend and validates it before assessment.

### Python Lifecycle Components

Location: `bridge/lifecycle/`

- `contracts.py` — lifecycle context and assessment contracts.
- `pre_operation.py` — common readiness assessment.
- `pre_acceptance.py` — accepted baseline snapshots.
- `evidence_binding.py` — immediate and historical evidence comparison.
- `baseline_comparison.py` — observed system change classification.
- `post_operation.py` — post-operation health and comparison results.
- `artifact_cleanup.py` — temporary artifact cleanup planning.
- `reporting.py` — lifecycle outcome classification.
- `coordinator.py` — read-only lifecycle orchestration.

### Readiness and Reporting

Common readiness statuses:

- READY
- DEGRADED
- BLOCKED
- UNKNOWN
- UNSUPPORTED

Lifecycle reporting distinguishes successful read-only inspection
(`INSPECTED`) from actual operation outcomes such as `SUCCESS`,
`PARTIAL`, `FAILED`, `BLOCKED`, and `VERIFICATION_FAILED`.

Readiness for planning does not grant execution permission.

### Evidence and Change Classification

Evidence is collected and timestamped by the controller.

Historical baseline acceptance allows a previously accepted
pre-operation observation to be compared with fresh post-operation
evidence without requiring the original observation to remain fresh.

Baseline changes are classified as:

- REVIEW_REQUIRED — identity or other review-sensitive changes.
- CAPACITY_CHANGE — total resource capacity changes.
- OBSERVED_CHANGE — ordinary observed resource changes.

These classifications do not independently establish whether an
application operation succeeded.

### Temporary Artifact Cleanup

DSSA tracks temporary artifact identities, paths, and originating
lifecycle requests.

The current cleanup module creates planning records only.

Cleanup plans explicitly report that filesystem ownership and
filesystem safety have not been verified, and execution is disabled.

Future deletion support requires dedicated staging ownership,
canonical-path and symlink-safe verification, protection against
pre-existing files, and explicit cleanup authorization.

### Current Trust Boundaries

- Historical acceptance snapshots are not tamper-proof.
- Read-only inspections do not perform application operations.
- Application-specific prerequisites remain within application modules.
- Cleanup planning does not perform file deletion.
- No privileged remediation or swap activation is authorized.
- Future execution workflows require separate approval and verification.

### Validation

Milestone 4.10 was validated with:

- 177 passing backend regression tests.
- Successful read-only lifecycle inspections across six lab VMs:
  Ubuntu 22.04, Ubuntu 24.04, Ubuntu 26.04, Debian 13,
  AlmaLinux 10, and Rocky Linux 10.
- Failure handling for pre-operation and post-operation collection.
- Cleanup planning and lifecycle identity validation.

These results validate the current read-only implementation,
not production-ready remote execution or cleanup.

## Future Extensions

- PHP/JavaScript frontend
- Windows PowerShell backend
- InfraGuard integration
- Multi-server management
- Versioned API
- Centralized audit storage

## Architectural Principle

Frontend presents.
Python processes and coordinates.
Platform-specific backends execute.
Significant actions are auditable.
