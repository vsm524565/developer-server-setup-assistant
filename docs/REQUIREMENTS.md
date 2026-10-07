# Project Requirements

## Objective

Provide developers with a guided tool for preparing a Linux server for their
application requirements without requiring them to manually perform routine
Linux administration tasks.

## User

The initial user is a developer who:

- Already has a Linux server.
- Has SSH credentials.
- Has sudo privileges.
- Knows the infrastructure requirements of their application.
- Does not necessarily know how to install or configure those requirements.

## Core Requirements

The tool must:

1. Accept server connection information.
2. Verify server reachability and SSH authentication.
3. Verify sudo privileges.
4. Discover server information.
5. Detect an existing supported control panel.
6. Allow selection of required server components.
7. Detect whether selected components already exist.
8. Install missing components.
9. Validate installed or existing components.
10. Inspect RAM, disk and swap.
11. Support safe swap creation when required.
12. Produce a final server-readiness report.

## Server Discovery

The tool should identify:

- Hostname
- Operating system
- OS version
- Kernel
- CPU architecture
- RAM
- Disk capacity
- Available disk space
- Existing swap

## Safety Requirements

The tool must follow:

Detect → Decide → Change → Verify

Existing working components should not be unnecessarily reinstalled.

Server modifications must only begin after SSH and sudo verification succeeds.

Swap must not be created without checking available disk space.

Failures must be reported rather than silently ignored.

## V1 Exclusions

V1 will not:

- Deploy the developer's application.
- Provide a web frontend.
- Provide a REST API.
- Maintain a centralized server inventory.
- Provide multi-user RBAC.
- Act as a monitoring platform.
- Replace InfraGuard.

These capabilities may be evaluated in later versions.
