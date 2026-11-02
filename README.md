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