"""
Registry of approved remote operations.

Only explicitly registered operation identifiers may be executed.
"""

OPERATIONS = {
    "check_hostname": {
        "command": "hostname",
        "sudo": False,
        "timeout": 10,
    },
    "check_identity": {
        "command": "id -u",
        "sudo": False,
        "timeout": 10,
    },
    "check_root_identity": {
        "command": "id -u",
        "sudo": True,
        "timeout": 10,
    },
    "check_nginx_service": {
        "command": "systemctl is-active nginx",
        "sudo": False,
        "timeout": 10,
    },
    "test_timeout": {
        "command": "sleep 5",
        "sudo": False,
        "timeout": 2,
    },
}