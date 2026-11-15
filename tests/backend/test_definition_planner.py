"""Definition-driven planning decisions (4.30).

These scenarios were first written as parity tests against the legacy
hard-coded planner (backend/linux/planning/components.sh). That planner
was retired in 4.34 with the legacy setup path; the scenarios stay, and
each still records the action the legacy planner produced
(``legacy_action``) so the intended differences remain visible:
- An unsupported platform was legacy REVIEW, now BLOCKED.
- A draft variant that would INSTALL is planned as action REVIEW.
"""

import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "backend/linux/engine/definitions.sh"
DEFINITIONS = ROOT / "definitions/components"

NO_PANELS = '{"panels":[]}'
NO_PORTS = '{"listening_tcp_ports":[]}'

DOCKER_PACKAGES = (
    "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin",
)


def _mocks(installed, executables, units, arch="x86_64", dpkg_arch="amd64"):
    """Shell functions replacing every host probe."""

    return f"""
package_is_installed() {{
    case " {' '.join(shlex.quote(p) for p in installed)} " in
        *" $2 "*) return 0 ;;
        *) return 1 ;;
    esac
}}
uname() {{ [[ "$1" == "-m" ]] && echo {shlex.quote(arch)} || builtin command uname "$@"; }}
dpkg() {{ [[ "$1" == "--print-architecture" ]] && echo {shlex.quote(dpkg_arch)}; }}
_dssa_mock_executable() {{
    case " {' '.join(shlex.quote(e) for e in executables)} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}}
command() {{
    if [[ "$1" == "-v" ]]; then
        [[ "$2" == "--" ]] && shift
        _dssa_mock_executable "$2"
        return
    fi
    builtin command "$@"
}}
systemctl() {{
    if [[ "$1" == "list-unit-files" ]]; then
        local unit
        for unit in {' '.join(shlex.quote(u) for u in units)}; do
            printf '%s enabled enabled\\n' "$unit"
        done
        return 0
    fi
    return 97
}}
"""


class Scenario:
    def __init__(
        self, component, manager="apt", os_id="ubuntu", version="24.04",
        installed=(), executables=(), units=(), paths=(), repo_text="",
        panels=NO_PANELS, ports=NO_PORTS, arch="x86_64", dpkg_arch="amd64",
    ):
        self.component = component
        self.manager = manager
        self.os_id = os_id
        self.version = version
        self.installed = installed
        self.executables = executables
        self.units = units
        self.paths = paths
        self.repo_text = repo_text
        self.panels = panels
        self.ports = ports
        self.arch = arch
        self.dpkg_arch = dpkg_arch


def _run(script):
    result = subprocess.run(
        ["bash", "-c", script],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"planner failed ({result.returncode}): {result.stderr}"
        )
    return json.loads(result.stdout)


def run_new(scenario):
    """Plan with the definition engine against a temporary root."""

    with tempfile.TemporaryDirectory() as root:
        root_path = Path(root)
        (root_path / "etc").mkdir()
        (root_path / "etc/os-release").write_text(
            f'ID={scenario.os_id}\nVERSION_ID="{scenario.version}"\n'
        )
        for path in scenario.paths:
            target = root_path / path.lstrip("/")
            target.mkdir(parents=True, exist_ok=True)
        if scenario.repo_text:
            sources = root_path / "etc/apt/sources.list.d"
            sources.mkdir(parents=True, exist_ok=True)
            (sources / "other.list").write_text(scenario.repo_text)

        definition = (DEFINITIONS / f"{scenario.component}.json").read_text()

        script = f"""
set -euo pipefail
export DSSA_ROOT={shlex.quote(root)}
source {shlex.quote(str(ENGINE))}
{_mocks(scenario.installed, scenario.executables, scenario.units,
        scenario.arch, scenario.dpkg_arch)}
dssa_executable_present() {{ _dssa_mock_executable "$1"; }}
plan_component_from_definition {shlex.quote(definition)} \\
    {shlex.quote(scenario.manager)} \\
    {shlex.quote(scenario.panels)} {shlex.quote(scenario.ports)}
"""
        return _run(script)


def host_lacks(*paths):
    return all(not Path(path).exists() for path in paths)


