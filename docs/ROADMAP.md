# Development Roadmap

## Milestone 0 — Project Foundation

- [x] Define project problem
- [x] Define initial scope
- [x] Define V1 requirements
- [x] Define supported platform
- [x] Create Git repository
- [x] Create initial documentation

## Milestone 1 — Connection & Discovery

- [ ] Accept server connection details
- [ ] Verify SSH connectivity
- [ ] Verify authentication
- [ ] Verify sudo access
- [ ] Detect operating system
- [ ] Detect kernel
- [ ] Detect architecture
- [ ] Detect RAM
- [ ] Detect disk capacity
- [ ] Detect available disk
- [ ] Detect swap
- [ ] Generate discovery report
- [ ] Break/fix testing

## Milestone 2 — Control Panel

- [ ] Detect existing aaPanel
- [ ] Support aaPanel requirement
- [ ] Validate aaPanel installation
- [ ] Prevent conflicting installations

## Milestone 3 — Component Engine

- [ ] Component selection
- [ ] Component detection
- [ ] Dependency handling
- [ ] Installation workflow
- [ ] Component validation

## Milestone 4 — Swap Management

- [ ] Detect existing swap
- [ ] Evaluate RAM
- [ ] Evaluate available disk
- [ ] Recommend swap when required
- [ ] Create swap safely
- [ ] Persist swap configuration
- [ ] Validate swap

## Milestone 5 — Supported Components

Implement and test supported components individually.

## Milestone 6 — Final Validation

- [ ] Server readiness report
- [ ] Failure reporting
- [ ] Logging
- [ ] End-to-end tests
- [ ] Break/fix scenarios

## Milestone 7 — Portfolio Release

- [ ] Complete README
- [ ] Architecture diagram
- [ ] Usage examples
- [ ] Screenshots/demo
- [ ] Security review
- [ ] GitHub release
- [ ] LinkedIn project write-up
- [ ] External review
- [ ] Technical viva

## Backend Integration Checkpoint — 2026-10-19

### Architecture

Python coordinates requests and presents results.
The central Linux Bash backend owns SSH and privileged execution.
Destination servers use agentless SSH.

### Completed

- Web-server discovery collectors and response validation.
- Live web-server discovery passed on all six lab distributions.
- Backend SSH key selection through DSSA_SSH_KEY.
- Separate six-server lab inventory.
- Python bridge CLI for the Nginx workflow.
- Ubuntu 24.04 Nginx installer with platform, panel and port checks.
- Existing-package handling avoids repeated package installation.
- Nginx configuration validation and active-service verification.
- Installation start and outcome audit events with a shared request ID.
- Installer SHA256 recorded in installation audit events.
- 47 backend tests passed before installation audit integration.
- Integrated CLI and successful installation audit path verified live.
- Bounded local HTTP readiness check passed with HTTP 200 on Ubuntu 24.
- Python bridge checks OS compatibility before installation confirmation.
- Ubuntu 26.04 rejection verified without attempting installation.

### Current Limits

- Fresh Nginx installation branch exists but is not yet verified live.
- Installation currently supports Ubuntu 24.04 only.
- Confirmation is interactive through the lab runner.
- Python plan-bound approval is not yet enforced by the install runner.
- Installation uses a separate runner rather than the JSON dispatcher.
- Remote jq is required for web-server discovery.
- LiteSpeed edition identification currently uses Debian package metadata.
- Failed installation may leave partial changes; automatic rollback is absent.
- HTTP readiness checks the local default endpoint only; external reachability and application-specific health are not verified.

### Working Process

Develop one operational capability at a time.
Explain file responsibilities before creating or modifying files.
Use proportionate verification and record unverified behaviour.
Snapshots and VM resets are not routine requirements.

## Bash Foundation Refactor — 2026-10-19

- Bash owns environment detection and component coordination.
- Python remains the request and presentation bridge.
- Shared package functions select apt or dnf and check package presence.
- Package-changing functions are separated from discovery.
- Nginx installation and read-only verification are separate functions.
- The coordinator validates component selections before execution.
- Existing Nginx packages are skipped without changing service state.
- Integrated Ubuntu 24 existing-package path passed.
- Multi-component selection, backend planning and approval enforcement remain pending.
- Fresh-installation and dnf execution paths remain unverified.
## Multi-Component Setup — 2026-10-20

