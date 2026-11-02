#!/usr/bin/env bash
set -euo pipefail

# Approved read-only application-state discovery entry point.
# Shared package-manager and application-state functions must
# be sourced by the SSH execution bundle before this runner.

if [[ $# -ne 2 ]]; then
    echo "ERROR: Application ID and package manager are required." >&2
    exit 2
fi

application_id="$1"
package_manager="$2"

for function_name in \
    application_package_state \
    application_binary_path \
    application_service_state \
    discover_application_state
do
    declare -F "$function_name" >/dev/null || {
        echo "ERROR: Missing backend function: $function_name" >&2
        exit 3
    }
done

discover_application_state "$application_id" "$package_manager"
