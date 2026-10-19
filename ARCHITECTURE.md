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
