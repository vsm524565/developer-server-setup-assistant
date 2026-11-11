# Developer Server Setup Assistant

A developer-focused Linux server preparation tool being built as part of a
hands-on DevOps engineering project.

## Problem

Developers frequently receive a Linux server for deploying an application,
but preparing that server may require Linux administration knowledge.

Before deploying their application they may need to:

- Install Docker and Docker Compose
- Configure a web server
- Install a database
- Install Redis
- Prepare language runtimes
- Configure swap
- Install a control panel
- Verify system resources
- Validate that required services are running

The goal of this project is to automate the server preparation process while
allowing the developer to remain responsible for deploying their application.

## Project Goal

The tool will connect to a developer-provided Linux server, verify access,
discover the existing server environment, allow required components to be
selected, install missing requirements, and validate that the server is ready
for application deployment.

## Planned Workflow

Server Details
→ SSH Verification
→ Sudo Verification
→ Server Discovery
→ Control Panel Detection
→ Requirement Selection
→ Existing Component Detection
→ Installation of Missing Components
→ Swap Evaluation
→ Validation
→ Readiness Report

## Initial Platform Support

V1 development will initially target:

- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- x86_64 systems

Additional operating systems may be introduced after the initial
implementation is stable.

## Planned Components

- Docker Engine
- Docker Compose
- Nginx
- Apache
- MySQL / MariaDB
- PostgreSQL
- Redis
- PHP
- Node.js
- Python runtime
- Git
- Certbot
- Swap management

### Control Panel

Initial control-panel support:

- aaPanel

## Current Status

🚧 Project under active development.
### Common Server Lifecycle — Milestone 4.10

The project now includes a reusable, read-only common server lifecycle layer.

Implemented capabilities:
- Agentless Linux bootstrap and common-health discovery.
- Pre-operation readiness assessment and evidence validation.
- Historical baseline acceptance and before/after comparison.
- Post-operation health assessment.
- Temporary artifact cleanup planning.
- Structured lifecycle reporting and read-only coordination.

Validation:
- 177 backend tests passed.
- Integrated lifecycle verified across six Linux lab VMs.
- Failure handling and cleanup planning safeguards tested.

Current scope:
- Read-only inspection and planning.
- No application execution or privileged remediation.
- No remote temporary-file deletion.
- No swap creation or activation.

Future milestones will integrate approved application execution,
operation-specific verification, safe cleanup, and recovery workflows.

Current milestone:

**Milestone 1 — SSH connection, sudo verification and server discovery**

No production use is recommended at this stage.
### Application Catalog

DSSA includes a read-only catalog of 23 application definitions
covering web servers, databases, caching, containers, runtimes,
development tools, SSL, control panels and system utilities.

Applications are assessed using target-bound Linux discovery
evidence and Python planning policies.

Supported catalog entries are currently DRAFT definitions.
Registration does not imply compatibility, installation readiness,
or execution authorization.

The framework includes read-only discovery for listening TCP
ports and known control-panel installation indicators.

Application-specific installation workflows will be implemented
in subsequent milestones.
### Application Installation Planning

DSSA includes six draft installation recipes covering Git,
Nginx, Docker Engine and Docker Compose.

Git and Nginx use package-manager-specific APT/DNF recipes.
Docker and Compose currently use Ubuntu 24.04 vendor-repository
draft recipes.

Plans are generated from accepted Linux discovery evidence.
They identify applicable recipes, dependency states, potential
changes and verification requirements.

All planning is read-only. Matching a recipe does not establish
package availability, compatibility certification or permission
to execute an installation.
### Application State Discovery

DSSA supports read-only discovery of Git, Nginx, Docker Engine
and Docker Compose across supported APT/DNF Linux environments.

The collector gathers installed-package evidence, executable
presence, service status and Docker Compose plugin observations.

Accepted evidence is interpreted into:

`INSTALLED_HEALTHY`, `MISSING`, `UNMANAGED`, `UNHEALTHY`,
and `UNKNOWN`.

The initial health classification is limited to basic installation
and service checks. Docker installation ownership and alternative
installation methods require additional discovery.

All application-state results are observational and cannot
authorize package installation.
### Application Inventory and Installation Identification — Milestone 4.14

DSSA supports read-only application inventory discovery for Git, Nginx, Docker Engine and Docker Compose.

The inventory collector identifies:

