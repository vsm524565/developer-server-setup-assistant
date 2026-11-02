"""Read-only mapping between application IDs and legacy planner names.

Mappings are for compatibility with the existing planning interface.
They never grant installation or execution permission.
"""

from bridge.planning.dependencies import DependencyResolver


LEGACY_COMPONENT_NAMES = {
    "docker": "Docker",
    "docker_compose": "Docker Compose",
    "nginx": "Nginx",
    "apache": "Apache",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "postgresql": "PostgreSQL",
    "redis": "Redis",
    "php": "PHP",
}


def to_legacy_component_name(application_id):
    """Translate an explicitly mapped application ID."""

    if not isinstance(application_id, str):
        raise ValueError("Invalid application ID")

    if application_id not in LEGACY_COMPONENT_NAMES:
        raise ValueError(
            f"No legacy planner mapping for: {application_id}"
        )

    return LEGACY_COMPONENT_NAMES[application_id]


def resolve_legacy_dependencies(application_ids):
    """Resolve mapped dependencies without creating installation actions."""

    if not isinstance(application_ids, (list, tuple)):
        raise ValueError("Application IDs must be a list or tuple")

    if not application_ids:
        raise ValueError("At least one application is required")

    if len(application_ids) != len(set(application_ids)):
        raise ValueError("Duplicate application selection")

    names = [
        to_legacy_component_name(application_id)
        for application_id in application_ids
    ]

    resolver = DependencyResolver(
        available_components=set(LEGACY_COMPONENT_NAMES.values())
    )

    ordered_names = resolver.resolve(names)

    reverse_mapping = {
        name: application_id
        for application_id, name in LEGACY_COMPONENT_NAMES.items()
    }

    return {
        "schema_version": "1.0",
        "check": "application_dependency_order",
        "requested": list(application_ids),
        "ordered_applications": [
            reverse_mapping[name]
            for name in ordered_names
        ],
        "planning_only": True,
        "execution_allowed": False,
    }
