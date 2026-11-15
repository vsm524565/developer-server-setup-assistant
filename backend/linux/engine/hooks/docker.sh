#!/usr/bin/env bash

# Named verification hooks for the Docker definition (Milestone 4.34).
#
# A definition can only call hooks registered in DSSA_HOOKS, so it can
# never reach an arbitrary engine function. Hooks are read-only: they
# check, never change. Output goes to stderr; the result is the exit code.

DSSA_HOOKS="${DSSA_HOOKS:- }verify_docker_daemon "

# The daemon answers on its socket and reports a server version.
# No image is pulled and no container is started.
verify_docker_daemon() {
    local version

    command -v docker >/dev/null 2>&1 || {
        echo "docker: client not found" >&2
        return 1
    }
    version="$(timeout 20 docker info --format '{{.ServerVersion}}' 2>&1)" || {
        printf 'docker: daemon did not answer: %s\n' "$version" >&2
        return 1
    }
    [[ -n "$version" && "$version" != *"<no value>"* ]] || {
        echo "docker: daemon reported no server version" >&2
        return 1
    }
    printf 'docker: daemon answering, server version %s\n' "$version" >&2
}
