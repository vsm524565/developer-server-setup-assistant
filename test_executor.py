from getpass import getpass

import paramiko

from execution.executor import (
    RemoteExecutor,
    display_execution_result,
)


host = input("Server IP: ").strip()
username = input("SSH username: ").strip()
password = getpass("Password: ")

client = paramiko.SSHClient()
client.load_system_host_keys()
client.load_host_keys(
    str(__import__("pathlib").Path.home() / ".ssh" / "known_hosts")
)
client.set_missing_host_key_policy(paramiko.RejectPolicy())

try:
    client.connect(
        hostname=host,
        username=username,
        password=password,
        timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )

    executor = RemoteExecutor(client, password)

    for operation in (
        "check_hostname",
        "check_identity",
        "check_root_identity",
        "check_nginx_service",
        "test_timeout",
    ):
        result = executor.execute(operation)
        display_execution_result(result)

    try:
        executor.execute("install_nginx")
    except ValueError as error:
        print(f"\nSecurity test: {error}")

finally:
    client.close()