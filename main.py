from getpass import getpass
from multiprocessing.dummy import connection

from connection.ssh import SSHConnection


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

def verify_sudo(self):
    """
    Verify that the connected account can authenticate with sudo and
    execute privileged commands.

    Returns:
        bool: True when sudo access is successfully verified.
    """

    if self.client is None:
        raise ConnectionError("SSH connection has not been established.")

    stdin, stdout, stderr = self.client.exec_command(
        "sudo -S -p '' id -u"
    )

    stdin.write(self.password + "\n")
    stdin.flush()

    exit_code = stdout.channel.recv_exit_status()
    output = stdout.read().decode().strip()
    error = stderr.read().decode().strip()

    if exit_code != 0:
        raise PermissionError(
            error or "Sudo authentication or authorization failed."
        )

    if output != "0":
        raise PermissionError(
            "Sudo command did not execute with root privileges."
        )

    return True

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

    except PermissionError as error:
        print(f"Sudo verification failed: {error}")

    except ConnectionError as error:
        print(f"Connection failed: {error}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()