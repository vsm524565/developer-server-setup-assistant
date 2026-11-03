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

## Application Architecture — Milestone 4.11

DSSA provides a reusable application catalog with 23 registered
application definitions.

### Application Layer

Located under `bridge/applications/`.

Responsibilities:

- Register applications using stable application IDs.
- Declare application categories and requirements.
- Evaluate operating-system compatibility.
- Evaluate prerequisites and conflicts.
- Describe expected changes and verification requirements.
- Map accepted Linux discovery evidence to application observations.
- Produce read-only application planning assessments.
- Translate selected application decisions to the legacy planner.

### Application Categories

- Web servers
- Databases
- Caching
- Containers
- Development runtimes
- Development tools
- Reverse proxy and SSL
- Control panels
- System utilities

### Controller and Backend Responsibilities

The Bash backend collects actual server observations and performs
server-side operations.

The Python controller validates discovery evidence, applies application
policies, resolves planning decisions, and coordinates the lifecycle.

Application discovery uses the existing dispatcher and SSH transport.

Two read-only discovery operations were added:

- `discovery.ports`
- `discovery.panels`

### Evidence and Planning

Application planning uses:

1. An explicitly registered application definition.
2. Application-required discovery operations.
3. Target-bound discovery evidence.
4. Freshness and evidence acceptance checks.
5. Mapping of observed server facts to declared requirements.
6. Compatibility, prerequisite, and conflict evaluation.

An accepted evidence gate permits read-only planning only.

### Installation Boundary

All 23 application definitions remain DRAFT.

Draft applications cannot become installation-ready through the
application planning adapter.

The new application framework does not invoke the existing Bash
installation entry point or authorize privileged operations.

Existing installation capabilities remain separate and unchanged.

Control-panel directory indicators do not independently establish
that a control panel is installed correctly or operational.

### Legacy Compatibility

The application catalog uses stable IDs such as `docker_compose`,
whereas the older planner uses component names such as `Docker Compose`.

An explicit translation adapter preserves compatibility without
modifying the existing dependency resolver.

Docker Engine is ordered before Docker Compose when resolving the
corresponding legacy dependency.

### Current Limitations

- Application-specific installation policies remain incomplete.
- Application-specific execution and recovery are not integrated.
- Most application prerequisites lack dedicated discovery evidence.
- Evidence envelopes are controller-created but not cryptographically
  authenticated as independent execution authorization.
- Discovery observations may change between separate SSH operations.
- Port discovery identifies listeners but not owning processes.
- Control-panel discovery uses known filesystem indicators.
- Application verification declarations are not executable probes.
## Application Installation Planning — Milestone 4.12

DSSA uses package-manager-oriented draft installation recipes
under `bridge/applications/installation/`.

### Recipe Selection

- The detected package manager determines the general installation method.
- The OS family and architecture constrain recipe applicability.
- Exact OS versions are required only when declared by a recipe.
- A wildcard OS version represents broad draft applicability,
  not verified support for every release.
- Package availability, repository trust and installation safety
  require additional evidence before execution.

### Registered Recipes

Six draft recipes cover four applications:

- Git: APT for Ubuntu/Debian; DNF for AlmaLinux/Rocky.
- Nginx: APT for Ubuntu/Debian; DNF for AlmaLinux/Rocky.
- Docker Engine: Ubuntu 24.04 vendor-repository recipe.
- Docker Compose: Ubuntu 24.04 vendor-repository recipe.

### Evidence and Dependencies

Installation planning reuses the existing Linux discovery backend,
target-bound evidence collection and freshness validation.

Docker Compose depends on Docker Engine. The current discovery
system cannot establish Docker package ownership and daemon health,
so the dependency remains UNKNOWN.

Nginx planning checks listening ports and known control-panel
installation indicators.

### Execution Boundary

All recipes have DRAFT status.

Generated plans describe package sources, dependencies, expected
changes and verification requirements.

They are not executable instructions or authorization tokens.
The existing Bash installation backend remains separate.

### Remaining Work

- Package ownership and installed-version discovery.
- Package availability and repository validation.
- Service health and unmanaged-installation detection.
- Application-specific compatibility and conflict policies.
- Stronger approval and evidence binding before execution.
## Application State Discovery — Milestone 4.13

DSSA provides read-only application-state discovery through the
existing controller, dispatcher and agentless SSH backend.

### Architecture

1. Python requests `discovery.application_state` for an approved application.
2. The dispatcher validates the request and application identifier.
3. The SSH transport resolves the target through backend-owned inventory.
4. The remote Bash bundle determines the package manager.
5. The application-state collector gathers package, binary, service
   and plugin observations.
