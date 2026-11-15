"""Multi-component, dependency-aware planning (Milestone 4.31).

Dependency rules are exercised with test-only definitions
(alpha, beta, gamma, ...) because no real component declares a
dependency yet. Real Git, Nginx and Docker definitions check parity
with the 4.30 single-component planner.
"""

import copy
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from bridge.components.loader import load_component_definitions
from bridge.components.plan import (
    PlanError,
    changed_definitions,
    definition_digest,
    validate_plan_document,
)


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "backend/linux/engine/definitions.sh"
PLANNER = ROOT / "backend/linux/engine/plan.sh"
DEFINITIONS = ROOT / "definitions/components"

HOST_KEY = "SHA256:" + "A" * 43
NO_PANELS = '{"panels":[]}'
NO_PORTS = '{"listening_tcp_ports":[]}'


def make_definition(component_id, depends_on=(), support="supported"):
    """A minimal supported definition: package == executable == ID."""

    definition = json.loads((DEFINITIONS / "git.json").read_text())
    definition["id"] = component_id
    definition["display_name"] = component_id.title()
    definition["depends_on"] = [
        {"id": dep, "required": required} for dep, required in depends_on
    ]
    definition["variants"] = definition["variants"][:1]
    variant = definition["variants"][0]
    variant["support"] = support
    variant["detection"]["packages_all"] = [component_id]
    variant["conflicts"]["unmanaged_executables"] = [component_id]
    variant["steps"][1]["packages"] = [component_id]
    variant["verification"] = [
        {"type": "packages_installed", "packages": [component_id]},
    ]
    return definition


def write_definitions(directory, definitions):
    for definition in definitions:
        (Path(directory) / f"{definition['id']}.json").write_text(
            json.dumps(definition, indent=2) + "\n"
        )


def bash(body, installed=(), executables=()):
    """Run planner code with every host probe mocked."""

    script = f"""
set -euo pipefail
source {shlex.quote(str(ENGINE))}
source {shlex.quote(str(PLANNER))}
package_is_installed() {{
    case " {' '.join(installed)} " in *" $2 "*) return 0 ;; *) return 1 ;; esac
}}
dssa_executable_present() {{
    case " {' '.join(executables)} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}}
dssa_unit_known() {{ return 1; }}
uname() {{ echo x86_64; }}
dpkg() {{ echo amd64; }}
{body}
"""
    return subprocess.run(
        ["bash", "-c", script],
        text=True, capture_output=True, timeout=30, check=False,
    )


def plan(directory, selection, installed=(), executables=(), os_id="ubuntu",
         version="24.04", manager="apt", panels=NO_PANELS, ports=NO_PORTS):
    """Build a request on the 'laptop' side and plan it on the 'target'."""

    with tempfile.TemporaryDirectory() as root:
        (Path(root) / "etc").mkdir()
        (Path(root) / "etc/os-release").write_text(
            f'ID={os_id}\nVERSION_ID="{version}"\n'
        )
        args = " ".join(shlex.quote(c) for c in selection)
        result = bash(
            f"""
export DSSA_ROOT={shlex.quote(root)}
request="$(build_plan_request ubuntu24 {HOST_KEY} {shlex.quote(str(directory))} {args})"
build_definition_plan "$request" {manager} {shlex.quote(panels)} {shlex.quote(ports)}
""",
            installed=installed, executables=executables,
        )
    if result.returncode != 0:
        raise AssertionError(f"plan failed: {result.stderr}")
    document = json.loads(result.stdout)
    validate_plan_document(document)
    return document


def actions(document):
    return [(s["component"], s["action"]) for s in document["steps"]]


class OrderingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_required_dependencies_are_added_and_ordered_first(self):
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
            make_definition("gamma", [("beta", True)]),
        ])
        document = plan(self.dir, ["gamma"])
        self.assertEqual(
            [s["component"] for s in document["steps"]],
            ["alpha", "beta", "gamma"],
        )
        added = {s["component"]: s["added_as_dependency"] for s in document["steps"]}
        self.assertEqual(added, {"alpha": True, "beta": True, "gamma": False})
        self.assertEqual(document["selection"], ["gamma"])

    def test_unrelated_components_keep_selection_order(self):
        write_definitions(self.dir, [make_definition("alpha"), make_definition("beta")])
        self.assertEqual(
            [s["component"] for s in plan(self.dir, ["beta", "alpha"])["steps"]],
            ["beta", "alpha"],
        )
        self.assertEqual(
            [s["component"] for s in plan(self.dir, ["alpha", "beta"])["steps"]],
            ["alpha", "beta"],
        )

    def test_dependency_moves_before_dependent_selected_earlier(self):
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
            make_definition("delta"),
        ])
        document = plan(self.dir, ["delta", "beta", "alpha"])
        self.assertEqual(
            [s["component"] for s in document["steps"]],
            ["delta", "alpha", "beta"],
        )
        self.assertFalse(any(s["added_as_dependency"] for s in document["steps"]))

    def test_optional_dependency_orders_but_is_not_added(self):
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", False)]),
        ])
        self.assertEqual(
            [s["component"] for s in plan(self.dir, ["beta"])["steps"]],
            ["beta"],
        )
        self.assertEqual(
            [s["component"] for s in plan(self.dir, ["beta", "alpha"])["steps"]],
            ["alpha", "beta"],
        )

    def test_shared_dependency_appears_once(self):
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
            make_definition("gamma", [("alpha", True)]),
        ])
        document = plan(self.dir, ["beta", "gamma", "beta"])
        self.assertEqual(
            [s["component"] for s in document["steps"]],
            ["alpha", "beta", "gamma"],
        )
        self.assertEqual(document["selection"], ["beta", "gamma"])

    def test_unknown_component_and_dependency_are_rejected(self):
        write_definitions(self.dir, [
            make_definition("beta", [("missing", True)]),
            make_definition("alpha"),
        ])
        for selection in (["nothere"], ["beta"]):
            with self.subTest(selection=selection):
                result = bash(
                    f'build_plan_request ubuntu24 {HOST_KEY} '
                    f'{shlex.quote(self.dir)} {" ".join(selection)}'
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


class HoldPropagationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
            make_definition("gamma", [("beta", True)]),
            make_definition("delta"),
            make_definition("epsilon", [("alpha", False)]),
        ])

    def test_held_dependency_blocks_dependents_transitively(self):
        # An unmanaged 'alpha' executable makes alpha REVIEW.
        document = plan(
            self.dir, ["gamma", "delta"], executables=("alpha",),
        )
        self.assertEqual(actions(document), [
            ("alpha", "REVIEW"), ("beta", "BLOCKED"),
            ("gamma", "BLOCKED"), ("delta", "INSTALL"),
        ])
        steps = {s["component"]: s for s in document["steps"]}
        self.assertEqual(steps["beta"]["blocked_by"], ["alpha"])
        self.assertEqual(steps["gamma"]["blocked_by"], ["beta"])
        self.assertEqual(steps["beta"]["decision"], "INSTALL")
        self.assertIn("Required dependency not runnable", steps["beta"]["reasons"][-1])
        self.assertEqual(document["runnable"], ["delta"])
        self.assertEqual(
            [h["component"] for h in document["held"]], ["alpha", "beta", "gamma"],
        )

    def test_optional_dependency_does_not_block(self):
        document = plan(self.dir, ["alpha", "epsilon"], executables=("alpha",))
        self.assertEqual(actions(document), [("alpha", "REVIEW"), ("epsilon", "INSTALL")])
        self.assertEqual(document["runnable"], ["epsilon"])

    def test_installed_dependency_lets_dependents_run(self):
        document = plan(self.dir, ["gamma"], installed=("alpha",))
        self.assertEqual(actions(document), [
            ("alpha", "SKIP"), ("beta", "INSTALL"), ("gamma", "INSTALL"),
        ])
        self.assertEqual(document["held"], [])

    def test_draft_dependency_blocks_dependents(self):
        write_definitions(self.dir, [make_definition("alpha", support="draft")])
        document = plan(self.dir, ["beta"])
        self.assertEqual(actions(document), [("alpha", "REVIEW"), ("beta", "BLOCKED")])


