#!/usr/bin/env bash

# DSSA reusable web server discovery collector.
# Read-only: does not install, start, stop, or modify services.

set -euo pipefail

# Find a server executable using PATH and approved locations.
find_web_server_binary() {
    local candidate

    for candidate in "$@"; do
        if [[ "$candidate" == /* ]]; then
            if [[ -f "$candidate" && -x "$candidate" ]]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        elif command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done

    return 1
}

# Find an existing systemd service unit.
find_web_server_service() {
    local unit

    command -v systemctl >/dev/null 2>&1 || return 1

    for unit in "$@"; do
        if systemctl list-unit-files \
            --type=service --no-legend --no-pager \
            "$unit" 2>/dev/null |
            awk '{print $1}' |
            grep -Fxq "$unit"; then

            printf '%s\n' "$unit"
            return 0
        fi
    done

    return 1
}

# Read a service state without changing it.
get_web_server_service_state() {
    local unit="$1"
    local state

    if [[ -z "$unit" ]]; then
        printf '%s\n' "unknown"
        return 0
    fi

    state="$(systemctl is-active "$unit" 2>/dev/null || true)"

    case "$state" in
        active|inactive|failed)
            printf '%s\n' "$state"
            ;;
        *)
            printf '%s\n' "unknown"
            ;;
    esac
}

# Read the first line of a version command.
get_web_server_version() {
    local binary="$1"
    local server="$2"
    local output=""

    case "$server" in
        nginx)
            output="$("$binary" -v 2>&1 || true)"
            ;;
        apache)
            output="$("$binary" -v 2>&1 || true)"
            ;;
        openlitespeed|litespeed)
            # Edition-specific version detection will be
            # introduced after validating real installations.
            ;;
    esac

    if [[ -n "$output" ]]; then
        printf '%s\n' "${output%%$'\n'*}"
    fi
}
# Locate an OpenLiteSpeed-specific executable candidate.
# Finding this executable does not independently prove its edition.
find_openlitespeed_candidate() {
    find_web_server_binary \
        /usr/local/lsws/bin/openlitespeed
}

# Identify LiteSpeed-family installation evidence.
# Shared installation paths do not prove a specific edition.
find_litespeed_evidence() {
    local path

    if path="$(find_openlitespeed_candidate)"; then
        printf '%s\n' "$path"
        return 0
    fi

    if path="$(find_web_server_binary \
        /usr/local/lsws/bin/lshttpd)"; then
        printf '%s\n' "$path"
        return 0
    fi

    if find_web_server_service lsws.service >/dev/null; then
        printf '%s\n' "lsws.service"
        return 0
    fi

    return 1
}


# Identify LiteSpeed edition from installed Debian package metadata.
# Conflicting package evidence must never select an edition.
find_litespeed_package_edition() {
    local package status
    local openlitespeed_installed=false
    local litespeed_installed=false

    command -v dpkg-query >/dev/null 2>&1 || return 1

    for package in openlitespeed lsws; do
        status="$(
            dpkg-query -W \
                -f='${db:Status-Abbrev}' \
                "$package" 2>/dev/null || true
        )"

        if [[ "$status" == "ii "* ]]; then
            case "$package" in
                openlitespeed)
                    openlitespeed_installed=true
                    ;;
                lsws)
                    litespeed_installed=true
                    ;;
            esac
        fi
    done

    if [[ "$openlitespeed_installed" == true &&
          "$litespeed_installed" == true ]]; then
        printf '%s\n' "unidentified"
    elif [[ "$openlitespeed_installed" == true ]]; then
        printf '%s\n' "openlitespeed"
    elif [[ "$litespeed_installed" == true ]]; then
        printf '%s\n' "litespeed"
    else
        return 1
    fi
}


# Generate one server's structured discovery result.
detect_web_server() {
    local server="$1"
    local binary=""
    local service=""
    local state="unknown"
    local version=""
    local available=false
    local detection_status="absent"
    local edition=""

    case "$server" in
        nginx)
            binary="$(find_web_server_binary \
                nginx /usr/sbin/nginx /usr/local/nginx/sbin/nginx \
                || true)"
            service="$(find_web_server_service nginx.service || true)"
            ;;
        apache)
            binary="$(find_web_server_binary \
                apache2 httpd /usr/sbin/apache2 /usr/sbin/httpd \
                || true)"
            service="$(find_web_server_service \
                apache2.service httpd.service || true)"
            ;;
openlitespeed|litespeed)
    edition="$(find_litespeed_package_edition || true)"

    service="$(find_web_server_service lsws.service || true)"

    if [[ "$edition" == "$server" ]]; then
        binary="$(find_web_server_binary \
            /usr/local/lsws/bin/lshttpd || true)"

        if [[ -z "$binary" && "$server" == "openlitespeed" ]]; then
            binary="$(find_openlitespeed_candidate || true)"
        fi

        if [[ -z "$binary" ]]; then
            detection_status="unidentified"
        fi

    elif [[ "$edition" == "unidentified" ]] ||
         find_litespeed_evidence >/dev/null; then
        detection_status="unidentified"
    fi
    ;;
        *)
            return 2
            ;;
    esac

    if [[ -n "$binary" ]]; then
        available=true
        version="$(get_web_server_version "$binary" "$server")"
    fi
    if [[ "$available" == true ]]; then
        detection_status="detected"
    fi

    state="$(get_web_server_service_state "$service")"

    jq -n \
        --argjson available "$available" \
        --arg binary "$binary" \
        --arg version "$version" \
        --arg service "$service" \
        --arg state "$state" \
        --arg detection_status "$detection_status" \
        '{
            available: $available,
            path: (if $binary == "" then null else $binary end),
            version: (if $version == "" then null else $version end),
            service_name: (if $service == "" then null else $service end),
            service_state: $state,
            detection_status: $detection_status
        }'
}

# Aggregate all supported web server results.
discover_web_servers() {
    local nginx_result
    local apache_result
    local openlitespeed_result
    local litespeed_result

    nginx_result="$(detect_web_server nginx)"
    apache_result="$(detect_web_server apache)"
    openlitespeed_result="$(detect_web_server openlitespeed)"
    litespeed_result="$(detect_web_server litespeed)"

    jq -n \
        --argjson nginx "$nginx_result" \
        --argjson apache "$apache_result" \
        --argjson openlitespeed "$openlitespeed_result" \
        --argjson litespeed "$litespeed_result" \
        '{
            web_servers: {
                nginx: $nginx,
                apache: $apache,
                openlitespeed: $openlitespeed,
                litespeed: $litespeed
            }
        }'
}
