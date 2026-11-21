"""Explicit catalog of DSSA application definitions."""

from importlib import import_module

from bridge.applications.registry import ApplicationRegistry


APPLICATION_MODULES = (
    "cache.memcached",
    "cache.redis",
    "containers.docker",
    "containers.docker_compose",
    "databases.elasticsearch",
    "databases.mariadb",
    "databases.mongodb",
    "databases.mysql",
    "databases.opensearch",
    "databases.postgresql",
    "panels.aapanel",
    "panels.cloudpanel",
    "panels.cpanel",
    "panels.cyberpanel",
    "panels.directadmin",
    "panels.hestia",
    "panels.plesk",
    "proxy_ssl.caddy",
    "proxy_ssl.certbot",
    "proxy_ssl.haproxy",
    "proxy_ssl.varnish",
    "runtimes.java",
    "runtimes.nodejs",
    "runtimes.php",
    "runtimes.php82",
    "runtimes.php83",
    "runtimes.php84",
    "runtimes.python",
    "system.chrony",
    "system.clamav",
    "system.fail2ban",
    "system.firewall",
    "system.postfix",
    "system.swap",
    "system.ufw",
    "tools.composer",
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
