"""Shared component definition loading and validation (Milestone 4.30).

Every rejection case is checked by both the Python validator and the
Bash/jq validator, so the two independent checks cannot drift apart.
"""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from bridge.applications.catalog import build_application_catalog
from bridge.components.contracts import (
    DefinitionError,
    parse_component_definition,
)
from bridge.components.loader import (
    DEFAULT_DEFINITIONS_DIR,
    load_component_definition,
    load_component_definitions,
)


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "backend/linux/engine/definitions.sh"


def jq_accepts(definition):
    """Run the backend validator on one decoded definition."""

    result = subprocess.run(
        [
            "bash", "-c",
            'source "$1"; validate_component_definition "$(cat)"',
            "_", str(ENGINE),
        ],
        input=json.dumps(definition),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def load_repo_definition(component_id):
    path = DEFAULT_DEFINITIONS_DIR / f"{component_id}.json"
    return json.loads(path.read_text())


class RepositoryDefinitionTests(unittest.TestCase):
    """The committed definitions load, validate and match the catalog."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = build_application_catalog()
        cls.definitions = load_component_definitions(catalog=cls.catalog)

    def test_expected_components_are_defined(self):
        self.assertEqual(
            sorted(self.definitions), ["docker", "git", "nginx"],
        )

    def test_only_git_is_supported(self):
        support = {
            definition.component_id: {v.support for v in definition.variants}
            for definition in self.definitions.values()
        }
        self.assertEqual(support["git"], {"supported"})
        self.assertEqual(support["docker"], {"draft"})
        self.assertEqual(support["nginx"], {"draft"})

    def test_git_and_nginx_match_on_package_manager_only(self):
        for component_id in ("git", "nginx"):
            for variant in self.definitions[component_id].variants:
                self.assertEqual(variant.platform.os, ())
                self.assertEqual(variant.platform.architectures, ())

    def test_docker_declares_its_os_requirement(self):
        platform = self.definitions["docker"].variants[0].platform
        self.assertEqual(platform.package_manager, "apt")
        self.assertEqual(platform.os[0].os_id, "ubuntu")
        self.assertEqual(platform.os[0].versions, ("24.04",))
        self.assertEqual(platform.architectures, ("x86_64",))

    def test_definitions_never_authorize_execution(self):
        for definition in self.definitions.values():
            self.assertTrue(definition.planning_only)
            self.assertFalse(definition.execution_allowed)

    def test_backend_validator_accepts_repository_definitions(self):
        for component_id in self.definitions:
            with self.subTest(component=component_id):
                self.assertTrue(jq_accepts(load_repo_definition(component_id)))


class RejectionTests(unittest.TestCase):
    """Unsafe or malformed definitions are rejected by both validators."""

    def mutate(self, change, component_id="docker"):
        definition = copy.deepcopy(load_repo_definition(component_id))
        change(definition)
        return definition

    def assert_rejected(self, definition, component_id="docker"):
        with self.assertRaises(DefinitionError):
            parse_component_definition(definition, expected_id=component_id)
        self.assertFalse(
            jq_accepts(definition),
            "backend validator accepted a definition Python rejected",
        )

    def test_rejection_cases(self):
        docker_variant = lambda d: d["variants"][0]
        cases = {
            "unknown top-level field":
                lambda d: d.update(extra=True),
            "wrong schema version":
                lambda d: d.update(schema_version="2.0"),
            "unknown variant field":
                lambda d: docker_variant(d).update(script="rm -rf /"),
            "shell metacharacters in package":
                lambda d: docker_variant(d)["detection"].update(
                    packages_all=["docker-ce;reboot"]),
            "plain http key URL":
                lambda d: docker_variant(d)["steps"][2].update(
                    key_url="http://download.docker.com/linux/ubuntu/gpg"),
            "short fingerprint":
                lambda d: docker_variant(d)["steps"][2].update(
                    key_fingerprint="9DC85822"),
            "relative path segment":
                lambda d: docker_variant(d)["conflicts"].update(
                    paths=["/etc/../root"]),
            "relative path":
                lambda d: docker_variant(d)["rollback"].update(
                    created_paths=["var/lib/docker"]),
            "unknown step type":
                lambda d: docker_variant(d)["steps"].append(
                    {"type": "shell", "command": "curl | sh"}),
            "unknown step parameter":
                lambda d: docker_variant(d)["steps"][0].update(force=True),
            "undeclared hook":
                lambda d: docker_variant(d).update(hooks={}),
            "vendor repository with wrong manager":
                lambda d: docker_variant(d)["platform"].update(
                    package_manager="dnf", os=[{"id": "almalinux", "versions": ["10"]}]),
            "unsupported package manager":
                lambda d: docker_variant(d)["platform"].update(
                    package_manager="zypper"),
            "unsupported architecture":
                lambda d: docker_variant(d)["platform"].update(
                    architectures=["riscv64"]),
            "invalid unit name":
                lambda d: docker_variant(d)["conflicts"].update(
                    systemd_units=["docker"]),
            "boolean used as port":
                lambda d: docker_variant(d)["conflicts"].update(
                    tcp_ports=[True]),
            "port out of range":
                lambda d: docker_variant(d)["conflicts"].update(
                    tcp_ports=[70000]),
            "unknown support level":
                lambda d: docker_variant(d).update(support="production"),
            "config test outside allowlist":
                lambda d: docker_variant(d)["verification"].append(
                    {"type": "config_test", "command": "bash -c id"}),
            "self dependency":
                lambda d: d.update(depends_on=[{"id": "docker"}]),
            "duplicate variant IDs":
                lambda d: d["variants"].append(copy.deepcopy(docker_variant(d))),
            "empty steps":
                lambda d: docker_variant(d).update(steps=[]),
        }

        for name, change in cases.items():
            with self.subTest(case=name):
                self.assert_rejected(self.mutate(change))

    def test_id_must_match_file_name(self):
        with self.assertRaises(DefinitionError):
            parse_component_definition(
                load_repo_definition("git"), expected_id="docker",
            )


class LoaderTests(unittest.TestCase):
    """Directory loading, catalog cross-checks and dependency graphs."""

    def write(self, directory, component_id, change=None, source="git"):
        definition = copy.deepcopy(load_repo_definition(source))
        definition["id"] = component_id
        if change:
            change(definition)
        path = Path(directory) / f"{component_id}.json"
        path.write_text(json.dumps(definition))
        return path

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "git.json"
            text = (DEFAULT_DEFINITIONS_DIR / "git.json").read_text()
            path.write_text(text.replace(
                '"display_name": "Git"',
                '"display_name": "Git", "display_name": "Other"',
                1,
            ))
            with self.assertRaises(DefinitionError):
                load_component_definition(path)

    def test_symlinked_definition_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "git.json"
            link.symlink_to(DEFAULT_DEFINITIONS_DIR / "git.json")
            with self.assertRaises(DefinitionError):
                load_component_definition(link)

    def test_unknown_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, "git", lambda d: d.update(
                depends_on=[{"id": "missing"}]))
            with self.assertRaises(DefinitionError):
                load_component_definitions(directory)

    def test_dependency_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, "alpha", lambda d: d.update(
                depends_on=[{"id": "beta"}]))
            self.write(directory, "beta", lambda d: d.update(
                depends_on=[{"id": "alpha"}]))
            with self.assertRaisesRegex(DefinitionError, "cycle"):
                load_component_definitions(directory)

    def test_valid_dependency_chain_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, "alpha")
            self.write(directory, "beta", lambda d: d.update(
                depends_on=[{"id": "alpha", "required": True}]))
            loaded = load_component_definitions(directory)
            self.assertEqual(sorted(loaded), ["alpha", "beta"])

    def test_component_outside_catalog_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, "unlisted")
            with self.assertRaisesRegex(DefinitionError, "catalog"):
                load_component_definitions(
                    directory, catalog=build_application_catalog(),
                )

    def test_category_must_match_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, "git", lambda d: d.update(category="runtime"))
            with self.assertRaisesRegex(DefinitionError, "category"):
                load_component_definitions(
                    directory, catalog=build_application_catalog(),
                )


if __name__ == "__main__":
    unittest.main()
