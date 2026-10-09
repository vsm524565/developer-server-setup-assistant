import shlex


class SystemDiscovery:
    """
    Collect read-only operating system and resource information from
    a remote Linux server for compatibility and readiness evaluation.
    """

    def __init__(self, connection):
        self.connection = connection

    def run(self, command):
        """Execute a discovery command and return its output."""

        exit_code, output, error = self.connection.execute(command)

        if exit_code != 0:
            raise RuntimeError(
                f"Discovery command failed: {error or command}"
            )

        return output

    def discover(self):
        """
        Gather server identity, operating system, CPU, memory, disk,
        and swap information.
        """

        os_info = self.run("cat /etc/os-release")

        os_details = {}
        for line in os_info.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_details[key] = value.strip().strip('"')

        memory = self.run("free -b").splitlines()
        memory_values = memory[1].split()
        swap_values = memory[2].split()

        disk_values = self.run("df -B1 -P /").splitlines()[1].split()

        return {
            "hostname": self.run("hostname"),
            "os": os_details.get("PRETTY_NAME", "Unknown"),
            "os_id": os_details.get("ID", "unknown"),
            "os_version": os_details.get("VERSION_ID", "unknown"),
            "kernel": self.run("uname -r"),
            "architecture": self.run("uname -m"),
            "cpu_cores": int(self.run("nproc")),
            "memory_total": int(memory_values[1]),
            "memory_available": int(memory_values[6]),
            "disk_total": int(disk_values[1]),
            "disk_available": int(disk_values[3]),
            "swap_total": int(swap_values[1]),
            "swap_used": int(swap_values[2]),
        }


def display_discovery_report(info):
    """Present server discovery results in a readable format."""

    gib = 1024 ** 3

    supported = (
        info["os_id"] == "ubuntu"
        and info["os_version"] in ("22.04", "24.04")
        and info["architecture"] == "x86_64"
    )

    print("\n=== SERVER DISCOVERY REPORT ===")
    print(f"Hostname       : {info['hostname']}")
    print(f"OS             : {info['os']}")
    print(f"Kernel         : {info['kernel']}")
    print(f"Architecture   : {info['architecture']}")
    print(f"CPU Cores      : {info['cpu_cores']}")
    print(f"Total RAM      : {info['memory_total'] / gib:.2f} GiB")
    print(f"Available RAM  : {info['memory_available'] / gib:.2f} GiB")
    print(f"Total Disk     : {info['disk_total'] / gib:.2f} GiB")
    print(f"Available Disk : {info['disk_available'] / gib:.2f} GiB")
    print(f"Total Swap     : {info['swap_total'] / gib:.2f} GiB")
    print(f"Used Swap      : {info['swap_used'] / gib:.2f} GiB")
    print(f"Compatibility  : {'SUPPORTED' if supported else 'UNSUPPORTED'}")

    return supported