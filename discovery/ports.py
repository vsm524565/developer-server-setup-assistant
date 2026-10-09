import re


class PortDiscovery:
    """
    Discover TCP listening ports and their associated processes.

    The module performs read-only inspection and does not
    modify firewall rules or running services.
    """

    PORTS = {
        80: "HTTP",
        443: "HTTPS",
        3306: "MySQL/MariaDB",
        5432: "PostgreSQL",
        6379: "Redis",
        8080: "Alternative HTTP",
    }

    def __init__(self, connection):
        self.connection = connection

    def discover(self):
        """Collect listening TCP sockets on monitored ports."""

        exit_code, output, error = self.connection.execute(
            "ss -H -ltnp"
        )

        if exit_code != 0:
            raise RuntimeError(
                f"Port discovery failed: {error or 'ss command failed'}"
            )

        results = {
            port: {
                "service": name,
                "listening": False,
                "listeners": [],
            }
            for port, name in self.PORTS.items()
        }

        for line in output.splitlines():
            fields = line.split(maxsplit=5)

            if len(fields) < 5:
                continue

            local_address = fields[3]
            match = re.search(r":(\d+)$", local_address)

            if not match:
                continue

            port = int(match.group(1))

            if port not in results:
                continue

            process_match = re.search(
                r'users:\(\("([^"]+)"',
                line,
            )

            process = (
                process_match.group(1)
                if process_match
                else "Unknown"
            )

            results[port]["listening"] = True
            results[port]["listeners"].append({
                "address": local_address,
                "process": process,
            })

        return results


def display_port_report(ports):
    """Display TCP listener information for monitored ports."""

    print("\n=== PORT DISCOVERY REPORT ===")
    print(
        f"{'Port':<8} {'Expected Service':<20} "
        f"{'Status':<12} {'Process'}"
    )
    print("-" * 65)

    for port, info in ports.items():
        if not info["listening"]:
            print(
                f"{port:<8} {info['service']:<20} "
                f"{'FREE':<12} -"
            )
            continue

        processes = sorted({
            listener["process"]
            for listener in info["listeners"]
        })

        print(
            f"{port:<8} {info['service']:<20} "
            f"{'LISTENING':<12} {', '.join(processes)}"
        )