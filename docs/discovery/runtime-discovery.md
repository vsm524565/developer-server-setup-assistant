
# Runtime Discovery — Milestone 4.8

## Overview

The Developer Server Setup Assistant (DSSA) supports read-only
runtime discovery on inventory-approved remote Linux servers.

Discovery is performed through the central Bash backend using
agentless SSH execution.

## Supported Runtimes

- Python 3 (`python3`)
- PHP (`php`)
- Node.js (`node`)
- Java (`java`)

For each runtime, the collector returns:

- `available`: Boolean indicating whether the executable is found
- `path`: Executable path, or null if unavailable
- `version`: Detected version string, or null if unavailable or undetermined

Runtime availability is determined using the remote executable PATH.

## Architecture

JSON Request
    |
    v
Bash Dispatcher
    |
    v
Inventory Validation
    |
    v
SSH Transport
    |
    v
Runtime Discovery Runner
    |
    v
Reusable Bash Runtime Collector
    |
    v
Validated JSON Response

## Operation

Operation identifier:

`discovery.runtimes`

Request parameters must be an empty object.

Example request:

```json
{
  "schema_version": "1.0",
  "request_id": "req-runtimes-001",
  "operation": "discovery.runtimes",
  "target_id": "lab-ubuntu-01",
  "parameters": {}
}
```

## Security

- Inventory-approved targets only
- Strict SSH host-key verification
- SSH key authentication
- No arbitrary remote commands supplied by requests
- No installation or system modification
- Strict JSON request and response validation
- Audit events for accepted requests and execution outcomes

## Testing

Milestone 4.8 validation:

- 11 dispatcher tests passed
- 11 SSH transport tests passed
- Remote Ubuntu runtime discovery verified
- Bash syntax validation passed
- Python compilation passed
- Git whitespace checks passed

The runtime-specific SSH tests cover valid responses,
malformed JSON, and invalid response schemas.

The dispatcher tests cover successful requests,
parameter rejection, and audit event generation.

## Current Limitations

- Discovery checks executables available through PATH.
- Installed runtimes outside PATH may not be detected.
- Version detection depends on runtime command output.
- Runtime discovery does not install, upgrade, or configure software.

## Future Enhancements

- Additional runtime discovery
- Runtime compatibility checks
- Component selection
- Installation planning
- Idempotent installers
- Python orchestration integration
- UI-based discovery presentation
```