class DecisionTests(unittest.TestCase):
    """Definition decisions for the scenarios once checked against legacy."""

    def assert_parity(self, scenario, legacy_action, decision, action=None):
        # legacy_action documents what the retired planner decided.
        new = run_new(scenario)

        self.assertEqual(new["decision"], decision, new)
        self.assertEqual(new["action"], action or decision, new)
        self.assertIs(new["planning_only"], True)
        self.assertIs(new["execution_allowed"], False)
        return new

    # Git: supported, matched on package manager only.

    def test_git_installed_is_skipped(self):
        for manager in ("apt", "dnf"):
            with self.subTest(manager=manager):
                self.assert_parity(
                    Scenario("git", manager=manager, installed=("git",)),
                    "SKIP", "SKIP",
                )

    def test_git_unmanaged_executable_requires_review(self):
        self.assert_parity(
            Scenario("git", executables=("git",)), "REVIEW", "REVIEW",
        )

    def test_git_missing_is_installable_on_any_os(self):
        for manager, os_id, version in (
            ("apt", "ubuntu", "22.04"), ("apt", "debian", "13"),
            ("dnf", "almalinux", "10"), ("dnf", "rocky", "10"),
        ):
            with self.subTest(os=os_id):
                self.assert_parity(
                    Scenario("git", manager=manager, os_id=os_id, version=version),
                    "INSTALL", "INSTALL",
                )

    # Docker: supported (4.34), Ubuntu 24.04 x86_64 vendor repository only.

    def test_docker_unsupported_platforms_are_blocked(self):
        for manager, os_id, version in (
            ("apt", "debian", "13"), ("apt", "ubuntu", "22.04"),
            ("apt", "ubuntu", "26.04"), ("dnf", "almalinux", "10"),
        ):
            with self.subTest(os=os_id, version=version):
                new = self.assert_parity(
                    Scenario("docker", manager=manager, os_id=os_id, version=version),
                    "REVIEW", "BLOCKED",
                )
                self.assertIsNone(new["variant_id"])

    def test_docker_wrong_architecture_is_blocked(self):
        self.assert_parity(
            Scenario("docker", arch="aarch64", dpkg_arch="arm64"),
            "REVIEW", "BLOCKED",
        )

    def test_docker_conflicting_package_requires_review(self):
        new = self.assert_parity(
            Scenario("docker", installed=("docker.io",)), "REVIEW", "REVIEW",
        )
        self.assertIn("docker.io", new["reasons"][0])

    def test_docker_conflict_checked_even_when_installed(self):
        self.assert_parity(
            Scenario("docker", installed=DOCKER_PACKAGES + ("runc",)),
            "REVIEW", "REVIEW",
        )

    def test_docker_complete_install_is_skipped(self):
        self.assert_parity(
            Scenario("docker", installed=DOCKER_PACKAGES), "SKIP", "SKIP",
        )

    def test_docker_partial_install_requires_review(self):
        new = self.assert_parity(
            Scenario("docker", installed=("docker-ce",)), "REVIEW", "REVIEW",
        )
        self.assertIn("Partial", new["reasons"][0])

    def test_docker_unmanaged_executable_requires_review(self):
        self.assert_parity(
            Scenario("docker", executables=("dockerd",)), "REVIEW", "REVIEW",
        )

    def test_docker_existing_unit_requires_review(self):
        self.assert_parity(
            Scenario("docker", units=("docker.socket",)), "REVIEW", "REVIEW",
        )

    @unittest.skipUnless(
        host_lacks(
            "/var/lib/docker", "/var/lib/containerd", "/etc/docker",
            "/etc/apt/sources.list.d/docker.sources",
            "/etc/apt/sources.list.d/docker.list",
            "/etc/apt/keyrings/docker.asc", "/etc/apt/keyrings/docker.gpg",
        ) and not shutil.which("docker"),
        "legacy planner reads Docker state paths on the host",
    )
    def test_docker_clean_host_is_install(self):
        self.assert_parity(Scenario("docker"), "INSTALL", "INSTALL")

    # Nginx: draft, matched on package manager only.

    def test_nginx_installed_is_skipped(self):
        self.assert_parity(
            Scenario("nginx", installed=("nginx",)), "SKIP", "SKIP",
        )

    def test_nginx_unmanaged_executable_requires_review(self):
        self.assert_parity(
            Scenario("nginx", executables=("nginx",)), "REVIEW", "REVIEW",
        )

    @unittest.skipUnless(
        host_lacks("/etc/nginx", "/usr/local/nginx"),
        "legacy planner reads Nginx paths on the host",
    )
    def test_nginx_panel_and_port_rules(self):
        cases = (
            (Scenario("nginx", panels='{"panels":["aapanel"]}'), "REVIEW", "REVIEW", None),
            (Scenario("nginx", ports='{"listening_tcp_ports":[443]}'), "REVIEW", "REVIEW", None),
            (Scenario("nginx", manager="dnf", os_id="rocky", version="10"),
             "INSTALL", "INSTALL", "REVIEW"),
        )
        for scenario, legacy_action, decision, action in cases:
            with self.subTest(panels=scenario.panels, ports=scenario.ports):
                self.assert_parity(scenario, legacy_action, decision, action)


