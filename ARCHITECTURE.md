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

#### Runtime Classification Contract — Milestone 4.17

Docker Compose runtime discovery preserves the distinction between:

1. Successful version verification
2. Explicit command unavailability
3. Inconclusive execution failure

The Bash collector produces plugin states:
available, absent and unknown.

The SSH and dispatcher layers independently validate these states,
including application binding, CLI consistency and disabled execution.

The Python interpreter maps the observations to:
PLUGIN_AVAILABLE, PLUGIN_ABSENT and UNVERIFIED.

Runtime reconciliation preserves REVIEW_REQUIRED when complete
application health cannot be established and retains existing
BLOCKED decisions.

The Docker daemon is not contacted during this discovery workflow.
Daemon responsiveness remains unverified.

### Docker Daemon Passive Preflight Architecture (4.18)

Operation: `discovery.docker_daemon_preflight`

Read-only data flow:

1. Python LinuxBackend receives an approved discovery request with `{}` parameters.
2. Linux dispatcher validates the operation and parameters.
3. SSH backend constructs and executes the approved remote Bash bundle.
4. Bash collector reads systemd, process, socket, and listener metadata.
5. SSH backend and dispatcher independently validate the JSON response.
6. Python evidence collection attaches controller timestamps and request identity.
7. Evidence gate evaluates freshness, operation, and target identity.
8. Python interpreter independently validates and classifies the passive observations.

Implementation components:

- `backend/linux/modules/software/docker_daemon_preflight.sh`
- `backend/linux/discovery/docker_daemon_preflight.sh`
- `backend/linux/connection/ssh.sh`
- `backend/linux/bin/dispatcher.sh`
- `bridge/adapters/linux_backend.py`
- `bridge/applications/runtime/docker_daemon_preflight.py`
- `tests/backend/test_docker_daemon_preflight.py`

Security and reliability boundaries:

- Discovery accepts no execution parameters.
- The collector does not contact the Docker API.
- No active probing is authorized by a passive classification.
- Planning requires accepted, fresh evidence.
- Raw collector and interpreted results preserve unknown daemon responsiveness.
- `execution_allowed` remains false throughout the pipeline.
- Existing Docker runtime readiness reconciliation is unchanged.

The implementation assesses passive evidence only. A separate design,
safety review, and test plan are required for active API health verification.

### Milestone 4.19 — Docker Service Unit State Extension

The existing `discovery.docker_daemon_preflight` operation now collects
systemd Docker service LoadState in addition to ActiveState, MainPID,
process identity, and socket observations.

The response schema includes:

- `service.load_state`
- `service.state`
- `service.main_pid`
- `service.process_verified`

Supported LoadState values:

- loaded
- not-found
- masked
- error
- bad-setting
- unknown

Classification is implemented consistently across:

1. The remote Bash collector.
2. The SSH response validator.
3. The dispatcher response validator.
4. The Python passive-preflight interpreter.

The Python evidence wrapper continues to independently verify request
identity and evidence freshness.

The extension does not modify application runtime readiness
reconciliation or introduce active Docker API probing.

Transport validation tests execute the actual SSH and dispatcher jq
expressions against synthetic positive and negative responses.

All operations remain planning-only and execution-disabled.

### Milestone 4.20 — Nine-Source Daemon-Aware Readiness

The daemon-aware coordination layer extends the established
runtime-aware readiness workflow without changing existing
Bash collectors, SSH transport, or evidence-gate behavior.

Pipeline:

1. assess_application_readiness() — seven evidence records.
2. assess_runtime_aware_readiness() — eighth runtime record.
3. discovery.docker_daemon_preflight — ninth passive record.
4. classify_docker_daemon_preflight_evidence() — independent
   validation of operation, target, freshness, and safety policy.
5. reconcile_daemon_readiness() — pure reconciliation without
   discovery or infrastructure modifications.

Implementation modules:
- bridge/applications/readiness/daemon_coordinator.py
- bridge/applications/readiness/daemon_reconciliation.py

The resulting decision preserves nine unique request IDs,
never upgrades readiness using passive daemon evidence,
and never claims verified Docker API responsiveness.

Milestone 4.20 applies to Docker only. Docker Compose continues
to use its existing independent runtime assessment.

### Milestone 4.21 — Docker Readiness Explanation Layer

Module:
`bridge/applications/readiness/readiness_explanation.py`

The pure `explain_docker_readiness()` function consumes an existing
nine-source daemon-aware readiness result.

Pipeline:

1. Existing application readiness assessment.
2. Existing runtime-aware readiness reconciliation.
3. Existing passive daemon preflight reconciliation.
4. Structured readiness explanation generation.

The explanation layer performs structural and policy validation,
including:
- Docker application identity.
- Supported readiness and runtime classifications.
- Supported daemon preflight classifications.
- Nine unique evidence request IDs.
- Matching daemon classification reason.
- Planning-only and execution-disabled policy.

It returns stable structured findings without changing the input
result, reclassifying readiness, or executing operations.

