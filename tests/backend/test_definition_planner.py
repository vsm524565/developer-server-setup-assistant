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
        kernel=None,
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
        # 4.36: running-kernel package state; default: present for the
        # running kernel, so no host rpm/dnf is ever asked.
        self.kernel = kernel or {"release": "6.12.0-211.el10.x86_64", "installed": True,
                                 "latest": None, "checked": True}


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
            if scenario.manager == "dnf":
                sources, name = root_path / "etc/yum.repos.d", "other.repo"
            else:
                sources, name = root_path / "etc/apt/sources.list.d", "other.list"
            sources.mkdir(parents=True, exist_ok=True)
            (sources / name).write_text(scenario.repo_text)

        definition = (DEFINITIONS / f"{scenario.component}.json").read_text()

        script = f"""
set -euo pipefail
export DSSA_ROOT={shlex.quote(root)}
source {shlex.quote(str(ENGINE))}
{_mocks(scenario.installed, scenario.executables, scenario.units,
        scenario.arch, scenario.dpkg_arch)}
dssa_executable_present() {{ _dssa_mock_executable "$1"; }}
dssa_running_kernel_package_state() {{ echo {shlex.quote(json.dumps(scenario.kernel))}; }}
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
        # 4.36 added Debian 13, Ubuntu 22.04/26.04 and EL10; these still
        # have no variant.
        for manager, os_id, version in (
            ("apt", "debian", "12"), ("apt", "ubuntu", "20.04"),
            ("dnf", "almalinux", "9.4"), ("dnf", "fedora", "41"),
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

    def test_docker_new_platforms_select_their_own_variant(self):
        # 4.36: one variant per OS release; all supported after the owner's
        # lab run on 2026-11-18.
        for manager, os_id, version, variant_id in (
            ("apt", "ubuntu", "22.04", "ubuntu22-amd64-vendor"),
            ("apt", "ubuntu", "26.04", "ubuntu26-amd64-vendor"),
            ("apt", "debian", "13", "debian13-amd64-vendor"),
            ("dnf", "almalinux", "10.2", "el10-x86_64-vendor"),
            ("dnf", "rocky", "10.2", "el10-x86_64-vendor"),
            ("dnf", "rocky", "10", "el10-x86_64-vendor"),
        ):
            with self.subTest(os=os_id, version=version):
                new = run_new(Scenario("docker", manager=manager, os_id=os_id, version=version))
                self.assertEqual(new["variant_id"], variant_id)
                self.assertEqual((new["decision"], new["action"]), ("INSTALL", "INSTALL"))
                self.assertEqual(new["support"], "supported")
                self.assertNotIn("draft", " ".join(new["reasons"]))

    def test_docker_ubuntu24_stays_supported(self):
        new = run_new(Scenario("docker"))
        self.assertEqual((new["variant_id"], new["support"], new["action"]),
                         ("ubuntu24-amd64-vendor", "supported", "INSTALL"))

    def test_docker_el10_podman_requires_review(self):
        new = run_new(Scenario("docker", manager="dnf", os_id="rocky", version="10.2",
                               installed=("podman",)))
        self.assertEqual(new["decision"], "REVIEW")
        self.assertIn("podman", " ".join(new["reasons"]))

    def test_docker_el10_existing_repository_requires_review(self):
        new = run_new(Scenario("docker", manager="dnf", os_id="almalinux", version="10.2",
                               repo_text="baseurl=https://download.docker.com/linux/centos/10/x86_64/stable\n"))
        self.assertEqual(new["decision"], "REVIEW")
        self.assertIn("download.docker.com", " ".join(new["reasons"]))

    def test_docker_el_major_version_does_not_match_other_majors(self):
        new = run_new(Scenario("docker", manager="dnf", os_id="rocky", version="100.1"))
        self.assertEqual(new["decision"], "BLOCKED")

    # --- 4.36: modules for the running kernel (EL) -----------------------

    def el_docker(self, kernel, installed=()):
        return run_new(Scenario("docker", manager="dnf", os_id="almalinux", version="10.2",
                                installed=installed, kernel=kernel))

    def test_docker_el_blocked_when_kernel_is_behind_the_repository(self):
        new = self.el_docker({"release": "6.12.0-211.el10.x86_64", "installed": False,
                              "latest": "6.12.0-264.el10.x86_64", "checked": True})
        self.assertEqual((new["decision"], new["action"]), ("BLOCKED", "BLOCKED"))
        reason = " ".join(new["reasons"])
        self.assertIn("6.12.0-211.el10.x86_64", reason)
        self.assertIn("6.12.0-264.el10.x86_64", reason)
        self.assertIn("reboot", reason)

    def test_docker_el_installs_when_repository_matches_running_kernel(self):
        new = self.el_docker({"release": "6.12.0-264.el10.x86_64", "installed": False,
                              "latest": "6.12.0-264.el10.x86_64", "checked": True})
        self.assertEqual(new["decision"], "INSTALL")

    def test_docker_el_blocked_when_kernel_check_fails_or_package_missing(self):
        for kernel, text in (
            ({"release": "6.12.0-211.el10.x86_64", "installed": False,
              "latest": None, "checked": False}, "Could not check"),
            ({"release": "6.12.0-211.el10.x86_64", "installed": False,
              "latest": None, "checked": True}, "not available"),
        ):
            with self.subTest(text=text):
                new = self.el_docker(kernel)
                self.assertEqual(new["decision"], "BLOCKED")
                self.assertIn(text, " ".join(new["reasons"]))

    def test_docker_el_installed_is_verified_whatever_the_kernel(self):
        new = self.el_docker({"release": "x", "installed": False, "latest": "y",
                              "checked": True}, installed=DOCKER_PACKAGES)
        self.assertEqual(new["decision"], "SKIP")

    def test_running_kernel_probe(self):
        # The real probe, with rpm/dnf/uname replaced by shell functions.
        def probe(rpm_out, dnf_out, dnf_rc=0):
            script = f"""
