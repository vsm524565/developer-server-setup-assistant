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

Current milestone:

**Milestone 1 — SSH connection, sudo verification and server discovery**

No production use is recommended at this stage.
