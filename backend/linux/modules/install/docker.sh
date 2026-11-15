
#!/usr/bin/env bash

# Install Docker Engine using the official Ubuntu 24.04 repository.
# This function must only be called after explicit operator approval.
install_docker() (
    set -euo pipefail
    umask 077

    local manager="${1:-}"
    local pkg
    local key_file
    local arch
    local fingerprint
    local expected_fingerprint="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"

    fail() {
        printf 'ERROR: %s\n' "$1" >&2
        exit 1
    }

    [[ "$EUID" -eq 0 ]] ||
        fail "Root privileges are required."

    [[ "$manager" == "apt" ]] ||
        fail "Docker installer currently supports APT only."

    for fn in package_is_installed verify_docker; do
        declare -F "$fn" >/dev/null ||
            fail "Missing required function: $fn"
    done

    # Reject missing operating-system information before any changes.
    [[ -r /etc/os-release ]] ||
        fail "Operating-system information unavailable."

    source /etc/os-release

    [[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" ]] ||
        fail "Only Ubuntu 24.04 is supported."

    [[ "$(uname -m)" == "x86_64" ]] ||
        fail "Only x86_64 is supported."


    arch="$(dpkg --print-architecture)"
    [[ "$arch" == "amd64" ]] ||
        fail "Unsupported Debian architecture."

    # Validate tools needed before installing repository prerequisites.
    for cmd in apt-get dpkg grep install mktemp systemctl jq; do
        command -v "$cmd" >/dev/null 2>&1 ||
            fail "Missing installation prerequisite: $cmd"
    done

    # Never silently replace conflicting Docker distributions.
    for pkg in \
        docker.io docker-compose docker-compose-v2 \
        docker-doc docker-buildx podman-docker containerd runc moby-engine moby-cli
    do
        if package_is_installed "$manager" "$pkg"; then
            fail "Conflicting package requires review: $pkg"
        fi
    done

    # A fully installed Docker Engine is verified, not reinstalled.
    if package_is_installed "$manager" docker-ce; then
        verify_docker "$manager" ||
            fail "Existing Docker Engine is unhealthy; review required."

        jq -cn '{
            component: "docker",
            package_action: "skipped",
            verification: "passed",
            service_action: "unchanged"
        }'
        exit 0
    fi

    # Reject partially installed or unmanaged Docker components.
    for pkg in docker-ce-cli containerd.io docker-buildx-plugin; do
        if package_is_installed "$manager" "$pkg"; then
            fail "Partial Docker installation requires review: $pkg"
        fi
    done

    for cmd in docker dockerd containerd; do
        if command -v "$cmd" >/dev/null 2>&1; then
            fail "Existing unmanaged executable requires review: $cmd"
        fi
    done

    for path in \
        /var/lib/docker \
        /var/lib/containerd \
        /etc/docker \
        /etc/apt/sources.list.d/docker.sources \
        /etc/apt/sources.list.d/docker.list \
        /etc/apt/keyrings/docker.asc \
        /etc/apt/keyrings/docker.gpg
    do
        [[ ! -e "$path" && ! -L "$path" ]] ||
            fail "Existing Docker state requires review: $path"
    done
    # Reject Docker repository references outside the expected files.
    # Existing repository ownership must be reviewed, not overwritten.
    if grep -RIl -- 'download.docker.com' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d \
        2>/dev/null | grep -q .; then

        fail "Existing Docker APT repository requires review."
    fi
    # Check for an existing Docker service even without Docker packages.
    if systemctl list-unit-files docker.service docker.socket \
        --no-legend 2>/dev/null | grep -Eq '^docker\.(service|socket)'; then
        fail "Existing Docker systemd units require review."
    fi


    # Install repository prerequisites.
    export DEBIAN_FRONTEND=noninteractive

    # Refresh distribution metadata before installing prerequisites.
    # No Docker repository configuration has been written yet.
    apt-get update ||
        fail "APT metadata refresh failed; no Docker repository changes were made."

    apt-get install -y -- ca-certificates curl gnupg ||
        fail "Repository prerequisite installation failed."

    # Verify the tools required to download and authenticate
    # the official Docker repository signing key.
    for cmd in curl gpg awk; do
        command -v "$cmd" >/dev/null 2>&1 ||
            fail "Missing repository verification tool: $cmd"
    done

    install -d -m 0755 /etc/apt/keyrings


    # Download and fingerprint-check Docker's signing key.
    key_file="$(mktemp)"
    trap 'rm -f "$key_file"' EXIT

    curl -fsSL --connect-timeout 10 --max-time 60 \
        https://download.docker.com/linux/ubuntu/gpg \
        -o "$key_file" ||
        fail "Docker signing key download failed."

    fingerprint="$(
        gpg --batch --show-keys --with-colons "$key_file" |
        awk -F: '$1 == "fpr" { print $10; exit }'
    )"

    [[ "$fingerprint" == "$expected_fingerprint" ]] ||
        fail "Docker signing key fingerprint mismatch."

    install -m 0644 "$key_file" /etc/apt/keyrings/docker.asc

    # Configure only the approved Ubuntu Noble vendor repository.
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    apt-get update ||
        fail "Docker repository metadata refresh failed."

    # Engine only; Compose remains a separate installation component.
    apt-get install -y -- \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin ||
        fail "Docker installation failed; partial changes may remain."

    # Docker may already be started by the package installation.
    systemctl is-active --quiet docker.service ||
        systemctl start docker.service ||
        fail "Docker daemon startup failed."

    verify_docker "$manager" ||
        fail "Docker installation verification failed."

    jq -cn '{
        component: "docker",
        package_action: "installed",
        verification: "passed",
        service_action: "start_requested"
    }'
)
