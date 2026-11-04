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