- Python passes component selections to the Bash backend.
- Bash validates all selections before connecting.
- The coordinator currently supports Nginx and Git.
- Required component functions are checked before processing.
- All module definitions are bundled before coordinator execution.
- Ubuntu 24 multi-component existing-package path passed.
- Installed packages are skipped without changing service state.
- Inventory lookup failures return a clear message and exit code 3.
- Runner renamed to setup-components.sh.
- Failure propagation regression coverage remains pending.
- Fresh-installation and dnf execution paths remain unverified.
## Shared Discovery Integration — 2026-10-20

- Added reusable, read-only panel and TCP-port discovery functions.
- Coordinator collects panel facts once and passes them to Nginx.
- Nginx uses shared discovery instead of duplicated panel-path checks.
- Port facts are refreshed before installing missing Nginx.
- Ubuntu 24 Nginx/Git existing-package workflow passed.
- Port-conflict and panel-policy rejection paths remain unverified.
- Observed coordinator failure propagated through SSH to the Python CLI.
## Bash Component Planning — 2026-10-20

- Bash generates per-component INSTALL, SKIP or REVIEW decisions.
- Plans include package manager and decision reasons.
- Duplicate selections are removed from generated plans.
- Any REVIEW decision makes the whole plan ineligible.
- Plan-only mode is available through the Python bridge.
- Ubuntu 24 plan-only run returned two SKIP decisions.
- Apply mode checks plan eligibility before processing components.
- Apply confirmation still occurs before remote plan generation.
- Execution still uses the selection loop rather than consuming plan steps.
- Binding execution to a reviewed plan remains pending.
- Execution now consumes generated plan steps rather than raw selections.
- Duplicate-selection live check passed: Nginx and Git each processed once.
- SKIP actions bypass installers and leave service state unchanged.
## Approval After Planning — 2026-10-20

- Apply mode displays the generated plan before requesting confirmation.
- Ineligible plans are rejected before execution.
- Confirmation and execution use the same in-memory plan.
- Plan-only mode bypasses execution confirmation.
- Ubuntu 24 approval flow passed with Nginx and Git SKIP actions.
- Persistent approval binding and post-review state validation remain pending.
## Installation Evidence — 2026-10-20

- Plan is displayed before interactive APPLY confirmation.
- Fresh Git installation passed on Debian 13 using APT: version 2.47.3.
- Fresh Git installation passed on AlmaLinux 10 using DNF: version 2.52.0.
- Existing-package skip path passed on Ubuntu 24.
- Fresh Nginx installation remains unverified.
## Structured Component Results — 2026-10-20

- Nginx and Git emit consistent JSON result objects.
- Coordinator validates and aggregates successful component results.
- Skipped components report verification as not_run.
- Ubuntu 24 two-component aggregation passed live.
- Structured failure responses and Python parsing remain pending.

## Python Bridge Response Parsing — 2026-10-20

- Backend emits a compact DSSA_RESPONSE JSON frame.
- Machine-readable output explicitly disables jq colours.
- Python streams terminal output and parses the response frame.
- Backend exit status is checked before accepting a response.
- Response component order is checked against deduplicated selections.
- Plan-only and approved setup summaries passed on Ubuntu 24.
- Structured failure and partial-result responses remain pending.
## Structured Failure Reporting — 2026-10-21

- Coordinator can preserve completed component outcomes on failure.
- Failed components report unknown change state.
- Remaining components are marked not_attempted.
- Runner can emit a failure frame while retaining a nonzero exit code.
- Local failure-response construction checks passed.
- Simulated backend-to-Python failure handling passed:
  backend exit 17 produced CLI exit 1.
- Real coordinator failure propagation through SSH remains unverified.

## Milestone 4.10 — Common Server Lifecycle Layer — 2026-10-31

Status: Implemented and tested (read-only scope).
Git commit: Pending.

### Completed

- Defined common lifecycle contracts and operation context.
- Implemented agentless bootstrap capability discovery.
- Implemented identity, privilege, and time-health discovery.
- Integrated common discovery with SSH, dispatcher, and Python bridge.
- Implemented evidence-based pre-operation readiness assessment.
- Implemented historical pre-operation baseline acceptance.
- Implemented immediate and historical evidence binding.
- Implemented system baseline comparison and change classification.
- Implemented post-operation health assessment.
- Implemented temporary artifact cleanup planning.
- Implemented structured lifecycle reporting.
- Implemented a read-only lifecycle coordinator.
- Added permanent lifecycle security and failure-handling tests.

