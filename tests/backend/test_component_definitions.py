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
        expected = [
            "apache",
            "caddy",
            "certbot",
            "chrony",
            "clamav",
            "composer",
            "docker",
            "docker_compose",
            "elasticsearch",
            "fail2ban",
            "firewall",
            "git",
            "haproxy",
            "java",
            "mariadb",
            "memcached",
            "mongodb",
            "mysql",
            "nginx",
            "nodejs",
            "openlitespeed",
            "opensearch",
            "php",
            "php82",
            "php83",
            "php84",
            "postfix",
            "postgresql",
            "python",
            "redis",
            "swap",
            "varnish",
        ]

        self.assertEqual(sorted(self.definitions), expected)

    def test_git_and_docker_are_supported_nginx_is_draft(self):
        # Docker became supported after the owner's lab run (4.34).
        support = {
            definition.component_id: {v.support for v in definition.variants}
            for definition in self.definitions.values()
        }
        self.assertEqual(support["git"], {"supported"})
        # Docker: ubuntu24 since 4.34; the 4.36 variants and Compose after
        # the owner's lab run on all six VMs (2026-11-18).
        self.assertEqual(support["docker"], {"supported"})
        self.assertEqual(support["docker_compose"], {"supported"})
        self.assertEqual(support["nginx"], {"draft"})
        for component in ("apache", "certbot", "chrony", "fail2ban", "firewall", "mariadb", "mysql",
                          "nodejs", "php", "postgresql", "python", "redis", "swap"):
            self.assertEqual(support[component], {"draft"}, component)

    def test_git_and_nginx_match_on_package_manager_only(self):
        for component_id in ("git", "nginx"):
            for variant in self.definitions[component_id].variants:
                self.assertEqual(variant.platform.os, ())
                self.assertEqual(variant.platform.architectures, ())

    def test_docker_declares_its_os_requirement(self):
        # One variant per OS release, each with literal repository values (4.36 A).
        expected = {
            "ubuntu22-amd64-vendor": ("apt", [("ubuntu", ("22.04",))]),
            "ubuntu24-amd64-vendor": ("apt", [("ubuntu", ("24.04",))]),
            "ubuntu26-amd64-vendor": ("apt", [("ubuntu", ("26.04",))]),
            "debian13-amd64-vendor": ("apt", [("debian", ("13",))]),
            "el10-x86_64-vendor": ("dnf", [("almalinux", ("10",)), ("rocky", ("10",))]),
        }
        variants = {v.variant_id: v for v in self.definitions["docker"].variants}
        self.assertEqual(set(variants), set(expected))
        for variant_id, (manager, os_list) in expected.items():
            platform = variants[variant_id].platform
            self.assertEqual(platform.package_manager, manager, variant_id)
            self.assertEqual([(o.os_id, o.versions) for o in platform.os], os_list, variant_id)
            self.assertEqual(platform.architectures, ("x86_64",), variant_id)

    def test_docker_el_requires_modules_for_the_running_kernel(self):
        variants = {v.variant_id: v for v in self.definitions["docker"].variants}
        self.assertEqual(variants["el10-x86_64-vendor"].running_kernel_packages,
                         ("kernel-modules-extra",))
        for variant_id, variant in variants.items():
            if variant_id != "el10-x86_64-vendor":
                self.assertEqual(variant.running_kernel_packages, (), variant_id)

    def test_docker_reloads_firewalld_before_starting_on_el(self):
        # firewalld must see iptables-nft before dockerd asks it for rules,
        # and Docker's saved firewalld zone is removed on rollback (4.36).
        for variant in self.definitions["docker"].variants:
            types = [s.action_type for s in variant.steps]
            self.assertIn("/etc/firewalld/zones/docker.xml", variant.created_paths, variant.variant_id)
            self.assertIn("/etc/firewalld/policies/docker-forwarding.xml", variant.created_paths,
                          variant.variant_id)
            if variant.variant_id == "el10-x86_64-vendor":
                reload = types.index("firewalld_reload")
                self.assertEqual(variant.steps[reload].params,
                                 {"when_packages_added": ("iptables-nft",)})
                self.assertLess(types.index("packages_install"), reload)
                self.assertLess(reload, types.index("service_enable"))
            else:
                self.assertNotIn("firewalld_reload", types, variant.variant_id)

    def test_compose_requires_docker(self):
        compose = self.definitions["docker_compose"]
        self.assertEqual([(d.component_id, d.required) for d in compose.depends_on],
                         [("docker", True)])

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
            "null dependency flag":
                lambda d: d.update(depends_on=[{"id": "git", "required": None}]),
            "self dependency":
                lambda d: d.update(depends_on=[{"id": "docker"}]),
            "duplicate variant IDs":
                lambda d: d["variants"].append(copy.deepcopy(docker_variant(d))),
            "empty steps":
                lambda d: docker_variant(d).update(steps=[]),
            "kernel requirement on an apt variant":
                lambda d: docker_variant(d).update(
                    requirements={"running_kernel_packages": ["kernel-modules-extra"]}),
            "unknown requirement":
                lambda d: d["variants"][-1].update(requirements={"reboot": True}),
            "firewalld reload without packages":
                lambda d: d["variants"][-1]["steps"].insert(5, {"type": "firewalld_reload"}),
            "firewalld reload with an unsafe package":
                lambda d: d["variants"][-1]["steps"].insert(
                    5, {"type": "firewalld_reload", "when_packages_added": ["x;reboot"]}),
            "firewalld reload with an extra parameter":
                lambda d: d["variants"][-1]["steps"].insert(
                    5, {"type": "firewalld_reload", "when_packages_added": ["iptables-nft"],
                        "zone": "docker"}),
            "unsafe kernel package name":
                lambda d: d["variants"][-1].update(
                    requirements={"running_kernel_packages": ["kernel;reboot"]}),
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