- Recognized package candidates and installed package versions
- Executable presence and resolved path
- Package-manager-reported executable ownership
- APT and DNF installation evidence
- Unrecognized, incomplete or conflicting installation observations

The Python inventory normalizer classifies observations as `IDENTIFIED`, `ABSENT`, `REVIEW_REQUIRED` or `UNKNOWN`.

Docker package families are evaluated separately to distinguish distribution packages, vendor packages and alternatives. Docker Compose remains unverified until plugin-specific runtime evidence is available.

All collected inventory remains planning-only. Application installation, package modification and service configuration are not enabled.

**Validation:** 266 backend tests passed, including 21 inventory normalization tests. Raw and normalized inventory checks passed across six Linux test environments, with 24 assessments each.
### Unified Application Readiness — Milestone 4.15

DSSA combines installation recipes, application-state discovery
and package inventory into a unified read-only readiness assessment.

Supported applications:
- Git
- Nginx
- Docker Engine
- Docker Compose

Readiness outcomes:
- READY_INSTALLED
- INSTALL_CANDIDATE_DRAFT
- REVIEW_REQUIRED
- BLOCKED

Readiness decisions use target-bound discovery evidence, package
ownership observations, application-state classification and
draft installation plans.

Docker and Docker Compose remain conservative when runtime or
plugin verification is incomplete.

All readiness assessments are planning-only. They do not authorize
package installation or server modifications.

Validation: 290 backend tests passed and 24 readiness assessments
passed across six Linux test environments.
### Docker Runtime Verification — Milestone 4.16

DSSA performs read-only runtime discovery for Docker Engine and
Docker Compose, independently of package inventory.

The runtime collector observes Docker CLI availability, CLI version,
systemd service/socket state and Compose plugin availability.

Runtime evidence passes through the existing SSH dispatcher, evidence
gate, interpreter and runtime-aware readiness coordinator.

CLI availability and active services do not establish Docker daemon
health. The daemon is not probed, and readiness stays conservative.

All results remain planning-only with execution_allowed=false.

#### Docker Compose Detection Improvements — Milestone 4.17

DSSA distinguishes three Docker Compose runtime conditions:

- PLUGIN_AVAILABLE: The Docker Compose version command succeeds.
- PLUGIN_ABSENT: The Docker CLI explicitly reports the Compose
  command as unavailable.
- UNVERIFIED: Runtime verification remains inconclusive.

PLUGIN_ABSENT does not prove that all standalone Compose
installations or packages are missing.

Runtime discovery remains read-only. It does not install packages,
restart services, create containers or probe the Docker daemon.

Validation:
- 321 passing backend regression tests
- 16 passing SSH/dispatcher transport contract scenarios
- 12 successful runtime-aware assessments across six Linux VMs

### Passive Docker Daemon Preflight — Milestone 4.18

DSSA supports read-only Docker daemon preflight through the approved
`discovery.docker_daemon_preflight` operation.

The preflight observes:

- systemd Docker service and socket-unit states.
- Docker service MainPID and process identity.
- `/run/docker.sock` presence, permissions, and ownership.
- Listening Unix socket metadata without connecting to the socket.

Classification results:

| Classification | Meaning |
|---|---|
| PASSIVE_CANDIDATE | Docker process and expected listening socket observed |
| DAEMON_INACTIVE | systemd reports Docker service inactive |
| DAEMON_FAILED | systemd reports Docker service failed |
| DAEMON_UNVERIFIED | Passive observations cannot establish a supported state |

Safety guarantees:

- Does not connect to the Docker API.
- Does not invoke Docker CLI health commands.
- Does not start, restart, or install Docker.
- Does not create or manage containers.
- Always reports `probe_attempted=false`.
- Always reports `daemon_responsive=null`.
- Always reports `execution_allowed=false`.

Important limitations:

- PASSIVE_CANDIDATE does not establish API responsiveness or daemon health.
- DAEMON_INACTIVE does not distinguish a stopped installed service from
  an absent Docker systemd unit.
- The current collector targets the conventional rootful systemd Docker
  service and `/run/docker.sock`.
- Rootless Docker and alternative endpoints are not verified.
- Passive observations are snapshots and may become stale.

Active Docker API verification is outside Milestone 4.18.

### Docker Service Unit Differentiation — Milestone 4.19

DSSA extends passive Docker daemon preflight with systemd `LoadState`
inspection.

