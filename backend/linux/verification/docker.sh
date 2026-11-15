
#!/usr/bin/env bash

# Verify Docker Engine's package, service, CLI, and daemon API.
verify_docker() {
    local manager="${1:-}"

    [[ "$manager" == "apt" ]] || return 1

    for pkg in docker-ce docker-ce-cli containerd.io docker-buildx-plugin; do
        package_is_installed "$manager" "$pkg" || return 1
    done

    command -v docker >/dev/null 2>&1 || return 1
    command -v systemctl >/dev/null 2>&1 || return 1
    command -v timeout >/dev/null 2>&1 || return 1

    systemctl is-active --quiet docker.service || return 1
    timeout 15 docker version --format '{{.Server.Version}}' ||
        return 1

    timeout 15 docker info --format '{{.ServerVersion}}' ||
        return 1
}
