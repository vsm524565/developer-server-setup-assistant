
#!/usr/bin/env bash
set -euo pipefail

# DSSA common time-health discovery.
#
# Read-only inspection.
# No NTP configuration, chronyc makestep, service restarts,
# package installation or system clock modification.

discover_time_health() {
    local synchronization="UNKNOWN"
    local ntp_enabled="UNKNOWN"
    local timedatectl_available=false
    local chrony_available=false
    local ntp_sync_value=""
    local ntp_enabled_value=""

    # Detect time-management utilities.
    if command -v timedatectl >/dev/null 2>&1; then
        timedatectl_available=true
    fi

    if command -v chronyc >/dev/null 2>&1; then
        chrony_available=true
    fi

    # systemd time synchronization inspection.
    # Failures are reported as UNKNOWN, not as UNSYNCHRONIZED.
    if [[ "$timedatectl_available" == true ]]; then

        ntp_sync_value="$(
            timedatectl show \
                -p NTPSynchronized --value 2>/dev/null
        )" || ntp_sync_value=""

        case "${ntp_sync_value,,}" in
            yes)
                synchronization="SYNCHRONIZED"
                ;;
            no)
                synchronization="UNSYNCHRONIZED"
                ;;
            *)
                synchronization="UNKNOWN"
                ;;
        esac

        ntp_enabled_value="$(
            timedatectl show \
                -p NTP --value 2>/dev/null
        )" || ntp_enabled_value=""

        case "${ntp_enabled_value,,}" in
            yes)
                ntp_enabled="ENABLED"
                ;;
            no)
                ntp_enabled="DISABLED"
                ;;
            *)
                ntp_enabled="UNKNOWN"
                ;;
        esac
    fi

    # chronyc availability is informational.
    # We do not trigger synchronization or modify services.

    printf '{'
    printf '"schema_version":"1.0",'
    printf '"check":"time_health",'
    printf '"synchronization":"%s",' "$synchronization"
    printf '"ntp_enabled":"%s",' "$ntp_enabled"
    printf '"timedatectl_available":%s,' "$timedatectl_available"
    printf '"chrony_available":%s,' "$chrony_available"
    printf '"execution_allowed":false'
    printf '}\n'
}