The collector distinguishes the following service-unit conditions:

| Classification | Meaning |
|---|---|
| DAEMON_UNIT_NOT_FOUND | docker.service is not found by systemd |
| DAEMON_UNIT_MASKED | docker.service is masked |
| DAEMON_INACTIVE | docker.service is loaded but inactive |
| DAEMON_FAILED | docker.service is loaded but failed |
| PASSIVE_CANDIDATE | Loaded service, verified dockerd process, and listening socket observed |
| DAEMON_UNVERIFIED | Available passive evidence is inconclusive |

Important limitations:

- A missing docker.service unit does not prove Docker is uninstalled.
- Rootless Docker and nonstandard service managers are outside this check.
- PASSIVE_CANDIDATE does not establish Docker API responsiveness.
- Passive observations may change after collection.

The operation remains read-only and accepts empty parameters only.

Safety properties:

- `probe_attempted=false`
- `daemon_responsive=null`
- `read_only=true`
- `execution_allowed=false`

No Docker API connections, container operations, installations,
service starts, or service restarts are performed.

### Milestone 4.20 — Passive Docker Readiness Reconciliation

DSSA combines nine independently identified discovery records into
a unified Docker planning assessment:

- Seven application-readiness discovery records.
- One Docker application-runtime record.
- One passive Docker daemon-preflight record.

The reconciled assessment distinguishes Docker CLI availability,
systemd service-unit state, and passive daemon process/socket evidence.

Safety guarantees:
- Evidence freshness and target identity are independently validated.
- Duplicate evidence request IDs are rejected.
- Existing readiness blockers are preserved.
- Passive daemon observations never establish Docker API health.
- No automatic service actions, installations, or container execution.
- `daemon_responsive=None`, `planning_only=True`,
  `execution_allowed=False`.

Validation: 373/373 backend tests and 6/6 Linux VM assessments.

### Milestone 4.21 — Structured Docker Readiness Explanation

DSSA converts its nine-source Docker readiness assessment into
structured, human-readable operational findings.

The explanation layer reports:
- Docker CLI availability.
- Docker systemd service-unit observations.
- Passive daemon process and socket findings.
- Unverified Docker API health.
- Informational investigation guidance.

The result includes stable finding codes, titles, descriptions,
investigation guidance, source reasons and evidence request IDs.

The explanation function does not perform SSH discovery or modify
the readiness decision. It is intended for future CLI and frontend
presentation.

Safety:
- No Docker API calls.
- No installation or service changes.
- No automatic troubleshooting or repair.
- No execution authorization.

Validation:
- 18/18 classification combinations.
- 14/14 permanent explanation tests.
- 387/387 complete backend regression tests.
- 6/6 end-to-end Linux VM assessments.

### Milestone 4.22 — Docker CLI Readiness Reports

DSSA provides a read-only Docker readiness assessment through
its existing CLI entry point.

Text report:

    python -m bridge.cli assess docker --target ubuntu24 --format text

JSON report:

    python -m bridge.cli assess docker --target ubuntu24 --format json

The command uses the existing nine-source readiness coordinator,
structured explanation layer, and report renderer.

Supported features:
- Human-readable terminal reports.
- Machine-readable JSON reports.
- Runtime and daemon classifications.
- Structured findings and investigation guidance.
- Original source reasons and nine evidence request IDs.
- Explicit planning-only and execution-disabled policies.

CLI exit codes:
- 0: Report generated successfully.
- 1: Assessment or reporting failed.
- 2: Invalid command arguments.
- 130: Assessment interrupted.

A successful report may contain REVIEW_REQUIRED or BLOCKED readiness.

Discovery stdout is suppressed during assessment, including output
from subprocesses inheriting stdout. The validated report is written
after stdout is restored.

This synchronous CLI isolation does not guarantee sanitization
of subprocess stderr or arbitrary concurrent output.

The assessment command does not install packages, restart services,
execute Docker API operations, or invoke component setup.

The existing legacy component CLI remains available.


### Milestone 4.23 — Unified Read-Only Workflow

DSSA uses a shared workflow coordinator for inventory-managed
Docker readiness assessments.

The workflow accepts a validated request containing:

- Schema version
- Workflow type
- Inventory target identity
- Application identifier
- Evidence freshness window

Currently supported:
- Workflow: readiness_assessment
- Application: docker
- Target kind: inventory

