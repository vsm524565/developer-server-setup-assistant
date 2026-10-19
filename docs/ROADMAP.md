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