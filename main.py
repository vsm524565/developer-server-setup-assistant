from getpass import getpass
from multiprocessing.dummy import connection
import discovery
from discovery.components import (
    ComponentDiscovery,
    display_component_report,
)
from planning.decisions import (
    ConfigurationPlanner,
    display_configuration_plan,
)
from planning.dependencies import DependencyResolver
from planning.installation import (
    InstallationPlanner,
    display_installation_plan,
)
from discovery.ports import PortDiscovery, display_port_report
from discovery.system import SystemDiscovery, display_discovery_report
from connection.ssh import SSHConnection
from discovery.panel import PanelDiscovery, display_panel_report


def get_server_details():
    """
    Collect and validate the SSH connection details required to access
    the target server.

    Returns:
        tuple: hostname, SSH port, username, and password.
    """

    while True:
        hostname = input("Enter server IP/hostname: ").strip()
        if hostname:
            break
        print("Server IP/hostname cannot be empty.")

    while True:
        port_input = input("Enter SSH port [22]: ").strip()

        if not port_input:
            port = 22
            break

        try:
            port = int(port_input)

            if 1 <= port <= 65535:
                break

            print("Port must be between 1 and 65535.")

        except ValueError:
            print("Port must be a number.")

    while True:
        username = input("Enter sudo username: ").strip()
        if username:
            break
        print("Username cannot be empty.")

    password = getpass("Enter password: ")

    return hostname, port, username, password

def select_components(available):
    """Collect and validate requested components for planning."""

    names = list(available)

    print("\n=== SELECT COMPONENTS ===")

    for index, name in enumerate(names, start=1):
        print(f"{index:>2}. {name}")

    while True:
        selection = input(
            "\nEnter component numbers separated by commas "
            "[Enter to skip]: "
        ).strip()

        if not selection:
            return []

        try:
            numbers = [
                int(value.strip())
                for value in selection.split(",")
            ]

            if not all(1 <= number <= len(names) for number in numbers):
                raise ValueError

            return list(dict.fromkeys(
                names[number - 1] for number in numbers
            ))

        except ValueError:
            print("Invalid selection. Enter valid component numbers.")

def main():
    """Run the server connection workflow."""

    hostname, port, username, password = get_server_details()

    connection = SSHConnection(hostname, port, username, password)

    print(f"\nConnecting to {hostname}:{port}...")

    try:
        connection.connect()

        exit_code, hostname_output, error = connection.execute("hostname")

        if exit_code != 0:
            print(f"Connected, but server validation failed: {error}")
            return

        print("SSH connection successful.")
        print(f"Remote hostname: {hostname_output}")

        print("Verifying sudo access...")

        connection.verify_sudo()
        
        print("Sudo access verified.")
        print("\nDiscovering server environment...")

        discovery = SystemDiscovery(connection)
        server_info = discovery.discover()

        supported = display_discovery_report(server_info)

        print("\nChecking existing control panel...")
        panel_discovery = PanelDiscovery(connection)
        panel_info = panel_discovery.discover()
        display_panel_report(panel_info)
        print("\nDiscovering existing software...")
        component_discovery = ComponentDiscovery(connection)
        components = component_discovery.discover()
        display_component_report(components)

        print("\nDiscovering listening ports...")
        port_discovery = PortDiscovery(connection)
        ports = port_discovery.discover()
        display_port_report(ports)
        requested = select_components(components)

        if requested:
            resolver = DependencyResolver(components)
            ordered_components = resolver.resolve(requested)

            print("\nResolved component order:")
            print(" -> ".join(ordered_components))

            planner = ConfigurationPlanner(
                supported=supported,
                panel=panel_info,
                components=components,
                ports=ports,
            )

            decisions = planner.evaluate(ordered_components)
            display_configuration_plan(decisions)

            installation_planner = InstallationPlanner(
                components=components,
                decisions=decisions,
            )

            installation_plan = installation_planner.build(
                ordered_components
            )

            display_installation_plan(installation_plan)

        else:
            print("\nNo components selected. Discovery completed.")

        if not supported:
            print(
                "\nThis operating system is not supported for automated "
                "server configuration."
            )
            print("Discovery completed. No server changes were made.")
            return
    except RuntimeError as error:
        print(f"Server discovery failed: {error}")

    except PermissionError as error:
        print(f"Sudo verification failed: {error}")

    except ConnectionError as error:
        print(f"Connection failed: {error}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()