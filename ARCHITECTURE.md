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
- API request and response processing
- Data validation and normalization
- Dependency planning
- Installation decision preparation
- Approval workflow coordination
- Application-specific business logic

Restrictions:
- No destination SSH connections
- No destination SSH credentials
- No direct destination-server commands

## Central Linux Bash Backend

Responsibilities:
- Resolve approved target identifiers
- Manage SSH connectivity
- Perform Linux system discovery
- Verify installed components
- Execute explicitly permitted operations
- Record backend operational and security events

The backend runs on a management host.

Destination servers do not require permanent agents.

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
