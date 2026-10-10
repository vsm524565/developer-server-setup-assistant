
"""
Read-only component verification.

Checks software availability, service state and selected
configuration validation commands over SSH.
"""

import shlex


class ComponentVerifier:
    """Verify installed components without changing server state."""

    CHECKS = {
        "Nginx": {
            "binary": "nginx",
            "service": "nginx",
            "config_test": "nginx -t",
        },
        "Apache": {
            "binary": "apache2",
            "service": "apache2",
            "config_test": "apache2ctl configtest",
        },
        "MySQL": {
            "binary": "mysqld",
            "service": "mysql",
        },
        "MariaDB": {
            "binary": "mariadbd",
            "service": "mariadb",
        },
        "PostgreSQL": {
            "binary": "psql",
            "service": "postgresql",
        },
        "Redis": {
            "binary": "redis-server",
            "service": "redis-server",
        },
        "Docker": {
            "binary": "docker",
            "service": "docker",
        },
        "Docker Compose": {
            "binary": "docker",
            "version_command": "docker compose version",
        },
        "PHP": {
            "binary": "php",
        },
        "Node.js": {
            "binary": "node",
        },
        "Python": {
            "binary": "python3",
        },
        "Git": {
            "binary": "git",
        },
    }

    def __init__(self, ssh_connection):
        self.ssh = ssh_connection

    def _run(self, command):
        """Execute a read-only verification command."""
        exit_code, stdout, stderr = self.ssh.execute(command)

        return {
            "success": exit_code == 0,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "exit_code": exit_code,
        }

    def verify(self, component):
        """Return verification details for a known component."""
        if component not in self.CHECKS:
            raise ValueError(f"Unsupported component: {component}")

        check = self.CHECKS[component]

        binary = shlex.quote(check["binary"])
        binary_result = self._run(f"command -v {binary}")

        result = {
            "component": component,
            "installed": binary_result["success"],
            "service_active": None,
            "config_valid": None,
            "version_available": None,
            "status": "NOT_INSTALLED",
        }

        if not result["installed"]:
            return result

        if "version_command" in check:
            version_result = self._run(check["version_command"])
            result["version_available"] = version_result["success"]

            if not version_result["success"]:
                result["status"] = "VERIFICATION_FAILED"
                return result

        if "service" in check:
            service = shlex.quote(check["service"])
            service_result = self._run(
                f"systemctl is-active {service}"
            )
            result["service_active"] = (
                service_result["stdout"] == "active"
            )

        if "config_test" in check:
            config_result = self._run(check["config_test"])
            result["config_valid"] = config_result["success"]

        failed = (
            result["service_active"] is False
            or result["config_valid"] is False
        )

        result["status"] = (
            "VERIFICATION_FAILED" if failed else "VERIFIED"
        )

        return result


def display_verification_report(results):
    """Display component verification outcomes."""
    print("\n=== COMPONENT VERIFICATION REPORT ===")

    for result in results:
        print(
            f"{result['component']:<18} "
            f"{result['status']}"
        )

        if result["installed"]:
            if result["service_active"] is not None:
                print(
                    f"  Service active: {result['service_active']}"
                )

            if result["config_valid"] is not None:
                print(
                    f"  Config valid: {result['config_valid']}"
                )

            if result["version_available"] is not None:
                print(
                    f"  Version available: "
                    f"{result['version_available']}"
                )
