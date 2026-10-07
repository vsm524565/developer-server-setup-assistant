import paramiko


class SSHConnection:
    """
    Manage the SSH connection to the target server and provide a reusable
    interface for executing remote commands.
    """

    def __init__(self, hostname, port, username, password):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.client = None

    def connect(self):
        """
        Establish an SSH connection using the supplied server credentials.

        Returns:
            bool: True when the connection is established successfully.
        """

        self.client = paramiko.SSHClient()

        # Accept unknown host keys for the initial version of the tool.
        # Host-key verification will be strengthened before production release.
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self.client.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=10,
                auth_timeout=10,
                banner_timeout=10,
            )

            return True

        except paramiko.AuthenticationException:
            raise ConnectionError("SSH authentication failed.")

        except paramiko.SSHException as error:
            raise ConnectionError(f"SSH connection failed: {error}")

        except OSError as error:
            raise ConnectionError(f"Unable to reach the server: {error}")

    def execute(self, command):
        """
        Execute a command on the connected server.

        Returns:
            tuple: command exit code, standard output, and standard error.
        """

        if self.client is None:
            raise ConnectionError("SSH connection has not been established.")

        stdin, stdout, stderr = self.client.exec_command(command)

        exit_code = stdout.channel.recv_exit_status()

        return (
            exit_code,
            stdout.read().decode().strip(),
            stderr.read().decode().strip(),
        )

    def close(self):
        """Close the active SSH connection."""

        if self.client:
            self.client.close()
            self.client = None

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