### Validation Results

- Backend unit and regression suite: 177/177 passed.
- Six-VM lifecycle integration: 6/6 passed.
- Pre-operation collection failure: BLOCKED.
- Post-operation verification failure: VERIFICATION_FAILED.
- Successful read-only lifecycle: INSPECTED.
- Execution authorization: disabled.

### Known Limitations

- No application execution is performed by the lifecycle coordinator.
- Historical acceptance records are not tamper-proof.
- The lifecycle does not authorize privileged operations.
- Temporary artifact cleanup is planning-only.
- Remote filesystem ownership and symlink safety are not yet verified.
- Application-specific verification remains a separate responsibility.

### Future Work

- Durable, integrity-protected lifecycle evidence and audit records.
- Operation-specific readiness and expected-change policies.
- Controlled application execution with explicit authorization.
- Independently verified post-operation service and network checks.
- Safe remote temporary-artifact cleanup with ownership verification.
- Recovery and rollback coordination.
## Milestone 4.11 — Application Contracts and Catalog

Status: Implementation and validation completed; documentation
and Git commit pending.

Delivered:

- 23 registered application definitions.
- Central application registry and catalog loader.
- Compatibility, prerequisite and conflict evaluation.
- Expected-change and verification contracts.
- Read-only application evidence mapping.
- Application evidence coordinator.
- Target-bound application assessment integration.
- Legacy component-name and dependency mapping.
- Non-executable legacy planning adapter.
- Read-only port and control-panel discovery operations.

Validation:

- 195 backend automated tests passed.
- Six Linux lab servers tested.
- 12 port/panel discovery operations passed.
- 12 Nginx/Docker application assessments passed.
- Draft applications returned REVIEW_REQUIRED.
- No application installation was performed through Milestone 4.11.

Next:

- Establish application-specific compatibility policies.
- Define package, service, dependency and conflict requirements.
- Implement validated application-specific installation plans.
- Expand Bash discovery and verification where required.
- Design individual HestiaCP, CyberPanel and aaPanel workflows.
- Introduce separately authorized execution integration.
## Milestone 4.12 — Package-Manager-First Installation Planning

Status: Implementation and validation complete.

Delivered:

- Installation recipe contracts and validated registry.
- Six draft recipes covering four applications.
- APT and DNF recipe selection for Git and Nginx.
- OS-specific vendor-repository recipes for Docker and Compose.
- Application dependency evaluation.
- Accepted evidence binding and platform normalization.
- Target-bound, read-only installation plan assembly.
- Nginx port and panel conflict checks.

Validation:

- 218 backend tests passed.
- Six Linux lab environments tested.
- 24 real SSH-backed installation assessments passed.
- No installation operations performed.

Next milestone: 4.13 — Application State Discovery and Readiness.

Priorities:

- Detect package-managed and unmanaged applications.
- Collect installed application versions.
- Determine application service health.
- Check package and repository availability.
- Establish evidence-backed installation readiness.
## Milestone 4.13 — Application State Discovery

Status: Completed and validated; repository commit pending.

Delivered:

- Reusable Bash application-state collector.
- Strict application ID validation.
- Existing SSH transport integration.
- Remote package-manager selection.
- Package, binary, service and plugin observations.
- Dispatcher response-schema validation.
- Python controller and evidence-gate integration.
- Conservative five-state application classification.
- Package-database error handling.
- Dedicated state-classification and package-query tests.

Validation:

- 245 backend regression tests passed.
- 24 six-VM application-state assessments passed.
- 12 additional APT/DNF discovery checks passed.
- Bash and Python syntax checks passed.
- No installation operations performed.

Next: Milestone 4.14 — Application Inventory and Readiness Evidence.

Planned work:

- Detect alternative package ownership and unmanaged applications.
- Collect installed application versions.
- Verify runtime and service health.
- Establish package availability and repository identity.
- Bind application states to installation prerequisites.
- Continue keeping execution authorization separate.

### Milestone 4.14 — Application Inventory and Readiness Evidence

**Status: Implementation and validation complete**

Completed:

- Read-only package and executable inventory collector
- Application inventory discovery runner
- Secure SSH and dispatcher integration
- Controller evidence integration
- Package version and ownership observations
- Docker package-family identification
- Python inventory normalization
- Incomplete evidence and conflict classification
- Fail-closed package-record collection
- 21 inventory normalization unit tests
- 266 passing backend regression tests
- 24 passing six-VM raw inventory checks
- 24 passing six-VM normalized inventory checks

