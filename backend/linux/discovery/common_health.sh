#!/usr/bin/env bash
set -euo pipefail

# DSSA common server health discovery.
# Read-only aggregation of independent collectors.
# The required collector functions are loaded before this runner.

identity_json="$(discover_identity)" || exit 1
privileges_json="$(discover_privileges)" || exit 1
time_json="$(discover_time_health)" || exit 1

# Keep collector results as JSON data.
# The three collectors produce JSON from fixed fields and safe values.
discover_common_health() {
    local identity_json privileges_json time_json

    identity_json="$(discover_identity)" || return 1
    privileges_json="$(discover_privileges)" || return 1
    time_json="$(discover_time_health)" || return 1

    printf '{'
    printf '"schema_version":"1.0",'
    printf '"check":"common_health",'
    printf '"identity":%s,' "$identity_json"
    printf '"privileges":%s,' "$privileges_json"
    printf '"time_health":%s,' "$time_json"
    printf '"execution_allowed":false'
    printf '}\n'
}