class DefinitionOnlyTests(unittest.TestCase):
    """Behaviour the legacy planner cannot express hermetically."""

    def test_docker_existing_state_path_requires_review(self):
        new = run_new(Scenario("docker", paths=("/etc/docker",)))
        self.assertEqual(new["decision"], "REVIEW")
        self.assertIn("/etc/docker", new["reasons"][0])

    def test_docker_existing_repository_reference_requires_review(self):
        new = run_new(Scenario(
            "docker",
            repo_text="deb https://download.docker.com/linux/ubuntu noble stable\n",
        ))
        self.assertEqual(new["decision"], "REVIEW")
        self.assertIn("download.docker.com", new["reasons"][0])

    def test_docker_clean_root_is_install(self):
        # Supported since the owner's lab run (4.34): no draft hold.
        new = run_new(Scenario("docker"))
        self.assertEqual(new["decision"], "INSTALL")
        self.assertEqual(new["action"], "INSTALL")
        self.assertEqual(new["support"], "supported")
        self.assertEqual(new["variant_id"], "ubuntu24-amd64-vendor")
        self.assertNotIn("draft", " ".join(new["reasons"]))

    def test_nginx_paths_panels_and_ports_report_every_reason(self):
        new = run_new(Scenario(
            "nginx",
            paths=("/etc/nginx",),
            panels='{"panels":["aapanel"]}',
            ports='{"listening_tcp_ports":[80,443]}',
        ))
        self.assertEqual(new["decision"], "REVIEW")
        self.assertEqual(len(new["reasons"]), 4, new["reasons"])

    def test_nginx_specific_panel_list_ignores_other_panels(self):
        definition = json.loads((DEFINITIONS / "nginx.json").read_text())
        definition["variants"][0]["conflicts"]["panels"] = ["cyberpanel"]
        script = f"""
set -euo pipefail
source {shlex.quote(str(ENGINE))}
decide_component {shlex.quote(json.dumps(definition))} \\
  '{{"package_manager":"apt","os_id":"ubuntu","os_version":"24.04","arch":"x86_64","dpkg_arch":"amd64"}}' \\
  '{{"packages":{{}},"executables":{{}},"paths":{{}},"repository_patterns":{{}},"units":{{}}}}' \\
  '{{"panels":["aapanel"]}}' '{NO_PORTS}'
"""
        new = _run(script)
        self.assertEqual(new["decision"], "INSTALL")

    def test_invalid_discovery_is_rejected(self):
        definition = (DEFINITIONS / "git.json").read_text()
        for panels, ports in (
            ('{"panels":"none"}', NO_PORTS),
            (NO_PANELS, '{"listening_tcp_ports":[0]}'),
        ):
            with self.subTest(panels=panels, ports=ports):
                result = subprocess.run(
                    ["bash", "-c",
                     f'source {shlex.quote(str(ENGINE))}; '
                     f'plan_component_from_definition "$1" apt "$2" "$3"',
                     "_", definition, panels, ports],
                    text=True, capture_output=True, timeout=15, check=False,
                )
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, "")

    def test_os_release_is_parsed_not_sourced(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "sourced"
            (Path(root) / "etc").mkdir()
            (Path(root) / "etc/os-release").write_text(
                f'ID=ubuntu\nVERSION_ID="24.04"\n'
                f'PRETTY_NAME="$(touch {marker})"\n'
            )
            result = subprocess.run(
                ["bash", "-c",
                 f'export DSSA_ROOT={shlex.quote(root)}; '
                 f'source {shlex.quote(str(ENGINE))}; '
                 'read_os_release_field ID; read_os_release_field VERSION_ID'],
                text=True, capture_output=True, timeout=15, check=False,
            )
            self.assertEqual(result.stdout.split(), ["ubuntu", "24.04"])
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
