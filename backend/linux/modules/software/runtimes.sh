#!/usr/bin/env bash

# DSSA reusable software runtime discovery collector.
# This module is read-only and produces structured JSON.

set -euo pipefail

# Discover a runtime by checking its executable and version.
detect_runtime() {
    local executable="$1"
    local path=""
    local version=""

    if ! command -v "$executable" >/dev/null 2>&1; then
        jq -n '{
            available: false,
            path: null,
            version: null
        }'
        return 0
    fi

    path="$(command -v "$executable")"

    case "$executable" in
        python3)
            version="$("$executable" --version 2>&1 | head -n 1)"
            ;;
        php)
            version="$("$executable" -v 2>&1 | head -n 1)"
            ;;
        node)
            version="$("$executable" --version 2>&1 | head -n 1)"
            ;;
        java)
            version="$("$executable" -version 2>&1 | head -n 1)"
            ;;
    esac

    # A runtime may be available even when its version cannot be read.
    jq -n \
        --arg path "$path" \
        --arg version "$version" \
        '{
            available: true,
            path: $path,
            version: (if $version == "" then null else $version end)
        }'
}

# Aggregate individual runtime results into one JSON response.
discover_runtimes() {
    local python_result
    local php_result
    local node_result
    local java_result

    python_result="$(detect_runtime python3)"
    php_result="$(detect_runtime php)"
    node_result="$(detect_runtime node)"
    java_result="$(detect_runtime java)"

    jq -n \
        --argjson python "$python_result" \
        --argjson php "$php_result" \
        --argjson node "$node_result" \
        --argjson java "$java_result" \
        '{
            runtimes: {
                python3: $python,
                php: $php,
                node: $node,
                java: $java
            }
        }'
}

# Execute only when invoked directly, not when sourced.
if [[ -n "${BASH_SOURCE[0]:-}" &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    discover_runtimes
fi