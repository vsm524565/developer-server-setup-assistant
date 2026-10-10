
"""
Developer Server Setup Assistant.

Connects to a remote Ubuntu server, discovers its environment,
evaluates component dependencies and conflicts, generates a
read-only installation plan, and verifies selected components.

Actual installation execution remains disabled.
"""

from getpass import getpass

from connection.ssh import SSHConnection

from discovery.components import (
    ComponentDiscovery,
    display_component_report,
)
from discovery.panel import (
    PanelDiscovery,
    display_panel_report,
)
from discovery.ports import (
    PortDiscovery,
    display_port_report,
)
from discovery.system import (
    SystemDiscovery,
    display_discovery_report,
)

from planning.approval import PlanApproval
from planning.decisions import (
    ConfigurationPlanner,
    display_configuration_plan,
)
from planning.dependencies import DependencyResolver
from planning.installation import (
    InstallationPlanner,
    display_installation_plan,
)
from planning.preflight import (
    PreflightValidator,
    display_preflight_report,
)
from planning.snapshot import (
    ServerSnapshotBuilder,
    SnapshotError,
)
from planning.state_consistency import (
    ServerStateComparator,
    display_consistency_report,
)

from verification.components import (
    ComponentVerifier,
    display_verification_report,
)


def get_server_details():
    """Collect and validate remote SSH connection details."""

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
            pass

        print("Enter a valid port number between 1 and 65535.")

    while True:
        username = input("Enter sudo username: ").strip()

        if username:
            break

        print("Username cannot be empty.")

    password = getpass("Enter password: ")

    return hostname, port, username, password


def select_components(available):
    """Collect and validate the components requested by the operator."""

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

            if not all(
                1 <= number <= len(names)
                for number in numbers
            ):
                raise ValueError

            return list(
                dict.fromkeys(
                    names[number - 1]
                    for number in numbers
                )
            )

        except ValueError:
            print(
                "Invalid selection. Enter valid component numbers."
            )


def discover_server(connection):
    """Collect system, panel, component and port information."""

    print("\nDiscovering server environment...")

    system_discovery = SystemDiscovery(connection)
    server_info = system_discovery.discover()

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

    return {
        "supported": supported,
        "system": server_info,
        "panel": panel_info,
        "components": components,
        "ports": ports,
    }


def generate_installation_plan(server_state, requested):
    """Resolve dependencies and generate a read-only installation plan."""

    resolver = DependencyResolver(server_state["components"])
    ordered_components = resolver.resolve(requested)

    print("\nResolved component order:")
    print(" -> ".join(ordered_components))

    configuration_planner = ConfigurationPlanner(
        supported=server_state["supported"],
        panel=server_state["panel"],
        components=server_state["components"],
        ports=server_state["ports"],
    )

    decisions = configuration_planner.evaluate(
        ordered_components
    )

    display_configuration_plan(decisions)

    installation_planner = InstallationPlanner(
        components=server_state["components"],
        decisions=decisions,
    )

    installation_plan = installation_planner.build(
        ordered_components
    )

    display_installation_plan(installation_plan)

    return ordered_components, installation_plan


def review_installation_plan(installation_plan):
    """Collect demonstration-only approval for the proposed plan."""

    approval = PlanApproval(installation_plan)
    validation = approval.validate()

    approved = approval.request_approval()

    if approved:
        print("\nPlan approved for this session.")

    elif validation["eligible"] and not validation["has_changes"]:
        print("\nVerification-only plan. No installation required.")

    else:
        print("\nInstallation plan was not approved.")

    return approved


def verify_components(connection, ordered_components):
    """Run read-only verification for selected components."""

    verifier = ComponentVerifier(connection)

    verification_results = [
        verifier.verify(component)
        for component in ordered_components
    ]

    display_verification_report(verification_results)

    return verification_results


def collect_server_snapshot(connection):
    """
    Collect fresh discovery data and build a normalized snapshot.

    Does not display discovery reports or modify the server.
    """

    system_info = SystemDiscovery(connection).discover()
    panel_info = PanelDiscovery(connection).discover()
    components = ComponentDiscovery(connection).discover()
    ports = PortDiscovery(connection).discover()

    return ServerSnapshotBuilder.build(
        system_info=system_info,
        panel_info=panel_info,
        components=components,
        ports=ports,
    )

def main():
    """Coordinate the remote server discovery and planning workflow."""

    hostname, port, username, password = get_server_details()

    connection = SSHConnection(
        hostname,
        port,
        username,
        password,
    )

    print(f"\nConnecting to {hostname}:{port}...")

    try:
        connection.connect()

        exit_code, hostname_output, error = connection.execute(
            "hostname"
        )

        if exit_code != 0:
            print(
                "Connected, but server validation failed: "
                f"{error}"
            )
            return

        print("SSH connection successful.")
        print(f"Remote hostname: {hostname_output}")

        print("Verifying sudo access...")

        connection.verify_sudo()

        print("Sudo access verified.")

        server_state = discover_server(connection)
        initial_snapshot = ServerSnapshotBuilder.build(
            system_info=server_state["system"],
            panel_info=server_state["panel"],
            components=server_state["components"],
            ports=server_state["ports"],
        )

        # Verification is allowed on unsupported systems,
        # but installation planning remains disabled.
        if not server_state["supported"]:
            print(
                "\nThis operating system is not supported "
                "for automated server configuration."
            )
            print(
                "Discovery completed. No server changes were made."
            )
            return

        requested = select_components(
            server_state["components"]
        )

        if not requested:
            print(
                "\nNo components selected. Discovery completed."
            )
            return

        ordered_components, installation_plan = (
            generate_installation_plan(
                server_state,
                requested,
            )
        )
        preflight = PreflightValidator(
            server_state=server_state,
            installation_plan=installation_plan,
        )


        preflight_result = preflight.validate()
        display_preflight_report(preflight_result)

        if preflight_result["passed"]:
            print("\nRefreshing server discovery before approval...")

            try:
                current_snapshot = collect_server_snapshot(connection)

                consistency_result = ServerStateComparator(
                    initial_snapshot
                ).compare(current_snapshot)

                display_consistency_report(consistency_result)

                if consistency_result["consistent"]:
                    review_installation_plan(installation_plan)
                else:
                    print(
                        "\nInstallation approval skipped: "
                        "server state changed. Replanning is required."
                    )

            except (RuntimeError, SnapshotError, ValueError) as error:
                print(
                    "\nInstallation approval skipped: "
                    "fresh server discovery could not be validated."
                )
                print(f"Reason: {error}")

        else:
            print(
                "\nInstallation approval skipped: "
                "preflight validation failed."
            )


        # Stage 3.4: read-only component verification.
        # Runs regardless of the approval outcome.
        verify_components(
            connection,
            ordered_components,
        )

        print(
            "\nWorkflow completed. "
            "No installation commands were executed."
        )

    except PermissionError as error:
        print(f"Sudo verification failed: {error}")

    except ConnectionError as error:
        print(f"Connection failed: {error}")

    except RuntimeError as error:
        print(f"Server operation failed: {error}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()