**Deferred work:**

- Docker Compose plugin-specific runtime verification
- Service/runtime health correlation
- Advanced symlink and package-ownership normalization
- Strict response validation for legacy ports and panels discovery
- Integration of inventory findings into installation-readiness decisions

All Milestone 4.14 operations remain read-only and planning-only.
### Milestone 4.15 — Unified Application Readiness

Status: Implementation and validation complete

Completed:
- Read-only readiness decision evaluator
- Live readiness evidence coordinator
- Installation recipe, state and inventory reconciliation
- Target and application identity verification
- Independent evidence freshness checks
- Duplicate discovery request-ID rejection
- Dependency and installation-plan blocker handling
- Contradictory observation handling
- Docker and Compose conservative readiness decisions
- 24 successful six-VM readiness assessments
- 290 passing backend regression tests

Deferred:
- Docker daemon health verification
- Docker Compose plugin-specific runtime verification
- Stronger cross-request snapshot consistency
- Integration with future authorized installation workflows

Execution remains disabled.
### Milestone 4.16 — Docker Runtime Verification

Status: Completed after final regression and integration validation.

Implemented:
- Docker Engine CLI runtime discovery
- Docker service and socket state observations
- Docker Compose plugin version verification
- Strict SSH and dispatcher integration
- Evidence-gated runtime interpretation
- Runtime-aware readiness reconciliation
- Eight-record runtime-aware readiness coordination
- Conservative handling of incomplete runtime evidence

Deferred:
- Explicit Docker daemon API health verification
- Compose deployment health verification
- Broader application runtime support
- Authorized installation and recovery workflows

All runtime discovery remains read-only.

### Milestone 4.17 — Docker Compose Runtime Classification

Status: Completed

Implemented:
- Recognized missing Compose command responses
- Distinguished absent plugins from inconclusive failures
- Updated SSH runtime response validation
- Updated dispatcher runtime response validation
- Added PLUGIN_ABSENT Python interpretation
- Extended runtime readiness reconciliation
- Added permanent runtime and transport regression tests
- Validated all six Linux lab environments

Validation:
- 321 backend tests passed
- 16 transport contract scenarios passed
- 12 six-VM integration assessments passed

Deferred:
- Docker daemon responsiveness verification
- Detection of alternative Compose installation layouts
- Additional installation/runtime reconciliation improvements

Execution remains disabled.

### Milestone 4.18 — Passive Docker Daemon Preflight

Status: Implementation and validation complete; publication pending.

Completed:

- Read-only Bash daemon preflight collector.
- Approved zero-parameter SSH discovery runner.
- SSH and dispatcher strict response validation.
- Python discovery evidence allowlisting.
- Independent evidence-gated Python classification.
- Passive-only safety and consistency checks.
- 17 Docker preflight unit tests passed.
- 338 total backend regression tests passed.
- 6/6 Linux VM integration assessments passed.
- 5/5 synthetic systemd-state scenarios passed.

Observed lab results:

- Ubuntu 22/24/26, Debian 13, AlmaLinux 10, Rocky Linux 10:
  DAEMON_INACTIVE.
- Ubuntu workstation: PASSIVE_CANDIDATE observed.

Scope exclusions:

- No Docker API health verification.
- No socket-triggering connection.
- No daemon installation or service modifications.
- No rootless Docker endpoint discovery.
- No change to application execution authorization.

Follow-up work:

- Review safe active API health-check strategies separately.
- Distinguish missing Docker units from installed inactive services.
- Extend endpoint discovery for rootless and nonstandard Docker installations.
- Consider integrating passive results into readiness reconciliation
  with separate evidence identity and freshness validation.

### Milestone 4.19 — Docker Installation-State Differentiation

Status: Implementation validated; GitHub publication pending.

Completed:

- Added systemd LoadState collection.
- Distinguished missing, masked, inactive, and failed Docker units.
- Preserved passive Docker process and socket checks.
- Updated strict SSH and dispatcher JSON contracts.
- Extended Python classification and evidence validation.
- Added permanent negative transport contract tests.
- Validated 11 synthetic collector scenarios.
- Validated 36 SSH and dispatcher contract scenarios.
- Passed 6/6 Linux VM integration assessments.
- Passed 342/342 backend regression tests.

