#!/usr/bin/env bash

# Install missing Git using the shared package functions.
install_git() (
    set -euo pipefail
    local manager="${1:?Package manager is required}"

    if package_is_installed "$manager" git; then
        jq -cn '{
            component: "git",
            package_action: "skipped",
            verification: "not_run",
            service_action: "unchanged"
        }'
        exit 0
    fi

    # Avoid replacing an executable outside the package database.
    if command -v git >/dev/null 2>&1; then
        echo "ERROR: Unmanaged Git executable requires review." >&2
        exit 1
    fi

    refresh_package_metadata "$manager" || {
        echo "ERROR: Package metadata refresh failed." >&2
        exit 1
    }

    install_package "$manager" git || {
        echo "ERROR: Git installation failed; partial changes may remain." >&2
        exit 1
    }

    package_is_installed "$manager" git || {
        echo "ERROR: Git package installation could not be verified." >&2
        exit 1
    }

    verify_git || exit 1

    jq -cn '{
        component: "git",
        package_action: "installed",
        verification: "passed",
        service_action: "not_applicable"
    }'
)