Evidence freshness is validated upstream by the existing coordinators.
The explanation layer does not independently authenticate raw evidence.

Docker Compose remains outside this explanation contract.

### Milestone 4.22 — Read-Only Assessment CLI

Components:

- bridge/cli.py
- bridge/assessment_cli.py
- bridge/applications/readiness/report_renderer.py

Execution flow:

1. The existing CLI dispatches the "assess" command.
2. The assessment parser validates the application and target ID.
3. LinuxBackend performs approved discovery.
4. The daemon-aware readiness coordinator validates and reconciles
   nine discovery evidence records.
5. The explanation layer generates structured findings.
6. The report renderer validates the reporting contract.
7. The CLI prints the completed text or JSON report.

The assessment path is separate from the existing setup workflow.

Output isolation:

- Python stdout and inherited FD 1 output are suppressed during
  synchronous discovery and report preparation.
- The original stdout descriptor is restored before report output.
- Assessment failures produce a generic stderr error message.
- Direct stderr emissions from external tools are not fully sanitized.
- FD 1 redirection is process-wide and unsuitable for concurrent
  application execution without additional isolation.

Safety:

- No setup_components() call from assessment CLI.
- No Docker API health probe.
- No installation or service modification.
- No repair or execution authorization.
- Original nine-source readiness decisions are preserved.


### Milestone 4.23 — Unified Application Workflow Coordinator

The assessment CLI delegates read-only application assessment
requests to a shared workflow layer.

Components:

- bridge/workflows/contracts.py
- bridge/workflows/coordinator.py
- bridge/assessment_cli.py

Execution flow:

1. CLI parses and validates command-line arguments.
2. CLI constructs an inventory-managed workflow request.
3. Workflow contract validates the request schema and identity.
4. Workflow coordinator invokes existing daemon-aware readiness.
5. Existing backend collects and validates nine evidence sources.
6. Explanation layer produces structured Docker findings.
7. Coordinator validates explanation and readiness consistency.
8. Coordinator returns a planning-only workflow result.
9. CLI validates the workflow envelope.
10. Existing report renderer generates text or JSON output.

Workflow contract:

- schema_version: 1.0
- workflow: readiness_assessment
- target.kind: inventory
- application_id: docker
- max_age_seconds: positive numeric value up to 300

Workflow response:

- schema_version
- check: application_workflow_result
- workflow
- target
- application_id
- status
- result: validated Docker readiness explanation
- planning_only: true
- execution_allowed: false

Security boundaries:

- Unsupported operations and applications are rejected.
- Raw SSH target information is not accepted.
- Unexpected request fields are rejected.
- Target and application identities must match throughout.
- Nine discovery request identities remain unique.
- Contradictory explanation and readiness results are rejected.
- No installation approval or execution permission is generated.
- Docker API health remains unverified.
- The legacy setup path is not invoked by the assessment workflow.

The existing stdout-isolation mechanism remains in the CLI.
It prevents Python and inherited subprocess stdout from corrupting
reports during synchronous discovery.

The coordinator is independent of CLI rendering and can support
future interfaces without duplicating discovery logic.

The original interactive application is not yet migrated to
the shared workflow architecture.

### Milestone 4.24 — Capability Registry and Handler Boundaries

DSSA separates application definitions, supported workflow
capabilities, and implemented workflow handlers.

Core modules:

- bridge/applications/catalog.py
- bridge/applications/registry.py
- bridge/workflows/contracts.py
- bridge/workflows/registry.py
- bridge/workflows/handlers.py
- bridge/workflows/coordinator.py

Request lifecycle:

1. Receive an inventory-managed workflow request.
2. Validate schema, fields, target and freshness constraints.
3. Confirm that the capability registry supports the request.
4. Validate the registered capability's read-only policy.
5. Resolve an explicitly implemented workflow handler.
6. Reject missing or mismatched handlers.
7. Execute the existing evidence-gated Docker assessment.
8. Validate readiness and explanation consistency.
9. Return a planning-only workflow result.

Current implemented combination:

    docker / readiness_assessment / inventory

Handler identity:

    docker_readiness_v1

Default capability registrations are derived from the implemented
handler catalog.

Registry operations do not perform SSH discovery or installation.

The coordinator retains a separate capability-policy check even
though the workflow contract validates registered support.

The Docker-specific evidence and explanation pipeline remains
unchanged.

Future application handlers must be implemented and tested before
their capabilities are advertised.

All current workflow results retain:

    planning_only = true
    execution_allowed = false

### Milestone 4.25 — Offline Capability Discovery Interface

New modules:

- bridge/workflows/discovery.py
- bridge/capability_cli.py

Modified entry point:

- bridge/cli.py

Workflow:

1. CLI receives the capabilities subcommand.
2. The capability CLI parses text or JSON formatting arguments.
3. The discovery service builds the existing workflow registry.
4. The registry exposes explicitly supported capabilities.
5. Discovery validates handler availability and read-only policy.
6. The capability CLI renders a deterministic report.

