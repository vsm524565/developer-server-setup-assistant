
#!/usr/bin/env bash
set -euo pipefail

# Approved read-only application inventory entry point.
# Shared modules are loaded by the SSH execution bundle.

if [[ $# -ne 2 ]]; then
    echo "ERROR: Application ID and package manager required" >&2
    exit 2
fi

application_id="$1"
package_manager="$2"

case "$application_id" in
    git|nginx|docker|docker_compose) ;;
    *)
        echo "ERROR: Unsupported application inventory target" >&2
        exit 2
        ;;
esac

case "$package_manager" in
    apt|dnf) ;;
    *)
        echo "ERROR: Unsupported package manager" >&2
        exit 2
        ;;
esac

for function_name in \
    application_package_state \
    application_binary_path \
    application_inventory_candidates \
    application_installed_package_version \
    application_binary_owner \
    discover_application_inventory
do
    declare -F "$function_name" >/dev/null || {
        echo "ERROR: Missing inventory function: $function_name" >&2
        exit 3
    }
done

discover_application_inventory "$application_id" "$package_manager"