Lab findings:

- Ubuntu 22/24/26, Debian 13, AlmaLinux 10, Rocky Linux 10:
  DAEMON_UNIT_NOT_FOUND.
- Ubuntu workstation:
  PASSIVE_CANDIDATE with LoadState=loaded.

Safety constraints preserved:

- No Docker API connections or daemon health claims.
- No service changes or package installations.
- No container execution.
- No automatic recovery.
- No execution authorization.

Remaining future work:

- Rootless Docker and alternative endpoint discovery.
- Optional passive-readiness integration.
- Separately designed and safety-reviewed active daemon verification.

### Milestone 4.20 — Passive Docker Readiness Reconciliation

Status: Complete — publication pending.

Completed:
- Introduced pure passive daemon readiness reconciliation.
- Added nine-source Docker readiness coordination.
- Preserved existing runtime and application readiness behavior.
- Validated unique evidence identities and source consistency.
- Preserved blockers and conservative readiness decisions.
- Added positive, negative, and cross-source regression tests.
- Passed 373/373 backend tests.
- Passed full nine-source assessment on six Linux VMs.

Verified environments:
- Ubuntu 22.04
- Ubuntu 24.04
- Ubuntu 26.04
- Debian 13
- AlmaLinux 10
- Rocky Linux 10

All six assessments returned REVIEW_REQUIRED with
CLI_ABSENT and DAEMON_UNIT_NOT_FOUND.

No Docker API probe, installation, service activation, or
execution authorization was introduced.

### Milestone 4.21 — Structured Docker Readiness Explanation

Status: Implementation and integration validated;
GitHub publication pending.

Completed:
- Created a pure Docker readiness explanation layer.
- Added structured finding codes and investigation guidance.
- Supported three Docker runtime classifications.
- Supported six passive daemon classifications.
- Preserved original readiness reasons and evidence identities.
- Added permanent positive and negative regression tests.
- Verified deterministic and non-mutating behavior.
- Passed 18/18 classification combinations.
- Passed 14/14 explanation regression tests.
- Passed 387/387 backend tests.
- Passed 6/6 Linux VM integration assessments.

No additional SSH operations, Docker API probes, installations,
service changes or execution permissions were introduced.

### Milestone 4.22 — Human-Readable CLI Readiness Reports

Status: Implementation validated; final closure verification pending.

Implemented:
- Extended the existing CLI with read-only Docker assessments.
- Added text and JSON report rendering.
- Added assessment argument validation.
- Preserved the legacy component CLI.
- Added deterministic report and evidence validation.
- Added sanitized assessment failure handling.
- Added native subprocess stdout isolation.
- Added permanent renderer and CLI regression tests.

Validated before final closure:
- 17/17 renderer tests.
- 10/10 CLI tests.
- 414/414 backend tests.
- 6/6 CLI assessments across Ubuntu 22, Ubuntu 24, Ubuntu 26,
  Debian 13, AlmaLinux 10 and Rocky Linux 10.
- Native stdout isolation on successful and failed assessments.
- No setup operation invoked by assessment CLI.

Final full regression, end-to-end testing, and GitHub publication
are required before marking the milestone complete.

### Milestone 4.23 — Unified Read-Only Workflow

Status: Implementation and integration validated.
Final end-of-day closure and publication pending.

Implemented:

- Shared workflow request contract
- Strict inventory target and operation validation
- Read-only application workflow coordinator
- Existing nine-source readiness pipeline integration
- Explanation consistency validation
- CLI migration to shared coordinator
- Preservation of text and JSON report compatibility
- Preservation of legacy entry points

Permanent regression coverage:

- 13 workflow contract tests
- 11 workflow coordinator tests
- 12 assessment CLI tests
- 440 complete backend tests

Integration coverage:

- Ubuntu 22.04
- Ubuntu 24.04
- Ubuntu 26.04
- Debian 13
- AlmaLinux 10
- Rocky Linux 10

Six-VM workflow comparison: PASS
Six-VM migrated CLI assessment: PASS
Twelve CLI text/JSON executions: PASS
Unknown inventory target rejection: PASS

Safety:

- Read-only assessment
- No installation authorization
- No Docker API health claims
- Nine unique evidence identities per assessment
- Legacy setup behavior remains separate

Remaining before completion:

- Final regression and end-to-end closure
- Documentation and staged-change review
- Git commit and GitHub publication
