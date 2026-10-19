#!/usr/bin/env bash

# Shared package-changing functions.
# Loaded by the backend; no commands execute when this file is sourced.

refresh_package_metadata() {
    local manager="$1"

    case "$manager" in
        apt)
            apt-get update >&2
            ;;
        dnf)
            dnf makecache >&2
            ;;
        *)
            echo "ERROR: Unsupported package manager." >&2
            return 2
            ;;
    esac
}

install_package() {
    local manager="$1"
    local package="$2"

    case "$manager" in
        apt)
            DEBIAN_FRONTEND=noninteractive \
                apt-get install -y -- "$package" >&2
            ;;
        dnf)
            dnf install -y -- "$package" >&2
            ;;
        *)
            echo "ERROR: Unsupported package manager." >&2
            return 2
            ;;
    esac
}
