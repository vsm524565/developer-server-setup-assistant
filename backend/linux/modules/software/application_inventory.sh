
#!/usr/bin/env bash

# Read-only application package inventory helpers.
# No package installation, repository refresh or service changes.

application_inventory_candidates() {
    local application="$1"

    case "$application" in
        git)
            printf '%s\n' git
            ;;
        nginx)
            printf '%s\n' nginx
            ;;
        docker)
            printf '%s\n' \
                docker-ce \
                docker-ce-cli \
                docker.io \
                moby-engine \
                moby-cli
            ;;
        docker_compose)
            printf '%s\n' \
                docker-compose-plugin \
                docker-compose-v2 \
                docker-compose
            ;;
        *)
            return 2
            ;;
    esac
}


application_installed_package_version() {
    local manager="$1"
    local package="$2"
    local output

    [[ "$package" =~ ^[a-zA-Z0-9][a-zA-Z0-9.+_-]*$ ]] ||
        return 2

    case "$manager" in
        apt)
            command -v dpkg-query >/dev/null 2>&1 ||
                return 1

            output="$(
                dpkg-query -W \
                    -f='${Status}\t${Version}' \
                    "$package" 2>/dev/null
            )" || return 1

            [[ "$output" == $'install ok installed\t'* ]] ||
                return 1

            printf '%s\n' "${output#*$'\t'}"
            ;;

        dnf)
            command -v rpm >/dev/null 2>&1 ||
                return 1

            rpm -q \
                --qf '%{VERSION}-%{RELEASE}\n' \
                -- "$package" 2>/dev/null
            ;;

        *)
            return 2
            ;;
    esac
}


application_binary_owner() {
    local manager="$1"
    local path="$2"

    [[ "$path" == /* ]] || return 2

    case "$manager" in
        apt)
            command -v dpkg-query >/dev/null 2>&1 ||
                return 1

            dpkg-query -S -- "$path" 2>/dev/null
            ;;

        dnf)
            command -v rpm >/dev/null 2>&1 ||
                return 1

            rpm -qf \
                --qf '%{NAME}\n' \
                -- "$path" 2>/dev/null
            ;;

        *)
            return 2
            ;;
    esac
}

# Collect structured package and executable inventory.
# Requires application_state.sh and application_inventory.sh
# to be loaded in the same Bash process.

discover_application_inventory() (
    set -euo pipefail

    local application="${1:-}"
    local manager="${2:-}"
    local binary=""
    local binary_path=""
    local binary_owner_raw=""
    local package version state
    local packages_json
    local ownership_status="unknown"
    local candidate_text
    local record
    local -a package_records=()
    case "$application" in
        git)
            binary="git"
            ;;
        nginx)
            binary="nginx"
            ;;
        docker|docker_compose)
            binary="docker"
            ;;
        *)
            echo "ERROR: Unsupported application inventory target" >&2
            return 2
            ;;
    esac

    case "$manager" in
        apt|dnf) ;;
        *)
            echo "ERROR: Unsupported package manager" >&2
            return 2
            ;;
    esac

    command -v jq >/dev/null 2>&1 || {
        echo "ERROR: jq is required" >&2
        return 127
    }

  
    # Resolve package candidates before collecting inventory.
    candidate_text="$(
        application_inventory_candidates "$application"
    )" || {
        echo "ERROR: Cannot resolve package candidates" >&2
        return 3
    }

    if [[ -z "$candidate_text" ]]; then
        echo "ERROR: Empty application package candidate list" >&2
        return 3
    fi

    # Generate each package record and check for failures.
    while IFS= read -r package; do
        if [[ -z "$package" ]]; then
            echo "ERROR: Empty package candidate" >&2
            return 3
        fi

        if ! state="$(
            application_package_state "$manager" "$package"
        )"; then
            echo "ERROR: Package state query failed: $package" >&2
            return 3
        fi

        case "$state" in
            installed|absent|unknown) ;;
            *)
                echo "ERROR: Invalid package state: $package" >&2
                return 3
                ;;
        esac

        version=""

        if [[ "$state" == "installed" ]]; then
            version="$(
                application_installed_package_version \
                    "$manager" "$package"
            )" || version=""
        fi

        record="$(
            jq -cn \
                --arg name "$package" \
                --arg state "$state" \
                --arg version "$version" \
                '{
                    name: $name,
                    state: $state,
                    version: (
                        if $version == ""
                        then null
                        else $version
                        end
                    )
                }'
        )" || {
            echo "ERROR: Package record serialization failed" >&2
            return 3
        }

        package_records+=("$record")

    done <<< "$candidate_text"

    # Convert all collected records into one JSON array.
    packages_json="$(
        printf '%s\n' "${package_records[@]}" | jq -sc .
    )" || {
        echo "ERROR: Package inventory serialization failed" >&2
        return 3
    }

    binary_path="$(application_binary_path "$binary")"

    if [[ -n "$binary_path" ]]; then
        if binary_owner_raw="$(
            application_binary_owner "$manager" "$binary_path"
        )"; then
            if [[ -n "$binary_owner_raw" ]]; then
                ownership_status="reported"
            fi
        else
            binary_owner_raw=""
        fi
    fi

    jq -cn \
        --arg application "$application" \
        --arg manager "$manager" \
        --argjson packages "$packages_json" \
        --arg binary_name "$binary" \
        --arg binary_path "$binary_path" \
        --arg ownership_status "$ownership_status" \
        --arg binary_owner_raw "$binary_owner_raw" \
        '{
            schema_version: "1.0",
            application_id: $application,
            package_manager: $manager,
            packages: $packages,
            binary: {
                name: $binary_name,
                path: (
                    if $binary_path == ""
                    then null
                    else $binary_path
                    end
                ),
                present: ($binary_path != ""),
                ownership: {
                    status: $ownership_status,
                    raw: (
                        if $binary_owner_raw == ""
                        then null
                        else $binary_owner_raw
                        end
                    )
                }
            },
            read_only: true,
            execution_allowed: false
        }'
)
