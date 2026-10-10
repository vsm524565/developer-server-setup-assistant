
"""
Normalize existing server discovery results into a consistent snapshot.

Read-only transformation. No SSH commands or installation actions.
"""

from copy import deepcopy

from discovery.components import ComponentDiscovery
from discovery.ports import PortDiscovery


class SnapshotError(ValueError):
    """Discovery data is incomplete or invalid."""


class ServerSnapshotBuilder:
    """Build normalized, execution-relevant server snapshots."""

    REQUIRED_SYSTEM_FIELDS = (
        "hostname",
        "os_id",
        "os_version",
        "architecture",
    )

    @staticmethod
    def build(system_info, panel_info, components, ports):
        if not isinstance(system_info, dict):
            raise SnapshotError("System discovery must be a dictionary.")

        system = {}

        for field in ServerSnapshotBuilder.REQUIRED_SYSTEM_FIELDS:
            value = system_info.get(field)

            if not isinstance(value, str) or not value.strip():
                raise SnapshotError(
                    f"Missing or invalid system field: {field}"
                )

            system[field] = value.strip()

        if not isinstance(panel_info, dict):
            raise SnapshotError("Panel discovery is invalid.")

        panel_name = panel_info.get("panel")
        panel_status = panel_info.get("status")

        valid_panel_states = {
            ("None", "NOT_INSTALLED"),
            ("aaPanel", "INCOMPLETE"),
            ("aaPanel", "INSTALLED_ACTIVE"),
            ("aaPanel", "INSTALLED_INACTIVE"),
        }

        if (panel_name, panel_status) not in valid_panel_states:
            raise SnapshotError("Panel discovery is incomplete or invalid.")

        if not isinstance(components, dict):
            raise SnapshotError("Component discovery is invalid.")

        normalized_components = {}

        for name, config in ComponentDiscovery.COMPONENTS.items():
            entry = components.get(name)

            if not isinstance(entry, dict):
                raise SnapshotError(
                    f"Missing component discovery: {name}"
                )

            installed = entry.get("installed")
            active = entry.get("service_active")

            if type(installed) is not bool:
                raise SnapshotError(
                    f"Invalid installation state: {name}"
                )

            if active is not None and type(active) is not bool:
                raise SnapshotError(
                    f"Invalid service state: {name}"
                )

            if not installed and active is not None:
                raise SnapshotError(
                    f"Inconsistent service state: {name}"
                )

            if installed and config["service"] and active is None:
                raise SnapshotError(
                    f"Missing service state: {name}"
                )

            normalized_components[name] = {
                "installed": installed,
                "service_active": active,
            }

        if not isinstance(ports, dict):
            raise SnapshotError("Port discovery is invalid.")

        normalized_ports = {}

        for port in PortDiscovery.PORTS:
            entry = ports.get(port)

            if not isinstance(entry, dict):
                raise SnapshotError(
                    f"Missing port discovery: {port}"
                )

            listening = entry.get("listening")
            listeners = entry.get("listeners")

            if type(listening) is not bool:
                raise SnapshotError(
                    f"Invalid listening state: {port}"
                )

            if not isinstance(listeners, list):
                raise SnapshotError(
                    f"Invalid listener inventory: {port}"
                )

            if listening and not listeners:
                raise SnapshotError(
                    f"Missing listener details: {port}"
                )

            if not listening and listeners:
                raise SnapshotError(
                    f"Inconsistent listener details: {port}"
                )

            normalized_ports[str(port)] = (
                "LISTENING" if listening else "FREE"
            )

        return deepcopy({
            **system,
            "panel": {
                "name": panel_name,
                "status": panel_status,
            },
            "components": normalized_components,
            "ports": normalized_ports,
        })
