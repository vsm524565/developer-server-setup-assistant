"""Load shared component definitions from ``definitions/components``.

Loading validates each file, cross-checks the application catalog and
rejects dependency cycles. It reads local files only and never connects
to a server.
"""

import grp
import json
import os
import pwd
import stat
from pathlib import Path

from bridge.components.contracts import (
    DefinitionError,
    parse_component_definition,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEFINITIONS_DIR = ROOT / "definitions" / "components"
MAX_DEFINITION_BYTES = 256 * 1024


def _private_group(gid, uid):
    """True when ``gid`` is the owner's own group with no other members."""

    try:
        group = grp.getgrgid(gid)
        owner = pwd.getpwuid(uid)
    except KeyError:
        return False
    if group.gr_mem or group.gr_name != owner.pw_name or owner.pw_gid != gid:
        return False
    return not any(entry.pw_gid == gid and entry.pw_uid != uid for entry in pwd.getpwall())


def check_definition_path(path):
    """Refuse a definition file or folder others could change (4.35).

    Same rule as ``dssa_check_definition_path`` in the Bash backend: a
    real file or folder, owned by the current user or root, never
    writable by others, and group-writable only for the owner's private
    group with no other members.
    """

    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise DefinitionError(f"{path}: definition path is missing") from None
    if stat.S_ISLNK(info.st_mode):
        raise DefinitionError(f"{path}: definition path must not be a symlink")
    if info.st_uid not in (os.getuid(), 0):
        raise DefinitionError(f"{path}: definition path is owned by another user")
    if info.st_mode & stat.S_IWOTH:
        raise DefinitionError(f"{path}: writable by other users (fix: chmod o-w)")
    if info.st_mode & stat.S_IWGRP and not _private_group(info.st_gid, info.st_uid):
        raise DefinitionError(f"{path}: writable by a shared group (fix: chmod g-w)")


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
    check_definition_path(path.parent)
    check_definition_path(path)
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
    check_definition_path(directory)

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
