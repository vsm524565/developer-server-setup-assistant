#!/usr/bin/env bash

# Plan Docker Engine installation without changing the target system.
# This planner only authorizes clean Ubuntu 24.04 amd64 vendor installs.
plan_docker_component() (
    set -euo pipefail

    local manager="${1:-}"
    local action="REVIEW"
    local reason=""
    local pkg path cmd
    local installed_count=0

    local -a required_packages=(
        docker-ce
        docker-ce-cli
        containerd.io
        docker-buildx-plugin
    )

    local -a conflicting_packages=(
        docker.io
        docker-compose
        docker-compose-v2
        docker-doc
        docker-buildx
        podman-docker
        containerd
        runc
        moby-engine
        moby-cli
    )

    # Only the explicitly supported platform may be installed.
    if [[ "$manager" != "apt" ]]; then
        reason="Docker installation currently supports APT only."

    elif [[ ! -r /etc/os-release ]]; then
        reason="Operating-system evidence is unavailable."

    else
        source /etc/os-release

        if [[ "${ID:-}" != "ubuntu" ||
              "${VERSION_ID:-}" != "24.04" ]]; then
            reason="Docker installation requires Ubuntu 24.04."

        elif [[ "$(uname -m)" != "x86_64" ||
                "$(dpkg --print-architecture)" != "amd64" ]]; then
            reason="Docker installation requires amd64 architecture."

        else
            # Count the complete vendor package set.
            for pkg in "${required_packages[@]}"; do
                if package_is_installed "$manager" "$pkg"; then
                    (( installed_count += 1 ))
                fi
            done

            # Detect conflicting distribution packages.
            for pkg in "${conflicting_packages[@]}"; do
                if package_is_installed "$manager" "$pkg"; then
                    reason="Conflicting package requires review: $pkg"
                    break
                fi
            done

            if [[ -z "$reason" ]]; then
                if (( installed_count == ${#required_packages[@]} )); then
                    action="SKIP"
                    reason="Docker vendor packages are already installed; runtime verification required."

                elif (( installed_count > 0 )); then
                    reason="Partial Docker vendor installation requires review."

                else
                    # Refuse to take ownership of unmanaged executables.
                    for cmd in docker dockerd containerd; do
                        if command -v "$cmd" >/dev/null 2>&1; then
                            reason="Existing Docker-related executable requires review: $cmd"
                            break
                        fi
                    done

                    # Refuse to overwrite Docker data or configuration.
                    if [[ -z "$reason" ]]; then
                        for path in \
                            /var/lib/docker \
                            /var/lib/containerd \
                            /etc/docker \
                            /etc/apt/sources.list.d/docker.sources \
                            /etc/apt/sources.list.d/docker.list \
                            /etc/apt/keyrings/docker.asc \
                            /etc/apt/keyrings/docker.gpg
                        do
                            if [[ -e "$path" || -L "$path" ]]; then
                                reason="Existing Docker state requires review: $path"
                                break
                            fi
                        done
                    fi

                    # Reject any pre-existing Docker APT source reference.
                    if [[ -z "$reason" ]] &&
                       grep -Ril -- 'download.docker.com' \
                           /etc/apt/sources.list \
                           /etc/apt/sources.list.d \
                           2>/dev/null | grep -q .; then
                        reason="Existing Docker APT repository requires review."
                    fi

                    # Refuse to replace independently managed Docker units.
                    if [[ -z "$reason" ]] &&
                       systemctl list-unit-files \
                           docker.service docker.socket \
                           --no-legend 2>/dev/null |
                           grep -Eq '^docker\.(service|socket)'; then
                        reason="Existing Docker systemd units require review."
                    fi

                    if [[ -z "$reason" ]]; then
                        action="INSTALL"
                        reason="Clean Ubuntu 24.04 amd64 Docker installation candidate."
                    fi
                fi
            fi
        fi
    fi

    jq -n \
        --arg component "docker" \
        --arg package "docker-ce" \
        --arg manager "$manager" \
        --arg action "$action" \
        --arg reason "$reason" \
        '{
            component: $component,
            package: $package,
            package_manager: $manager,
            action: $action,
            reasons: [$reason]
        }'
)

# Produce one component decision without changing the server.
plan_component() {
    local manager="$1"
    local panel_facts="$2"
    local port_facts="$3"
    local component="$4"
    local package action reason

    # Fixed mapping: selections never become arbitrary package names.
    case "$component" in
        nginx|git)
            package="$component"
            ;;
        docker)
            package="docker-ce"
            ;;
        *)
            echo "ERROR: Unsupported planning component." >&2
            return 2
            ;;
    esac

    # Reject incomplete discovery rather than assume no conflicts.
    jq -e '
        type == "object"
        and (.panels | type == "array")
        and all(.panels[]; type == "string")
    ' <<< "$panel_facts" >/dev/null || return 3

    jq -e '
        type == "object"
        and (.listening_tcp_ports | type == "array")
        and all(.listening_tcp_ports[];
            type == "number" and . >= 1 and . <= 65535
        )
    ' <<< "$port_facts" >/dev/null || return 3
    # Route Docker through its OS-specific safety planner.
    # Never allow Docker to fall through to generic package planning.
    if [[ "$component" == "docker" ]]; then
        plan_docker_component "$manager"
        return $?
    fi

    if package_is_installed "$manager" "$package"; then
        action="SKIP"
        reason="Package already installed; leave existing state unchanged."

    elif command -v "$component" >/dev/null 2>&1; then
        action="REVIEW"
        reason="Executable exists outside the detected package installation."

    elif [[ "$component" == "nginx" ]] &&
         [[ -e /etc/nginx || -e /usr/local/nginx ]]; then
        action="REVIEW"
        reason="Existing Nginx files require review."

    elif [[ "$component" == "nginx" ]] &&
         jq -e '.panels | length > 0' \
             <<< "$panel_facts" >/dev/null; then
        action="REVIEW"
        reason="Panel evidence found; Nginx installation requires review."

    elif [[ "$component" == "nginx" ]] &&
         jq -e '
             any(.listening_tcp_ports[]; . == 80 or . == 443)
         ' <<< "$port_facts" >/dev/null; then
        action="REVIEW"
        reason="TCP port 80 or 443 is occupied."

    else
        action="INSTALL"
        reason="Package missing; no conflicts found by current checks."
    fi

    jq -n \
        --arg component "$component" \
        --arg package "$package" \
        --arg manager "$manager" \
        --arg action "$action" \
        --arg reason "$reason" \
        '{
            component: $component,
            package: $package,
            package_manager: $manager,
            action: $action,
            reasons: [$reason]
        }'
}
# Assemble the complete plan before any installation is attempted.
build_component_plan() {
    local manager="$1"
    local panel_facts="$2"
    local port_facts="$3"
    shift 3

    [[ $# -gt 0 ]] || {
        echo "ERROR: No components selected for planning." >&2
        return 2
    }

    local component step
    local -a steps=()
    local -A seen=()

    for component in "$@"; do
        # Process repeated selections only once.
        if [[ -n "${seen[$component]:-}" ]]; then
            continue
        fi
        seen["$component"]=1

        step="$(
            plan_component \
                "$manager" "$panel_facts" "$port_facts" "$component"
        )" || return $?

        steps+=("$step")
    done

    printf '%s\n' "${steps[@]}" |
        jq -s '{
            schema_version: "1.0",
            eligible: all(.[];
                .action == "INSTALL" or .action == "SKIP"
            ),
            steps: .
        }'
}