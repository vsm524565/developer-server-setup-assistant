"""Load shared component definitions from ``definitions/components``.

Loading validates each file, cross-checks the application catalog and
rejects dependency cycles. It reads local files only and never connects
to a server.
"""

import json
from pathlib import Path

from bridge.components.contracts import (
    DefinitionError,
    parse_component_definition,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEFINITIONS_DIR = ROOT / "definitions" / "components"
MAX_DEFINITION_BYTES = 256 * 1024


def _reject_duplicate_keys(pairs):
    """Refuse JSON objects that repeat a key instead of silently keeping one."""

    result = {}
    for key, value in pairs:
        if key in result:
            raise DefinitionError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_component_definition(path):
    """Load and validate one definition file named ``<id>.json``."""

    path = Path(path)

    if path.suffix != ".json":
        raise DefinitionError(f"{path.name}: definition must be a .json file")
    if path.is_symlink() or not path.is_file():
        raise DefinitionError(f"{path.name}: must be a regular file")
    if path.stat().st_size > MAX_DEFINITION_BYTES:
        raise DefinitionError(f"{path.name}: definition is too large")

    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as error:
        raise DefinitionError(f"{path.name}: invalid JSON: {error}") from None

    return parse_component_definition(data, expected_id=path.stem)


def _check_dependencies(definitions):
    """Require known dependencies and an acyclic dependency graph."""

    for definition in definitions.values():
        for dependency in definition.depends_on:
            if dependency.component_id not in definitions:
                raise DefinitionError(
                    f"{definition.component_id}: unknown dependency "
                    f"{dependency.component_id!r}"
                )

    visiting, done = set(), set()

    def visit(component_id, chain):
        if component_id in done:
            return
        if component_id in visiting:
            cycle = " -> ".join(chain + [component_id])
            raise DefinitionError(f"dependency cycle: {cycle}")

        visiting.add(component_id)
        for dependency in definitions[component_id].depends_on:
            visit(dependency.component_id, chain + [component_id])
        visiting.discard(component_id)
        done.add(component_id)

    for component_id in sorted(definitions):
        visit(component_id, [])


def load_component_definitions(directory=None, catalog=None):
    """Load every definition in a directory, keyed by component ID.

    When ``catalog`` (an ApplicationRegistry) is given, each definition
    must name a registered application with the same category.
    """

    directory = Path(directory or DEFAULT_DEFINITIONS_DIR)
    if not directory.is_dir():
        raise DefinitionError(f"definitions directory not found: {directory}")

    definitions = {}
    for path in sorted(directory.glob("*.json")):
        definition = load_component_definition(path)
        definitions[definition.component_id] = definition

    if catalog is not None:
        for definition in definitions.values():
            if definition.component_id not in catalog:
                raise DefinitionError(
                    f"{definition.component_id}: not in the application catalog"
                )
            expected = catalog.get(definition.component_id).category
            if definition.category != expected:
                raise DefinitionError(
                    f"{definition.component_id}: category "
                    f"{definition.category!r} does not match catalog "
                    f"{expected!r}"
                )

    _check_dependencies(definitions)
    return definitions
