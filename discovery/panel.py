import shlex


class PanelDiscovery:
    """
    Detect aaPanel installation indicators and service state on a
    remote Linux server without changing its configuration.
    """

    PANEL_PATH = "/www/server/panel"
    PANEL_ENTRY = "/www/server/panel/BT-Panel"
    SERVICE = "bt"

    def __init__(self, connection):
        self.connection = connection

    def path_exists(self, path, file_type="directory"):
        """Check whether a remote directory or regular file exists."""
        flag = "-d" if file_type == "directory" else "-f"
        command = f"test {flag} {shlex.quote(path)}"
        exit_code, _, error = self.connection.execute(command)

        if exit_code not in (0, 1):
            raise RuntimeError(
                f"Panel path verification failed: {error}"
            )

        return exit_code == 0

    def service_active(self):
        """Check whether the aaPanel service is running."""
        exit_code, output, error = self.connection.execute(
            f"systemctl is-active {self.SERVICE}"
        )

        if exit_code not in (0, 3, 4):
            raise RuntimeError(
                f"Panel service verification failed: {error}"
            )

        return output == "active"

    def discover(self):
        """Classify aaPanel installation and runtime status."""
        directory_exists = self.path_exists(self.PANEL_PATH)

        if not directory_exists:
            return {
                "panel": "None",
                "status": "NOT_INSTALLED",
                "installation_path": None,
            }

        entry_exists = self.path_exists(
            self.PANEL_ENTRY, file_type="file"
        )

        if not entry_exists:
            return {
                "panel": "aaPanel",
                "status": "INCOMPLETE",
                "installation_path": self.PANEL_PATH,
            }

        active = self.service_active()

        return {
            "panel": "aaPanel",
            "status": (
                "INSTALLED_ACTIVE" if active else "INSTALLED_INACTIVE"
            ),
            "installation_path": self.PANEL_PATH,
        }


def display_panel_report(info):
    """Display the detected panel installation state."""
    print("\n=== CONTROL PANEL DISCOVERY ===")
    print(f"Panel        : {info['panel']}")
    print(f"Status       : {info['status']}")

    if info["installation_path"]:
        print(f"Installation : {info['installation_path']}")