Public CLI commands remain unchanged:

    python -m bridge.cli assess docker --target ubuntu24 --format text

    python -m bridge.cli assess docker --target ubuntu24 --format json

The CLI now delegates assessment orchestration to the shared
workflow coordinator.

The coordinator reuses the existing nine-source Docker readiness
pipeline, explanation generator, and explanation validator.

Safety guarantees:
- Strict request field validation
- No raw SSH credentials accepted in workflow requests
- Inventory target identity validation
- Nine unique discovery evidence identities
- Evidence freshness and operation validation
- No Docker API health verification claims
- Planning-only results
- Execution authorization disabled
- No component installation through the assessment workflow

The original interactive main.py workflow and legacy component
CLI remain separate and unchanged.

The legacy component CLI retains its existing setup capability.
Use --plan when requesting its read-only planning mode.

Milestone 4.23 validation:
- 440 backend regression tests passed
- 12 assessment CLI tests passed
- 24 workflow contract/coordinator tests passed
- Six Linux distributions assessed successfully
- Text and JSON CLI compatibility verified
- Unknown inventory targets rejected safely

### Milestone 4.24 — Workflow Capability Registry and Handler Dispatch

DSSA now uses an explicit capability registry and implemented-handler
catalog for read-only application workflows.

Architecture:

- Application catalog: defines known DSSA applications.
- Capability registry: advertises explicitly supported workflows.
- Workflow contracts: validate requests against registered capabilities.
- Handler catalog: identifies implemented assessment handlers.
- Workflow coordinator: verifies capabilities and handler identity
  before invoking backend discovery.

Currently supported workflow:

- Application: Docker
- Workflow: readiness_assessment
- Target type: inventory
- Handler: docker_readiness_v1

The application catalog currently contains 23 definitions. Catalog
membership does not automatically enable an assessment workflow.

Safety guarantees:

- Unsupported application/workflow combinations are rejected.
- Handler resolution occurs before backend discovery.
- Capability metadata cannot authorize installation.
- Workflow requests remain inventory-managed.
- Readiness reports remain planning-only.
- Docker daemon API health is not claimed as verified.
- Legacy setup and interactive entry points remain unchanged.

Validation:

- 462 backend tests passed.
- Six Linux VM Docker readiness assessments passed.
- Nine unique evidence identities per assessment.
- Offline capability discovery passed.
- Lab startup environment verified.

No installation execution functionality was introduced.

### Milestone 4.25 — Offline Workflow Capability Discovery CLI

DSSA provides an offline command for discovering implemented,
read-only application workflow capabilities.

Commands:

    python -m bridge.cli capabilities
    python -m bridge.cli capabilities --format text
    python -m bridge.cli capabilities --format json

The command does not require:
- Inventory target selection
- SSH credentials
- Running lab VMs
- LinuxBackend initialization
- Remote discovery

The response is generated using the existing workflow capability
registry and implemented-handler catalog.

Current supported capability:

- Application: docker
- Workflow: readiness_assessment
- Target kind: inventory
- Planning only: true
- Execution allowed: false

JSON reports use schema version 1.0 and the check identifier
workflow_capabilities.

Capability report validation enforces exact fields, unique capability
identities, stable ordering, implemented handler availability and
read-only policy.

The existing Docker assessment CLI and legacy component setup CLI
remain unchanged.

The capability command does not execute installation or active
health-check operations.

### Capability Discovery Contract Guarantees — Milestone 4.26

The offline capability discovery interface provides deterministic, read-only
workflow capability reporting.

```bash
python -m bridge.cli capabilities --format text
python -m bridge.cli capabilities --format json
```

Contract guarantees:

- Capability declarations use schema version `1.0`.
- Declarations are sorted by application ID, workflow and target kind.
- Duplicate or unsorted declarations are rejected during validation.
- Every advertised capability must resolve to an implemented handler.
- Capabilities must declare `planning_only: true` and
  `execution_allowed: false`.
- Discovery returns independent report objects.
- Unsupported output formats are rejected before report validation.
- Invalid discovery reports do not produce partial CLI output.
- Expected discovery and rendering failures return a sanitized error.
- Capability discovery does not require SSH, inventory access or a
  remote backend.

The current implemented capability remains Docker readiness assessment
using inventory targets. Capability discovery does not install software
or authorize execution.
