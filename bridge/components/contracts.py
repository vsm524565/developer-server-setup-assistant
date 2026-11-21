"""Validated, immutable models of shared component definitions.

Every field is checked against an allowlist or a safe pattern, because
definition values eventually reach privileged commands on the target.
The Bash backend repeats these checks independently before use.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from bridge.applications.contracts import APPLICATION_CATEGORIES


SCHEMA_VERSION = "1.0"

PACKAGE_MANAGERS = frozenset({"apt", "dnf"})
SUPPORT_LEVELS = frozenset({"draft", "supported"})
ARCHITECTURES = frozenset({"x86_64", "aarch64"})
PACKAGE_ROLES = frozenset({"prerequisite", "main"})
# setup (components phase): a hook a step may run, e.g. postgresql initdb.
HOOK_KINDS = frozenset({"precheck", "verify", "setup"})
FIREWALL_SERVICES = frozenset({"ssh", "http", "https"})
DETECTION_STATES = frozenset({"swap_active", "firewall_active"})
CONFIG_TEST_COMMANDS = frozenset({"nginx -t"})

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OS_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
VERSION_RE = re.compile(r"^[0-9][0-9.]{0,15}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]{0,127}$")
EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
PATH_RE = re.compile(r"^(/[A-Za-z0-9._@+-]+)+$")
UNIT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,127}"
    r"\.(service|socket|timer|target|mount)$"
)
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# An apt suite may have path parts, e.g. MongoDB's noble/mongodb-org/8.0 (4.47).
SUITE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}(/[a-z0-9][a-z0-9._-]{0,63}){0,3}$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$")
# A signing key URL may carry a simple query (keyserver lookups); the key
# is checked against its fingerprint after download anyway (4.47).
KEY_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?([?][A-Za-z0-9=&_.-]{1,200})?$")
FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")
# dnf vendor repositories (4.36): DSSA writes the repo file itself, so
# both files live only in their fixed system folders.
RPM_KEY_PATH_RE = re.compile(r"^/etc/pki/rpm-gpg/[A-Za-z0-9._-]{1,128}$")
YUM_REPO_PATH_RE = re.compile(r"^/etc/yum\.repos\.d/[A-Za-z0-9._-]{1,128}\.repo$")
HOOK_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TEMPLATE_RE = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_.-]+)*$")
MODE_RE = re.compile(r"^0[0-7]{3}$")
OWNER_RE = re.compile(r"^[a-z_][a-z0-9_-]*(:[a-z_][a-z0-9_-]*)?$")
STATUS_RE = re.compile(r"^[1-5](xx|[0-9]{2})$")
# 4.39: a POSIX extended regex from characters that mean the same in
# bash and Python; the first line of version output must match it.
VERSION_PATTERN_RE = re.compile(r"^[A-Za-z0-9 ^$.*+?()\[\]{}|\\:_,/-]{1,200}$")


class DefinitionError(ValueError):
    """A component definition failed validation."""


# --- primitive validators -------------------------------------------------

def _fail(where, message):
    raise DefinitionError(f"{where}: {message}")


def _object(value, where, required=(), optional=()):
    if not isinstance(value, dict):
        _fail(where, "must be an object")

    allowed = set(required) | set(optional)
    unknown = sorted(set(value) - allowed)
    if unknown:
        _fail(where, f"unknown field(s): {', '.join(unknown)}")

    missing = sorted(set(required) - set(value))
    if missing:
        _fail(where, f"missing field(s): {', '.join(missing)}")

    return value


def _text(value, where, pattern=None):
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        _fail(where, f"unsafe or invalid value: {value!r}")
    return value


def _choice(value, where, choices):
    if value not in choices:
        _fail(where, f"must be one of: {', '.join(sorted(choices))}")
    return value


def _list(value, where, item, allow_empty=True, unique=True):
    if not isinstance(value, list):
        _fail(where, "must be a list")
    if not allow_empty and not value:
        _fail(where, "must not be empty")

    items = tuple(item(entry, f"{where}[{i}]") for i, entry in enumerate(value))

    if unique and len(set(items)) != len(items):
        _fail(where, "contains duplicates")
    return items


def _texts(pattern, allow_empty=True):
    def parse(value, where):
        return _list(
            value, where,
            lambda entry, w: _text(entry, w, pattern),
            allow_empty=allow_empty,
        )
    return parse


def _version_pattern(value, where):
    _text(value, where, VERSION_PATTERN_RE)
    try:
        re.compile(value)
    except re.error:
        _fail(where, "not a valid regular expression")
    return value


def _firewall_allow(value, where):
    services = _list(value, where, lambda e, w: _choice(e, w, FIREWALL_SERVICES), allow_empty=False)
    if "ssh" not in services:
        _fail(where, "must allow ssh (the firewall is never enabled without it)")
    return services


def _dssa_file(value, where):
    # DSSA writes only files under its own names (decision 3, 4.45).
    if not re.fullmatch(r"dssa-[A-Za-z0-9._-]+", value.rsplit("/", 1)[-1]):
        _fail(where, "a managed file's name must start with dssa-")
    return value


def _port(value, where):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(where, "must be an integer port")
    if not 1 <= value <= 65535:
        _fail(where, "port out of range")
    return value


def _path(value, where):
    _text(value, where, PATH_RE)
    if any(part in (".", "..") for part in value.split("/")):
        _fail(where, "relative path segments are not allowed")
    return value


def _paths(value, where):
    return _list(value, where, _path)


# --- models ---------------------------------------------------------------

@dataclass(frozen=True)
class OsRequirement:
    os_id: str
    versions: Tuple[str, ...]


@dataclass(frozen=True)
class Platform:
    package_manager: str
    os: Tuple[OsRequirement, ...] = ()
    architectures: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Conflicts:
    packages: Tuple[str, ...] = ()
    partial_packages: bool = False
    unmanaged_executables: Tuple[str, ...] = ()
    paths: Tuple[str, ...] = ()
    repository_patterns: Tuple[str, ...] = ()
    systemd_units: Tuple[str, ...] = ()
    tcp_ports: Tuple[int, ...] = ()
    panels: Any = ()


@dataclass(frozen=True)
class Action:
    """One install step or verification check from the allowlist."""

    action_type: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Variant:
    variant_id: str
    support: str
    platform: Platform
    detection_packages: Tuple[str, ...]
    conflicts: Conflicts
    steps: Tuple[Action, ...]
    verification: Tuple[Action, ...]
    created_paths: Tuple[str, ...]
    hooks: Dict[str, str] = field(default_factory=dict)
    # 4.36: packages that must exist for the running kernel (dnf only).
    running_kernel_packages: Tuple[str, ...] = ()
    # Components phase: detected by a state (swap, firewall), not packages.
    detection_state: object = None


@dataclass(frozen=True)
class Dependency:
    component_id: str
    required: bool = True


@dataclass(frozen=True)
class ComponentDefinition:
    component_id: str
    display_name: str
    category: str
    depends_on: Tuple[Dependency, ...]
    variants: Tuple[Variant, ...]
    planning_only: bool = True
    execution_allowed: bool = False


# --- allowlisted step and check parameters --------------------------------

def _tokens(value, where):
    return _texts(TOKEN_RE, allow_empty=False)(value, where)


STEP_TYPES = {
    "package_metadata_refresh": ({}, {}),
    "packages_install": (
        {
            "role": lambda v, w: _choice(v, w, PACKAGE_ROLES),
            "packages": _texts(PACKAGE_RE, allow_empty=False),
        },
        {},
    ),
    "apt_vendor_repository": (
        {
            "repository_id": lambda v, w: _text(v, w, ID_RE),
            "key_url": lambda v, w: _text(v, w, KEY_URL_RE),
            "key_fingerprint": lambda v, w: _text(v, w, FINGERPRINT_RE),
            "key_path": _path,
            "source_path": _path,
            "uris": lambda v, w: _text(v, w, URL_RE),
            "suites": lambda v, w: _texts(SUITE_RE, allow_empty=False)(v, w),
            "components": _tokens,
            "architectures": _tokens,
        },
        {},
    ),
    "dnf_vendor_repository": (
        {
            "repository_id": lambda v, w: _text(v, w, ID_RE),
            "baseurl": lambda v, w: _text(v, w, URL_RE),
            "key_url": lambda v, w: _text(v, w, KEY_URL_RE),
            "key_fingerprint": lambda v, w: _text(v, w, FINGERPRINT_RE),
            "key_path": lambda v, w: _path(_text(v, w, RPM_KEY_PATH_RE), w),
            "repo_path": lambda v, w: _path(_text(v, w, YUM_REPO_PATH_RE), w),
        },
        {},
    ),
    # Reload firewalld when the run installed one of these packages (4.36).
    "firewalld_reload": (
        {"when_packages_added": _texts(PACKAGE_RE, allow_empty=False)}, {},
    ),
    "service_enable": ({"unit": lambda v, w: _text(v, w, UNIT_RE)}, {}),
    "service_start": ({"unit": lambda v, w: _text(v, w, UNIT_RE)}, {}),
    "managed_file": (
        {
            "path": lambda v, w: _dssa_file(_path(v, w), w),
            "template": lambda v, w: _text(v, w, TEMPLATE_RE),
            "mode": lambda v, w: _text(v, w, MODE_RE),
            "owner": lambda v, w: _text(v, w, OWNER_RE),
        },
        {},
    ),
    "hook": ({"name": lambda v, w: _text(v, w, HOOK_RE)}, {}),
    "firewall_enable": ({"allow": lambda v, w: _firewall_allow(v, w)}, {}),
    "swap_file": ({}, {"path": lambda v, w: _choice(v, w, frozenset({"/swapfile.dssa"}))}),
    # 4.45: reload a running unit after its config test passes.
    "service_reload": (
        {"unit": lambda v, w: _text(v, w, UNIT_RE)},
        {"config_test": lambda v, w: _choice(v, w, CONFIG_TEST_COMMANDS)},
    ),
}

CHECK_TYPES = {
    "packages_installed": (
        {"packages": _texts(PACKAGE_RE, allow_empty=False)}, {},
    ),
    "executable": (
        {"name": lambda v, w: _text(v, w, EXECUTABLE_RE)},
        {"version_args": _texts(re.compile(r"^-{1,2}[a-z][a-z-]*$")),
         "version_pattern": _version_pattern},
    ),
    "service_active": ({"unit": lambda v, w: _text(v, w, UNIT_RE)}, {}),
    "service_enabled": ({"unit": lambda v, w: _text(v, w, UNIT_RE)}, {}),
    "tcp_listening": ({"port": _port}, {}),
    "http_local": (
        {
            "port": _port,
            "expect_status": lambda v, w: _text(v, w, STATUS_RE),
        },
        {},
    ),
    "config_test": (
        {"command": lambda v, w: _choice(v, w, CONFIG_TEST_COMMANDS)}, {},
    ),
    "hook": ({"name": lambda v, w: _text(v, w, HOOK_RE)}, {}),
}


def _action(value, where, catalog):
    if not isinstance(value, dict) or "type" not in value:
        _fail(where, "must be an object with a type")

    action_type = value["type"]
    if action_type not in catalog:
        _fail(where, f"unsupported type: {action_type!r}")

    required, optional = catalog[action_type]
    params = {k: v for k, v in value.items() if k != "type"}
    _object(params, where, required=required, optional=optional)

    parsed = {}
    for key, raw in params.items():
        parser = required.get(key) or optional[key]
        parsed[key] = parser(raw, f"{where}.{key}")

    if "version_pattern" in parsed and not parsed.get("version_args"):
        _fail(f"{where}.version_pattern", "needs version_args")

    return Action(action_type=action_type, params=parsed)


# --- definition parsing ---------------------------------------------------

def _platform(value, where):
    _object(
        value, where,
        required=("package_manager",),
        optional=("os", "architectures"),
    )

    manager = _choice(
        value["package_manager"], f"{where}.package_manager",
        PACKAGE_MANAGERS,
    )

    def os_entry(entry, w):
        _object(entry, w, required=("id", "versions"))
        return OsRequirement(
            os_id=_text(entry["id"], f"{w}.id", OS_ID_RE),
            versions=_texts(VERSION_RE, allow_empty=False)(
                entry["versions"], f"{w}.versions"
            ),
        )

    os_requirements = ()
    if "os" in value:
        os_requirements = _list(
            value["os"], f"{where}.os", os_entry, allow_empty=False,
        )

    architectures = ()
    if "architectures" in value:
        architectures = _list(
            value["architectures"], f"{where}.architectures",
            lambda e, w: _choice(e, w, ARCHITECTURES),
            allow_empty=False,
        )

    return Platform(manager, os_requirements, architectures)


def _conflicts(value, where):
    _object(value, where, optional=(
        "packages", "partial_packages", "unmanaged_executables", "paths",
        "repository_patterns", "systemd_units", "tcp_ports", "panels",
    ))

    partial = value.get("partial_packages", False)
    if not isinstance(partial, bool):
        _fail(f"{where}.partial_packages", "must be a boolean")

    panels = value.get("panels", [])
    if panels != "any":
        panels = _texts(ID_RE)(panels, f"{where}.panels")

    return Conflicts(
        packages=_texts(PACKAGE_RE)(
            value.get("packages", []), f"{where}.packages"),
        partial_packages=partial,
        unmanaged_executables=_texts(EXECUTABLE_RE)(
            value.get("unmanaged_executables", []),
            f"{where}.unmanaged_executables"),
        paths=_paths(value.get("paths", []), f"{where}.paths"),
        repository_patterns=_texts(HOSTNAME_RE)(
            value.get("repository_patterns", []),
            f"{where}.repository_patterns"),
        systemd_units=_texts(UNIT_RE)(
            value.get("systemd_units", []), f"{where}.systemd_units"),
        tcp_ports=_list(
            value.get("tcp_ports", []), f"{where}.tcp_ports", _port),
        panels=panels,
    )


def _variant(value, where):
    _object(
        value, where,
        required=(
            "variant_id", "support", "platform", "detection",
            "conflicts", "steps", "verification", "rollback",
        ),
        optional=("hooks", "requirements"),
    )

    platform = _platform(value["platform"], f"{where}.platform")

    running_kernel_packages = ()
    if "requirements" in value:
        requirements = _object(
            value["requirements"], f"{where}.requirements",
            optional=("running_kernel_packages",),
        )
        running_kernel_packages = _texts(PACKAGE_RE)(
            requirements.get("running_kernel_packages", []),
            f"{where}.requirements.running_kernel_packages")
        if platform.package_manager != "dnf":
            _fail(f"{where}.requirements", "requirements need package manager dnf")

    detection = _object(
        value["detection"], f"{where}.detection",
        required=("packages_all",), optional=("state",),
    )
    state = None
    if "state" in detection:
        state = _choice(detection["state"], f"{where}.detection.state", DETECTION_STATES)
        if detection["packages_all"] != []:
            _fail(f"{where}.detection", "a state detection has no packages")
    rollback = _object(
        value["rollback"], f"{where}.rollback",
        required=("created_paths",),
    )

    hooks = {}
    if "hooks" in value:
        _object(value["hooks"], f"{where}.hooks", optional=HOOK_KINDS)
        hooks = {
            kind: _text(name, f"{where}.hooks.{kind}", HOOK_RE)
            for kind, name in value["hooks"].items()
        }

    steps = _list(
        value["steps"], f"{where}.steps",
        lambda e, w: _action(e, w, STEP_TYPES),
        allow_empty=False, unique=False,
    )
    verification = _list(
        value["verification"], f"{where}.verification",
        lambda e, w: _action(e, w, CHECK_TYPES),
        allow_empty=False, unique=False,
    )

    # A hook may only run if the variant declares it.
    declared_hooks = set(hooks.values())
    for label, actions in (("steps", steps), ("verification", verification)):
        for index, action in enumerate(actions):
            if action.action_type == "hook":
                if action.params["name"] not in declared_hooks:
                    _fail(
                        f"{where}.{label}[{index}].name",
                        "hook is not declared in hooks",
                    )

    # Vendor repositories must match the variant's package manager.
    for index, action in enumerate(steps):
        expected = {
            "apt_vendor_repository": "apt",
            "dnf_vendor_repository": "dnf",
        }.get(action.action_type)
        if expected and expected != platform.package_manager:
            _fail(
                f"{where}.steps[{index}]",
                f"{action.action_type} requires package manager {expected}",
            )

    return Variant(
        variant_id=_text(value["variant_id"], f"{where}.variant_id", ID_RE),
        support=_choice(value["support"], f"{where}.support", SUPPORT_LEVELS),
        platform=platform,
        detection_packages=_texts(PACKAGE_RE, allow_empty=state is not None)(
            detection["packages_all"], f"{where}.detection.packages_all"),
        detection_state=state,
        conflicts=_conflicts(value["conflicts"], f"{where}.conflicts"),
        steps=steps,
        verification=verification,
        created_paths=_paths(
            rollback["created_paths"], f"{where}.rollback.created_paths"),
        hooks=hooks,
        running_kernel_packages=running_kernel_packages,
    )


def parse_component_definition(data, expected_id=None):
    """Validate decoded JSON and return an immutable definition."""

    where = expected_id or "definition"

    _object(data, where, required=(
        "schema_version", "id", "display_name", "category",
        "depends_on", "variants",
    ))

    if data["schema_version"] != SCHEMA_VERSION:
        _fail(f"{where}.schema_version", f"must be {SCHEMA_VERSION!r}")

    component_id = _text(data["id"], f"{where}.id", ID_RE)
    if expected_id is not None and component_id != expected_id:
        _fail(f"{where}.id", "must match the definition file name")

    def dependency(entry, w):
        _object(entry, w, required=("id",), optional=("required",))
        required = entry.get("required", True)
        if not isinstance(required, bool):
            _fail(f"{w}.required", "must be a boolean")
        return Dependency(_text(entry["id"], f"{w}.id", ID_RE), required)

    depends_on = _list(
        data["depends_on"], f"{where}.depends_on", dependency, unique=False,
    )
    if any(dep.component_id == component_id for dep in depends_on):
        _fail(f"{where}.depends_on", "a component cannot depend on itself")
    if len({dep.component_id for dep in depends_on}) != len(depends_on):
        _fail(f"{where}.depends_on", "contains duplicate component IDs")

    variants = _list(
        data["variants"], f"{where}.variants", _variant,
        allow_empty=False, unique=False,
    )
    variant_ids = [variant.variant_id for variant in variants]
    if len(set(variant_ids)) != len(variant_ids):
        _fail(f"{where}.variants", "variant IDs must be unique")

    return ComponentDefinition(
        component_id=component_id,
        display_name=_text(data["display_name"], f"{where}.display_name"),
        category=_choice(
            data["category"], f"{where}.category", APPLICATION_CATEGORIES,
        ),
        depends_on=depends_on,
        variants=variants,
    )