source {shlex.quote(str(ENGINE))}
uname() {{ [[ "$1" == "-r" ]] && echo 6.12.0-211.el10.x86_64 || echo x86_64; }}
rpm() {{ printf '%s' {shlex.quote(rpm_out)}; }}
dnf() {{ printf '%s' {shlex.quote(dnf_out)}; return {dnf_rc}; }}
timeout() {{ shift; "$@"; }}
dssa_running_kernel_package_state kernel-modules-extra
"""
            return _run(script)
        self.assertEqual(probe("6.12.0-264.el10.x86_64\n6.12.0-211.el10.x86_64\n", ""),
                         {"release": "6.12.0-211.el10.x86_64", "installed": True,
                          "latest": None, "checked": True})
        self.assertEqual(probe("", "6.12.0-264.el10.x86_64\n"),
                         {"release": "6.12.0-211.el10.x86_64", "installed": False,
                          "latest": "6.12.0-264.el10.x86_64", "checked": True})
        self.assertEqual(probe("", "", dnf_rc=1)["checked"], False)

    def test_compose_plans_on_both_package_managers(self):
        for manager in ("apt", "dnf"):
            with self.subTest(manager=manager):
                new = run_new(Scenario("docker_compose", manager=manager,
                                       os_id="rocky" if manager == "dnf" else "ubuntu",
                                       version="10.2" if manager == "dnf" else "24.04"))
                self.assertEqual(new["variant_id"], f"{manager}-docker-plugin")
                # Supported after the owner's lab run (2026-11-18).
                self.assertEqual((new["decision"], new["action"], new["support"]),
                                 ("INSTALL", "INSTALL", "supported"))

    def test_compose_already_installed_is_skipped(self):
        new = run_new(Scenario("docker_compose", installed=("docker-compose-plugin",)))
        self.assertEqual(new["decision"], "SKIP")

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
