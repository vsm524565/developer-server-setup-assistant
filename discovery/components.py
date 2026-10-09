import shlex


class ComponentDiscovery:
    """
    Discover installed software and running services on a remote server.

    Detection is read-only and uses executable availability and
    systemd service state.
    """

    COMPONENTS = {
        "Nginx": {
            "command": "nginx",
            "service": "nginx",
        },
        "Apache": {
            "command": "apache2",
            "service": "apache2",
        },
        "MySQL": {
            "command": "mysqld",
            "service": "mysql",
        },
        "MariaDB": {
            "command": "mariadbd",
            "service": "mariadb",
        },
        "PostgreSQL": {
            "command": "psql",
            "service": "postgresql",
        },
        "Redis": {
            "command": "redis-server",
            "service": "redis-server",
        },
        "Docker": {
            "command": "docker",
            "service": "docker",
        },
        "Docker Compose": {
            "command": "docker",
            "service": None,
            "subcommand": "docker compose version",
        },
        "PHP": {
            "command": "php",
            "service": None,
        },
        "Node.js": {
            "command": "node",
            "service": None,
        },
        "Python": {
            "command": "python3",
            "service": None,
        },
        "Git": {
            "command": "git",
            "service": None,
        },
    }

    def __init__(self, connection):
        self.connection = connection

    def command_exists(self, command):
        """Determine whether an executable is available on PATH."""
        exit_code, _, error = self.connection.execute(
            f"command -v {shlex.quote(command)}"
        )

        if exit_code not in (0, 1):
            raise RuntimeError(
                f"Executable detection failed: {error}"
            )

        return exit_code == 0

    def service_active(self, service):
        """Check whether a systemd service is active."""
        exit_code, output, error = self.connection.execute(
            f"systemctl is-active {shlex.quote(service)}"
        )

        if exit_code not in (0, 3, 4):
            raise RuntimeError(
                f"Service detection failed for {service}: {error}"
            )

        return output == "active"

    def discover(self):
        """Collect installed and running status for supported components."""
        results = {}

        for name, config in self.COMPONENTS.items():
            installed = self.command_exists(config["command"])

            if installed and config.get("subcommand"):
                exit_code, _, _ = self.connection.execute(
                    config["subcommand"]
                )
                installed = exit_code == 0

            service = config["service"]

            active = (
                self.service_active(service)
                if installed and service
                else None
            )

            results[name] = {
                "installed": installed,
                "service_active": active,
            }

        return results


def display_component_report(components):
    """Display discovered software and service states."""
    print("\n=== INSTALLED COMPONENTS ===")
    print(f"{'Component':<20} {'Installed':<12} {'Service'}")
    print("-" * 45)

    for name, info in components.items():
        installed = "Yes" if info["installed"] else "No"

        if info["service_active"] is None:
            service = "N/A"
        else:
            service = (
                "Active" if info["service_active"] else "Inactive"
            )

        print(f"{name:<20} {installed:<12} {service}")