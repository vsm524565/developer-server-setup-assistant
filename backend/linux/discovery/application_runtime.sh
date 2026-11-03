
#!/usr/bin/env bash
set -euo pipefail

# Approved read-only application runtime discovery entry point.
# The runtime module must be loaded by the SSH execution bundle.

if [[ $# -ne 1 ]]; then
    echo "ERROR: Exactly one application ID is required" >&2
    exit 2
fi

application_id="$1"

case "$application_id" in
    docker|docker_compose) ;;
    *)
        echo "ERROR: Unsupported runtime application" >&2
        exit 2
        ;;
esac

for function_name in \
    application_runtime_version \
    discover_application_runtime; do

    if ! declare -F "$function_name" >/dev/null; then
        echo "ERROR: Missing runtime function: $function_name" >&2
        exit 3
    fi
done

discover_application_runtime "$application_id"
