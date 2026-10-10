class DependencyResolver:
    """
    Resolve component dependencies and produce an ordered component list.

    Dependencies are placed before their dependents. The resolver
    detects unknown components and circular dependency definitions.
    """

    DEPENDENCIES = {
        "Docker Compose": ("Docker",),
    }

    def __init__(self, available_components):
        self.available_components = set(available_components)

    def resolve(self, requested):
        """Return dependency-ordered components without duplicates."""
        ordered = []
        visited = set()
        visiting = set()

        def visit(component):
            if component not in self.available_components:
                raise ValueError(
                    f"Unsupported component: {component}"
                )

            if component in visiting:
                raise ValueError(
                    f"Circular dependency detected: {component}"
                )

            if component in visited:
                return

            visiting.add(component)

            for dependency in self.DEPENDENCIES.get(component, ()):
                visit(dependency)

            visiting.remove(component)
            visited.add(component)
            ordered.append(component)

        for component in requested:
            visit(component)

        return ordered