class RequestIntegrityTests(unittest.TestCase):
    """The target re-checks everything the laptop sends."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        write_definitions(self.dir, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
        ])
        result = bash(f"build_plan_request ubuntu24 {HOST_KEY} {shlex.quote(self.dir)} beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.request = json.loads(result.stdout)

    def run_target(self, request):
        return bash(
            f'build_definition_plan {shlex.quote(json.dumps(request))} apt '
            f"'{NO_PANELS}' '{NO_PORTS}'"
        )

    def test_request_embeds_closure_with_digests(self):
        self.assertEqual([d["id"] for d in self.request["definitions"]], ["alpha", "beta"])
        for entry in self.request["definitions"]:
            self.assertEqual(
                entry["sha256"],
                definition_digest(Path(self.dir) / f"{entry['id']}.json"),
            )

    def test_tampered_request_is_rejected(self):
        def tamper_content(r):
            definition = json.loads(r["definitions"][0]["content"])
            definition["variants"][0]["support"] = "supported"
            definition["display_name"] = "Changed"
            r["definitions"][0]["content"] = json.dumps(definition)

        cases = {
            "content changed without digest": tamper_content,
            "missing dependency definition": lambda r: r["definitions"].pop(0),
            "extra definition": lambda r: r["definitions"].append({
                "id": "zeta", "sha256": "sha256:" + "0" * 64,
                "content": json.dumps(make_definition("zeta")),
            }),
            "id swapped": lambda r: r["definitions"][0].update(id="beta"),
            "bad host key": lambda r: r["target"].update(host_key_sha256="SHA256:short"),
            "bad target id": lambda r: r["target"].update(target_id="UBUNTU 24"),
            "bad plan id": lambda r: r.update(plan_id="not-a-uuid"),
            "extra field": lambda r: r.update(execute=True),
            "empty selection": lambda r: r.update(selection=[]),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                request = copy.deepcopy(self.request)
                change(request)
                result = self.run_target(request)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_untampered_request_plans(self):
        result = self.run_target(self.request)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        validate_plan_document(document)
        self.assertEqual(changed_definitions(document, self.dir), [])

    def test_changed_local_definition_is_reported(self):
        document = json.loads(self.run_target(self.request).stdout)
        write_definitions(self.dir, [make_definition("alpha", support="draft")])
        self.assertEqual(changed_definitions(document, self.dir), ["alpha"])


class RealDefinitionParityTests(unittest.TestCase):
    """Without dependencies, each step equals the 4.30 single result."""

    def single(self, component, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "etc").mkdir()
            (Path(root) / "etc/os-release").write_text(
                f'ID={kwargs.get("os_id", "ubuntu")}\n'
                f'VERSION_ID="{kwargs.get("version", "24.04")}"\n'
            )
            definition = (DEFINITIONS / f"{component}.json").read_text()
            result = bash(
                f"export DSSA_ROOT={shlex.quote(root)}\n"
                f"plan_component_from_definition {shlex.quote(definition)} "
                f"{kwargs.get('manager', 'apt')} '{NO_PANELS}' '{NO_PORTS}'",
                installed=kwargs.get("installed", ()),
                executables=kwargs.get("executables", ()),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_combined_plan_matches_single_plans(self):
        scenarios = (
            {},
            {"installed": ("git",)},
            {"os_id": "debian", "version": "13"},
            {"manager": "dnf", "os_id": "rocky", "version": "10.2"},
            {"executables": ("nginx",), "installed": ("git",)},
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                document = plan(DEFINITIONS, ["docker", "git", "nginx"], **scenario)
                self.assertEqual(
                    [s["component"] for s in document["steps"]],
                    ["docker", "git", "nginx"],
                )
                for step in document["steps"]:
                    single = self.single(step["component"], **scenario)
                    self.assertEqual(step["decision"], single["decision"])
                    self.assertEqual(step["action"], single["action"])
                    self.assertEqual(step["blocked_by"], [])
                    self.assertFalse(step["added_as_dependency"])

    def test_legacy_eligibility_equals_empty_held(self):
        # Legacy eligible == every action INSTALL or SKIP.
        document = plan(DEFINITIONS, ["git"], installed=("git",))
        self.assertEqual(document["held"], [])
        document = plan(DEFINITIONS, ["git", "nginx"], installed=("git",))
        self.assertEqual(document["runnable"], ["git"])
        self.assertEqual([h["component"] for h in document["held"]], ["nginx"])

    def test_repository_definitions_load_with_digests_matching_backend(self):
        load_component_definitions()
        document = plan(DEFINITIONS, ["git"])
        self.assertEqual(changed_definitions(document), [])


class PlanValidatorTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        write_definitions(cls.tmp.name, [
            make_definition("alpha"),
            make_definition("beta", [("alpha", True)]),
            make_definition("delta"),
        ])
        cls.document = plan(cls.tmp.name, ["beta", "delta"], executables=("alpha",))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_inconsistent_plans_are_rejected(self):
        def reorder(d):
            d["steps"][0], d["steps"][1] = d["steps"][1], d["steps"][0]

        def unblock(d):
            d["steps"][1]["action"] = "INSTALL"
            d["steps"][1]["blocked_by"] = []

        cases = {
            "execution allowed": lambda d: d.update(execution_allowed=True),
            "step execution allowed": lambda d: d["steps"][0].update(execution_allowed=True),
            "dependency ordered after dependent": reorder,
            "runnable list altered": lambda d: d["runnable"].append("alpha"),
            "held list emptied": lambda d: d.update(held=[]),
            "blocked step unblocked": unblock,
            "added flag wrong": lambda d: d["steps"][0].update(added_as_dependency=False),
            "definition digest missing": lambda d: d["definitions"].pop("alpha"),
            "bad digest": lambda d: d["definitions"].update(alpha="sha256:xyz"),
            "unknown field": lambda d: d.update(approved=True),
            "draft planned as install": lambda d: d["steps"][2].update(support="draft"),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                document = copy.deepcopy(self.document)
                change(document)
                with self.assertRaises(PlanError):
                    validate_plan_document(document)

    def test_valid_plan_is_accepted(self):
        validate_plan_document(copy.deepcopy(self.document))


if __name__ == "__main__":
    unittest.main()
