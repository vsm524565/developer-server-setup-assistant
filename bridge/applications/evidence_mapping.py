
"""Map validated Linux discovery data to application observations.

This module does not collect evidence or validate its provenance.
Callers must first pass evidence through the controller evidence gate.
"""

KNOWN_WEB_SERVERS = frozenset({
    "nginx",
    "apache",
    "openlitespeed",
    "litespeed",
})

KNOWN_PANELS = frozenset({
    "aapanel",
    "cyberpanel",
    "hestia",
    "cpanel",
    "directadmin",
    "plesk",
})


def map_web_server_observations(data):
    """Extract web-server presence and service observations."""

    if not isinstance(data, dict):
        raise ValueError("Invalid web-server discovery data")

    servers = data.get("web_servers")

    if not isinstance(servers, dict):
        raise ValueError("Missing web_servers discovery data")

    observations = {}

    for server in KNOWN_WEB_SERVERS:
        record = servers.get(server)

        if not isinstance(record, dict):
            raise ValueError(f"Missing web-server record: {server}")

        available = record.get("available")
        detection = record.get("detection_status")
        state = record.get("service_state")

        if type(available) is not bool:
            raise ValueError(f"Invalid availability: {server}")

        if detection not in ("detected", "absent", "unidentified"):
            raise ValueError(f"Invalid detection status: {server}")

        if state not in ("active", "inactive", "failed", "unknown"):
            raise ValueError(f"Invalid service state: {server}")

        if available and detection == "detected":
            present = True
        elif not available and detection == "absent":
            present = False
        else:
            present = None

        observations[f"{server}.binary_present"] = present

        if state == "active":
            active = True
        elif state in ("inactive", "failed"):
            active = False
        else:
            active = None

        observations[f"{server}.service_active"] = active

    return observations


def map_port_observations(data, ports):
    """Evaluate TCP listener presence for specified ports."""

    if not isinstance(data, dict):
        raise ValueError("Invalid port discovery data")

    listeners = data.get("listening_tcp_ports")

    if not isinstance(listeners, list):
        raise ValueError("Missing listening_tcp_ports")

    if any(
        type(port) is not int or port < 1 or port > 65535
        for port in listeners
    ):
        raise ValueError("Invalid listening TCP port")

    if len(listeners) != len(set(listeners)):
        raise ValueError("Duplicate listening TCP ports")

    if not isinstance(ports, (list, tuple)):
        raise ValueError("Invalid requested ports")

    observations = {}
    listening = set(listeners)

    for port in ports:
        if type(port) is not int or port < 1 or port > 65535:
            raise ValueError("Invalid requested port")

        observations[f"tcp_port.{port}.listening"] = (
            port in listening
        )

    return observations


def map_panel_observations(data):
    """Map panel installation indicators, not operational health."""

    if not isinstance(data, dict):
        raise ValueError("Invalid panel discovery data")

    panels = data.get("panels")

    if not isinstance(panels, list):
        raise ValueError("Missing panel discovery data")

    if (
        any(
            not isinstance(panel, str)
            or panel not in KNOWN_PANELS
            for panel in panels
        )
        or len(panels) != len(set(panels))
    ):
        raise ValueError("Invalid panel indicators")

    detected = set(panels)

    return {
        f"{panel}.path_indicator_present": panel in detected
        for panel in sorted(KNOWN_PANELS)
    }
