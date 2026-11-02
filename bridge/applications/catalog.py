"""Explicit catalog of DSSA application definitions."""

from importlib import import_module

from bridge.applications.registry import ApplicationRegistry


APPLICATION_MODULES = (
    "cache.memcached",
    "cache.redis",
    "containers.docker",
    "containers.docker_compose",
    "databases.mariadb",
    "databases.mysql",
    "databases.postgresql",
    "panels.aapanel",
    "panels.cyberpanel",
    "panels.hestia",
    "proxy_ssl.caddy",
    "proxy_ssl.certbot",
    "runtimes.java",
    "runtimes.nodejs",
    "runtimes.php",
    "runtimes.python",
    "system.fail2ban",
    "system.swap",
    "system.ufw",
    "tools.git",
    "web.apache",
    "web.nginx",
    "web.openlitespeed",
)


def build_application_catalog():
    """Load and register the explicitly supported catalog modules."""

    registry = ApplicationRegistry()

    for module_name in APPLICATION_MODULES:
        module = import_module(
            f"bridge.applications.definitions.{module_name}"
        )

        definition = module.get_definition()
        registry.register(definition)

    return registry