6. The SSH transport and dispatcher validate the JSON response.
7. The Python evidence gate validates target identity and freshness.
8. The application-state interpreter classifies accepted observations.

### Application Coverage

Initial applications:

- Git
- Nginx
- Docker Engine
- Docker Compose

### Application States

- INSTALLED_HEALTHY
- MISSING
- UNMANAGED
- UNHEALTHY
- UNKNOWN

INSTALLED_HEALTHY currently indicates passing basic installation
and service-presence checks, not full functional verification.

Docker Engine and Docker Compose classification remains conservative
because alternative installation methods and ownership are not yet
fully identified.

### Package Database Handling

APT/dpkg and DNF/RPM package inventories are queried without
modifying packages or repositories.

Successful inventory queries can establish whether an expected
package is installed or absent from the package database.

Failed or ambiguous inventory queries produce UNKNOWN.

Package absence alone does not establish application absence.

### Execution Boundary

All application-state operations are read-only.

The application-state interpreter does not generate installation
commands, invoke installers or authorize execution.

Application discovery is not yet connected to installation readiness.

### Validated Environments

- Ubuntu 22.04
- Ubuntu 24.04
- Ubuntu 26.04
- Debian 13
- AlmaLinux 10
- Rocky Linux 10

Validation: 245 backend tests passed and 24 live application-state
discovery/classification assessments completed successfully.
### Application Inventory Evidence Pipeline — Milestone 4.14

The application inventory operation follows the existing layered architecture:

`Python Controller → Dispatcher → SSH Transport → Remote Bash Collector → JSON Validation → Evidence Gate → Inventory Normalizer`

**Operation:** `discovery.application_inventory`

**Request parameter:** `application_id`

Supported application identifiers are `git`, `nginx`, `docker` and `docker_compose`.

The remote inventory collector uses package-manager queries to identify recognized packages, package versions, executable paths and package ownership. Package queries are read-only, and the collector rejects failed package-state queries rather than returning partial candidate lists.

The SSH and dispatcher layers validate application identifiers and inventory response structure. The Python controller maintains target-bound evidence and freshness checks.

The normalizer provides installation-identification statuses:

- `IDENTIFIED`: Recognized installation with consistent package and executable ownership evidence
- `ABSENT`: Recognized packages and executable are absent
- `REVIEW_REQUIRED`: Conflicting or incomplete installation evidence requires review
- `UNKNOWN`: Available evidence does not establish the installation state

Package ownership does not establish service health or application functionality. Docker Compose plugin availability is not confirmed by the presence of the Docker executable.

The inventory operation does not authorize package installation or other system modifications. `execution_allowed` remains `false`.

**Known limitation:** The legacy ports and control-panel discovery routes require dedicated strict response-schema validation. This remains a separate hardening task.
### Unified Application Readiness Architecture — Milestone 4.15

The readiness coordinator combines three existing subsystems:

1. Installation recipe planning (Milestone 4.12)
2. Application-state interpretation (Milestone 4.13)
3. Package inventory normalization (Milestone 4.14)

Readiness coordination:
- Builds the existing draft installation plan
- Collects application-state evidence
- Collects application-inventory evidence
- Verifies evidence identity and freshness
- Rejects duplicate discovery request IDs
- Normalizes state and inventory observations
- Evaluates a unified planning-only readiness decision

Implementation:
- bridge/applications/readiness/evaluator.py
- bridge/applications/readiness/coordinator.py

The evaluator performs no SSH communication or installations.
The coordinator delegates discovery to the existing Linux backend.

Evidence is collected in separate requests, not as an atomic
snapshot. Conflicting or incomplete observations require review.

READY_INSTALLED indicates agreement between the existing
application-state and package-inventory checks. It is not a
comprehensive application-health guarantee.

INSTALL_CANDIDATE_DRAFT indicates that a draft recipe is suitable
for further planning; it is not authorization to execute.

All readiness results retain:
- planning_only: true
- execution_allowed: false
### Runtime-Aware Application Readiness — Milestone 4.16

Data flow:

Bash runtime collector
  -> approved SSH discovery runner
  -> strict dispatcher response validation
  -> Python evidence gate
  -> runtime interpreter
  -> existing application readiness coordinator
  -> runtime reconciliation policy

Modules:
- backend/linux/modules/software/application_runtime.sh
- backend/linux/discovery/application_runtime.sh
- bridge/applications/runtime/interpreter.py
- bridge/applications/readiness/runtime_reconciliation.py
- bridge/applications/readiness/runtime_coordinator.py

A runtime-aware assessment combines seven existing readiness evidence
records with one runtime evidence record. Request identities must
be unique and the execution policy must remain disabled.

Docker daemon responsiveness remains unverified. Runtime evidence
cannot promote Docker or Compose to fully healthy readiness.
