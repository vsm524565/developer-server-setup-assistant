#!/usr/bin/env bash

# Read-only Git verification.
verify_git() {
    command -v git >/dev/null 2>&1 || {
        echo "ERROR: Git executable not found." >&2
        return 1
    }

    git --version >&2
}