The capability interface does not construct LinuxBackend, perform
SSH discovery, or invoke application installation.

JSON report contract:

    schema_version: "1.0"
    check: "workflow_capabilities"
    capabilities: list

Each capability contains:

    application_id
    workflow
    target_kind
    planning_only
    execution_allowed

The capability report validator rejects malformed schemas,
unauthorized fields, duplicate capabilities, unsupported handlers,
unsafe policy values and unsorted capability declarations.

The existing assess command and legacy setup CLI entry points are
preserved.

### Milestone 4.26 — Discovery Contract Hardening

The capability discovery contract is enforced at two boundaries.

**Discovery boundary**

`bridge/workflows/discovery.py` builds the capability report from the
workflow registry, sorts declarations by their identity tuple and
validates the complete report.

The validator enforces:

- Exact top-level and capability fields
- Supported schema version and report check identity
- Nonempty string capability identities
- Unique application/workflow/target-kind combinations
- Strictly sorted capability declarations
- Resolution to implemented workflow handlers
- Read-only planning and execution-denied policy

Successful validation returns a deep copy.

**CLI boundary**

`bridge/capability_cli.py` validates the requested output format before
validating the report. The complete report must pass validation before
text or JSON is rendered.

Expected discovery and rendering failures use a sanitized error message
and a nonzero exit code. Unsupported CLI formats are rejected by
argument parsing.

The capability CLI remains offline. No SSH connection, installation
backend or execution authorization is introduced by this milestone.

**Compatibility**

The existing capability report schema remains version `1.0`.
The `assess` command, original component setup CLI and interactive
`main.py` workflow remain separate from capability discovery.

### Milestone 4.27 — Multi-Application Read-Only Workflow Dispatch

Milestone 4.27 extends the workflow architecture to support two
implemented readiness assessment handlers without introducing
installation or execution authority.

#### Workflow entry points

The shared assessment command accepts:

- `docker`
- `docker_compose`

Both applications use inventory-managed targets and the existing
workflow request schema version `1.0`.

#### Capability registration

`bridge/workflows/handlers.py` declares the implemented handler
identities:

- `docker_readiness_v1`
- `docker_compose_readiness_v1`

`bridge/workflows/registry.py` constructs the capability registry
from the implemented handler catalog.

Capabilities remain limited to:

- Workflow: `readiness_assessment`
- Target kind: `inventory`
- Planning only: `true`
- Execution allowed: `false`

Unsupported application/workflow combinations are rejected before
backend discovery.

#### Docker Engine workflow

```text
Assessment CLI
    |
    v
Workflow request validation
    |
    v
Capability and handler validation
    |
    v
assess_daemon_aware_readiness()
    |
    v
Nine-source Docker readiness
    |
    v
explain_docker_readiness()
    |
    v
validate_explanation()
    |
    v
Docker text / JSON report
```

The Docker workflow retains passive daemon preflight evidence.
It does not claim successful Docker API responsiveness.

#### Docker Compose workflow

```text
Assessment CLI
    |
    v
Workflow request validation
    |
    v
Capability and handler validation
    |
    v
assess_runtime_aware_readiness()
    |
    v
Eight-source Compose readiness
    |
    v
explain_compose_readiness()
    |
    v
validate_compose_explanation()
    |
    v
Compose text / JSON report
```

The Compose workflow evaluates runtime plugin classification.
It does not perform daemon preflight or Compose project execution.

#### Compose reporting modules

`bridge/applications/readiness/compose_explanation.py`

- Constructs a structured Compose readiness explanation.
- Validates application and target identity.
- Requires eight distinct evidence identities.
- Requires the matching runtime classification reason.
- Preserves the execution-denied policy.
- Produces independent report data.

`bridge/applications/readiness/compose_report.py`

- Validates the explanation schema.
- Rejects contradictory runtime findings.
- Rejects malformed evidence and execution authorization.
- Renders deterministic text and JSON reports.
- Does not connect to a backend or modify the server.

#### Shared coordinator safety boundary

`bridge/workflows/coordinator.py` resolves the registered handler
before dispatching to the appropriate readiness coordinator.

After assessment, it checks:

- Target identity
- Application identity
- Readiness status agreement
- Runtime classification agreement
- Evidence request identity agreement
- Source reason agreement
- Planning-only policy
- Execution-denied policy
- Unverified daemon responsiveness

Docker Engine additionally requires the daemon preflight
classification in the explanation to match the readiness result.

The coordinator returns the existing outer
`application_workflow_result` contract.

#### Compatibility and safety

The existing Docker nine-source workflow remains supported.

The Compose workflow introduces a separate eight-source
reporting contract rather than adding daemon evidence to Compose.

The original interactive `main.py` application, legacy setup CLI,
and component installation paths remain separate from these
read-only assessment workflows.

No installation, service modification, container deployment,
or automatic remediation is introduced by Milestone 4.27.