class ManagedFileContractTests(unittest.TestCase):
    """managed_file paths use DSSA's own names; service_reload (4.45)."""

    def with_step(self, step):
        definition = load_repo_definition("git")
        definition["variants"][0]["steps"].append(step)
        return definition

    def test_dssa_named_file_and_reload_are_valid(self):
        for step in ({"type": "managed_file", "path": "/etc/nginx/conf.d/dssa-default.conf",
                      "template": "nginx/default.conf", "mode": "0644", "owner": "root:root"},
                     {"type": "service_reload", "unit": "nginx.service", "config_test": "nginx -t"}):
            with self.subTest(step["type"]):
                definition = self.with_step(step)
                parse_component_definition(definition)
                self.assertTrue(jq_accepts(definition))

    def test_other_names_and_commands_are_refused(self):
        for step in ({"type": "managed_file", "path": "/etc/nginx/nginx.conf",
                      "template": "nginx/default.conf", "mode": "0644", "owner": "root:root"},
                     {"type": "service_reload", "unit": "nginx.service", "config_test": "rm -rf /"}):
            with self.subTest(step["type"]):
                definition = self.with_step(step)
                with self.assertRaises(DefinitionError):
                    parse_component_definition(definition)
                self.assertFalse(jq_accepts(definition))


class VersionPatternContractTests(unittest.TestCase):
    """version_pattern on executable checks, in Python and bash (4.39)."""

    def with_pattern(self, pattern, args=("--version",)):
        definition = load_repo_definition("git")
        for variant in definition["variants"]:
            for check in variant["verification"]:
                if check["type"] == "executable":
                    check["version_pattern"] = pattern
                    if args is None:
                        check.pop("version_args", None)
        return definition

    def test_repository_patterns_match_real_output(self):
        samples = {
            "git": ["git version 2.43.0", "git version 2.53.0"],
            "docker": ["Docker version 29.1.3, build 29.1.3-0ubuntu4.1",
                       "Docker version 29.10.0, build 1234567"],
        }
        for component, lines in samples.items():
            for variant in load_repo_definition(component)["variants"]:
                check = next(c for c in variant["verification"] if c["type"] == "executable")
                for line in lines:
                    self.assertRegex(line, check["version_pattern"])
                self.assertNotRegex("usage: %s [options]" % component, check["version_pattern"])

    def test_valid_pattern_is_accepted_by_both_validators(self):
        definition = self.with_pattern(r"^git version [0-9]+\.")
        parse_component_definition(definition)
        self.assertTrue(jq_accepts(definition))

    def test_invalid_patterns_are_refused_by_both_validators(self):
        cases = {
            "unbalanced": ("(", ("--version",)),
            "unsafe characters": ("a;b", ("--version",)),
            "backtick": ("x`y", ("--version",)),
            "empty": ("", ("--version",)),
            "too long": ("a" * 201, ("--version",)),
            "without version_args": ("^git", None),
        }
        for name, (pattern, args) in cases.items():
            with self.subTest(name):
                definition = self.with_pattern(pattern, args)
                with self.assertRaises(DefinitionError):
                    parse_component_definition(definition)
                self.assertFalse(jq_accepts(definition))


if __name__ == "__main__":
    unittest.main()
