
from getpass import getpass

from connection.ssh import SSHConnection


hostname = input("Server IP/hostname: ").strip()
port = int(input("SSH port [22]: ").strip() or "22")
username = input("SSH username: ").strip()
password = getpass("SSH password: ")

connection = SSHConnection(
    hostname, port, username, password
)

try:
    connection.connect()

    fingerprint = connection.get_host_key_fingerprint()

    print("\n=== AUTHENTICATED SSH IDENTITY ===")
    print(f"Target      : {hostname}:{port}")
    print(f"Username    : {username}")
    print(f"Fingerprint : {fingerprint}")

    assert fingerprint.startswith("SHA256:")
    assert len(fingerprint) > len("SHA256:")

    print("\nSSH identity extraction passed.")

finally:
    connection.close()
