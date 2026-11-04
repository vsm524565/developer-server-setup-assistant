
#!/usr/bin/env bash
set -euo pipefail

# Approved read-only Docker daemon preflight runner.
# The collector function must already be loaded by the SSH bundle.

if [[ $# -ne 0 ]]; then
    echo "ERROR: Docker daemon preflight accepts no arguments" >&2
    exit 2
fi

if ! declare -F discover_docker_daemon_preflight >/dev/null; then
    echo "ERROR: Docker daemon preflight collector unavailable" >&2
    exit 3
fi

discover_docker_daemon_preflight
