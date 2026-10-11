#!/usr/bin/env bash

set -euo pipefail

# Linux OS identity discovery.
# Read-only operations; no system configuration changes.

if [[ ! -r /etc/os-release ]]; then
    echo "ERROR: Cannot read /etc/os-release" >&2
    exit 1
fi

# Load standard OS identification fields.
# This file is supplied by the operating system.
source /etc/os-release

hostname="$(hostname)"
architecture="$(uname -m)"
# CPU capacity discovery.
cpu_logical_cores="$(nproc)"
cpu_model="$(awk -F ':' '/^model name[[:space:]]*:/ {
    sub(/^[[:space:]]+/, "", $2)
    print $2
    exit
}' /proc/cpuinfo)"

# Memory capacity discovery.
memory_total_bytes="$(free -b | awk '/^Mem:/ {print $2}')"
memory_available_bytes="$(free -b | awk '/^Mem:/ {print $7}')"

# Root filesystem capacity discovery.
disk_info="$(df -B1 / | awk 'NR==2 {print $2, $3, $4}')"

read -r disk_total_bytes disk_used_bytes disk_available_bytes <<< "$disk_info"

# System uptime discovery.
uptime_seconds="$(awk '{printf "%.0f", $1}' /proc/uptime)"

# Virtualization discovery.
virtualization="$(systemd-detect-virt 2>/dev/null || true)"

if [[ -z "$virtualization" || "$virtualization" == "none" ]]; then
    virtualization="physical"
fi

# Validate required fields before producing output.
if [[ -z "$hostname" || -z "${ID:-}" ||
      -z "$architecture" ||
        ! "$cpu_logical_cores" =~ ^[0-9]+$ ||
        "$cpu_logical_cores" -lt 1 ||
        -z "$cpu_model" ]]; then
        ! "$memory_total_bytes" =~ ^[0-9]+$ ||
        ! "$memory_available_bytes" =~ ^[0-9]+$ ||
        (( memory_total_bytes < 1 )) ||
        (( memory_available_bytes > memory_total_bytes )) ||
        ! "$disk_total_bytes" =~ ^[0-9]+$ ||
        ! "$disk_used_bytes" =~ ^[0-9]+$ ||
        ! "$disk_available_bytes" =~ ^[0-9]+$ ||
        (( disk_total_bytes < 1 )) ||
        (( disk_used_bytes > disk_total_bytes )) ||
        (( disk_available_bytes > disk_total_bytes )) ||
        ! "$uptime_seconds" =~ ^[0-9]+$ ||
        -z "$virtualization" ||
    echo "ERROR: Incomplete OS discovery information" >&2
    exit 1
fi

# Output structured JSON.
jq -n \
    --arg hostname "$hostname" \
    --arg os_id "$ID" \
    --arg os_version "$VERSION_ID" \
    --arg architecture "$architecture" \
    --argjson cpu_logical_cores "$cpu_logical_cores" \
    --arg cpu_model "$cpu_model" \
    --argjson memory_total_bytes "$memory_total_bytes" \
    --argjson memory_available_bytes "$memory_available_bytes" \
    --argjson disk_total_bytes "$disk_total_bytes" \
    --argjson disk_used_bytes "$disk_used_bytes" \
    --argjson disk_available_bytes "$disk_available_bytes" \
    --argjson uptime_seconds "$uptime_seconds" \
    --arg virtualization "$virtualization" \
    '{
        hostname: $hostname,
        os_id: $os_id,
        os_version: $os_version,
        architecture: $architecture,
        cpu_logical_cores: $cpu_logical_cores,
        cpu_model: $cpu_model,
        memory_total_bytes: $memory_total_bytes,
        memory_available_bytes: $memory_available_bytes,
        disk_total_bytes: $disk_total_bytes,
        disk_used_bytes: $disk_used_bytes,
        disk_available_bytes: $disk_available_bytes,
        uptime_seconds: $uptime_seconds,
        virtualization: $virtualization
    }'
