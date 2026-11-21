"""Control panels (4.48): fresh-server checks, the vendor installer, the
owner split, and no undo where the vendor has none (docs/design/panels.md).
The vendor installers are mocked scripts served by the mocked curl.

The ten cases, one test each:
  1. every panel plans INSTALL (action REVIEW: draft) on a supported, fresh
     server, with details {"panel", "reversible": false} and the
     "Not reversible" reason; hestia/cloudpanel have no dnf variant (BLOCKED)
  2. fresh-server blocks: a web/db/mail package installed; a non-empty
     install record; another panel's folder present; too little RAM; too
     little swap (directadmin, plesk); no FQDN (hestia, cpanel)
  3. aapanel installs through the mocked installer: panel folder created,
     verify hook passes, record entry written, installer SHA-256 journaled,
     "S3cretFromVendor" masked as "[hidden by DSSA]" in the run output
  4. hestia: {fqdn}/{password} filled in (installer_args.txt), the password
     file is 0600 and the password is in neither the output nor the journal
  5. a failing installer without an uninstaller: rollback_incomplete, exit 4,
     "reinstall the OS or restore the VM snapshot"
  6. a test-only panel definition with uninstall_url: the uninstaller runs
     on failure (failed_rolled_back, exit 3)
  7. owner split: with a panel present, nginx plans REVIEW "Managed by
     <panel>", swap and git still plan INSTALL
  8. plan --remove of an installed panel is BLOCKED
  9. risky_steps(plan) asks for the panel's name with the snapshot warning
 10. a failed installer download (fail.json "installer_download") changes
     nothing
"""

import json
import shlex
import unittest

from tests.backend.test_components import ComponentServer, load
from tests.backend.test_journal_rollback import PLAN_A, ROOT
from bridge.components.risk import risky_steps

PANELS = ("aapanel", "cyberpanel", "hestia", "cloudpanel", "cpanel", "plesk", "directadmin")
FOLDER = {"aapanel": "www/server/panel", "cyberpanel": "usr/local/CyberCP", "hestia": "usr/local/hestia",
          "cloudpanel": "home/clp/htdocs/app", "cpanel": "usr/local/cpanel", "plesk": "usr/local/psa",
          "directadmin": "usr/local/directadmin"}
PORT = {"hestia": 8083, "cloudpanel": 8443, "cpanel": 2087, "directadmin": 2222}


def installer(folder, exit_code=0, port=None):
    """A stand-in vendor installer: records its arguments, makes the
    panel's folder, prints a password like the real ones do."""
    return ("#!/bin/bash\n"
            'printf \'%s\\n\' "$@" > "$MOCK_STATE/installer_args.txt"\n'
            'env > "$MOCK_STATE/installer_env.txt"\n'
            'echo "username: admin"\n'
            'echo "password: S3cretFromVendor"\n'
            'echo "Admin password will be set to S3cretFromVendor"\n'
            f'mkdir -p "$DSSA_ROOT/{folder}"\n'
            + (f'python3 - <<PY\nimport json,os\np=os.environ["MOCK_STATE"]+"/units.json"\n'
               f'u=json.load(open(p)) if os.path.exists(p) else {{}}\n'
               f'u["panel.service"]={{"file_state":"enabled","active":"active"}}\njson.dump(u,open(p,"w"))\n'
               f'json.dump({{"panel.service":[{port}]}},open(os.environ["MOCK_STATE"]+"/unit_ports.json","w"))\nPY\n'
               if port else "")
            + f"exit {exit_code}\n")


class PanelServer(ComponentServer):

    def setUp(self):
        super().setUp()
        for tool in ("hostname", "df"):
            (self.tools / tool).symlink_to(ROOT / "tests/backend/fixtures/mock_system.py")
        (self.root / "root").mkdir()
        (self.root / "proc/meminfo").write_text("MemTotal:  8388608 kB\nSwapTotal: 8388608 kB\n")
        self.write("hostname.json", "server1.example.com")

    def bash(self, body, with_root=True):
        # Panel discovery and the install record are needed by the
        # fresh-server facts. discover_panels checks real paths, so tests
        # that need a panel present create its folder under DSSA_ROOT and
        # override discover_panels in the body (see TODO below).
        panel = ROOT / "backend/linux/discovery/panel.sh"
        record = ROOT / "backend/linux/engine/record.sh"
        return super().bash(f"source {shlex.quote(str(panel))}\nsource {shlex.quote(str(record))}\n" + body,
                            with_root)

    def serve(self, panel, exit_code=0):
        url = next(st["url"] for v in load(panel)["variants"] for st in v["steps"] if st["type"] == "vendor_installer")
        self.write("installers.json", {url: installer(FOLDER[panel], exit_code, PORT.get(panel))})


    def install_definition(self, definition, manager="apt"):
        """apply_component for an in-memory definition (allow_draft)."""
        variant = next(v["variant_id"] for v in definition["variants"]
                       if v["platform"]["package_manager"] == manager)
        result = self.bash(f"""
begin_transaction {PLAN_A} {manager} '[]' || exit 90
r="$(apply_component {PLAN_A} {manager} {shlex.quote(json.dumps(definition))} {variant} true)"; rc=$?
end_transaction {PLAN_A} "[$r]" || exit 91
printf '%s\\n' "$r"; exit $rc""")
        self.assertNotIn(result.returncode, (90, 91), result.stderr)
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1]), result

    def plan_with(self, component, manager="apt", panels=()):
        result = self.bash(f"plan_component_from_definition {shlex.quote(json.dumps(load(component)))} {manager} "
                           f"{shlex.quote(json.dumps({'panels': list(panels)}))} "
                           f"{shlex.quote(json.dumps({'listening_tcp_ports': []}))}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def journal(self):
        path = self.root / "var/lib/dssa/transactions" / PLAN_A / "journal.jsonl"
        return path.read_text() if path.exists() else ""


class PlanTests(PanelServer):

    def test_1_every_panel_plans_install_on_a_fresh_supported_server(self):
        for manager, os_id, version in (("apt", "ubuntu", "24.04"), ("dnf", "almalinux", "10.0")):
            self.os(os_id, version)
            for panel in PANELS:
                with self.subTest(manager=manager, panel=panel):
                    entry = self.plan_with(panel, manager)
                    if manager == "dnf" and panel in ("hestia", "cloudpanel"):
                        self.assertEqual(entry["decision"], "BLOCKED", "Debian/Ubuntu only")
                        continue
                    self.assertEqual(entry["decision"], "INSTALL", entry["reasons"])
                    self.assertEqual(entry["action"], "REVIEW", "draft until the lab run")
                    self.assertEqual(entry["details"], {"panel": panel, "reversible": False})
                    self.assertTrue(any("Not reversible" in r for r in entry["reasons"]))

    def test_2_fresh_server_blocks(self):
        def reasons(panel):
            entry = self.plan_with(panel)
            self.assertEqual(entry["decision"], "BLOCKED", entry["reasons"])
            return " ".join(entry["reasons"])

        self.write("packages.json", {"base-files": "1.0", "nginx": "1.24"})
        self.assertIn("Already installed, a panel needs a fresh server: nginx.", reasons("aapanel"))
        self.write("packages.json", {"base-files": "1.0"})

        (self.root / "usr/local/cpanel").mkdir(parents=True)
        self.assertIn("Another control panel is installed: cpanel.", reasons("aapanel"))
        (self.root / "usr/local/cpanel").rmdir()

        state = self.root / "var/lib/dssa"
        state.mkdir(parents=True)
        state.chmod(0o711)
        (state / "installed.json").write_text('{"schema_version": "1.0", "components": {"git": {}}}')
        (state / "installed.json").chmod(0o644)
        self.assertIn("install record not empty", reasons("plesk"))
        (state / "installed.json").unlink()

        (self.root / "proc/meminfo").write_text("MemTotal:  262144 kB\nSwapTotal: 0 kB\n")
        self.assertIn("Needs at least 2048 MiB of RAM (has 256).", reasons("cloudpanel"))
        self.assertIn("Needs at least 4096 MiB of swap (has 0); add the swap component first.", reasons("directadmin"))
        (self.root / "proc/meminfo").write_text("MemTotal:  8388608 kB\nSwapTotal: 8388608 kB\n")

        self.write("disk.json", 1000)
        self.assertIn("Needs at least 25600 MiB of free disk (has 1000).", reasons("cpanel"))
        self.write("disk.json", 102400)

        self.write("hostname.json", "localhost")
        self.assertIn('Needs a fully qualified host name (hostname -f gives "localhost").', reasons("hestia"))

    def test_7_owner_split(self):
        panels = ("aapanel",)
        nginx = self.plan_with("nginx", panels=panels)
        self.assertEqual(nginx["decision"], "REVIEW")
        self.assertIn("Managed by aapanel; use its own tools (DSSA installs only what the panel does not own).",
                      nginx["reasons"])
        for allowed in ("swap", "git", "chrony", "python", "nodejs", "java"):
            with self.subTest(allowed=allowed):
                self.assertNotIn("Managed by", " ".join(self.plan_with(allowed, panels=panels)["reasons"]))

    def test_9_approval_asks_for_the_panel_name(self):
        plan = {"runnable": ["aapanel"], "steps": [
            {"component": "aapanel", "action": "INSTALL", "details": {"panel": "aapanel", "reversible": False}}]}
        ((name, what, warnings),) = risky_steps(plan)
        self.assertEqual((name, what), ("aapanel", "install this control panel"))
        self.assertIn("Take a VM snapshot first", warnings[0])


class InstallTests(PanelServer):

    def test_3_aapanel_installs_and_hides_the_vendor_password(self):
        self.serve("aapanel")
        code, result, run = self.install("aapanel", "apt")
        self.assertEqual(code, 0, run.stderr)
        self.assertEqual(result["outcome"], "installed")
        self.assertTrue((self.root / "www/server/panel").is_dir())
        self.assertNotIn("S3cretFromVendor", run.stderr + run.stdout)
        self.assertIn("password: [hidden by DSSA]", run.stderr)
        self.assertIn("aapanel: installed", run.stderr, "the verify hook ran")
        self.assertEqual((self.state / "installer_args.txt").read_text().split(), ["-y"])
        journal = self.journal()
        self.assertRegex(journal, r'"sha256": ?"sha256:[0-9a-f]{64}"')
        self.assertNotIn("S3cretFromVendor", journal)
        record = json.loads((self.root / "var/lib/dssa/installed.json").read_text())
        self.assertEqual(record["components"]["aapanel"]["how"], "installed")

    def test_4_hestia_gets_fqdn_and_a_generated_password(self):
        self.serve("hestia")
        code, _, run = self.install("hestia", "apt")
        self.assertEqual(code, 0, run.stderr)
        args = (self.state / "installer_args.txt").read_text().split("\n")
        stored = self.root / "root/.dssa/hestia-admin-password"
        password = stored.read_text().strip()
        self.assertEqual(oct(stored.stat().st_mode & 0o777), "0o600")
        self.assertIn("server1.example.com", args)
        self.assertIn("admin@server1.example.com", args)
        self.assertIn(password, args)
        self.assertNotIn(password, run.stderr + run.stdout)
        self.assertNotIn(password, self.journal())

    def test_5_failure_without_an_uninstaller_needs_review(self):
        self.serve("aapanel", exit_code=1)
        code, result, run = self.install("aapanel", "apt")
        self.assertEqual(code, 4, run.stderr)
        self.assertEqual(result["outcome"], "rollback_incomplete")
        self.assertIn("reinstall the OS or restore the VM snapshot", json.dumps(result))

    def test_6_failure_with_an_uninstaller_runs_it(self):
        definition = load("aapanel")
        uninstall = "https://www.aapanel.com/script/uninstall_test.sh"
        for variant in definition["variants"]:
            variant["steps"][0]["uninstall_url"] = uninstall
        url = definition["variants"][0]["steps"][0]["url"]
        self.write("installers.json", {
            url: installer(FOLDER["aapanel"], exit_code=1),
            uninstall: '#!/bin/bash\nrm -rf "$DSSA_ROOT/www/server/panel"\ntouch "$MOCK_STATE/uninstalled"\n'})
        code, result, run = self.install_definition(definition)
        self.assertEqual(code, 3, run.stderr)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        self.assertTrue((self.state / "uninstalled").exists())
        self.assertFalse((self.root / "www/server/panel").exists())

    def test_8_an_installed_panel_cannot_be_removed(self):
        self.serve("aapanel")
        self.install("aapanel", "apt")
        record = self.bash("dssa_record_read").stdout
        result = self.bash(f"plan_remove_component {shlex.quote(json.dumps(load('aapanel')))} apt "
                           f"{shlex.quote(record)} '[\"aapanel\"]'")
        entry = json.loads(result.stdout)
        self.assertEqual(entry["action"], "BLOCKED")
        self.assertIn("Reinstall the OS or restore the VM snapshot", entry["reasons"][0])

    def test_10_failed_download_changes_nothing(self):
        self.serve("aapanel")
        self.write("fail.json", {"installer_download": True})
        code, result, run = self.install("aapanel", "apt")
        self.assertEqual(code, 3, run.stderr)
        self.assertIn("Could not download", run.stderr)
        self.assertFalse((self.root / "www/server/panel").exists())
        self.assertFalse((self.state / "installer_args.txt").exists(), "nothing ran")


if __name__ == "__main__":
    unittest.main()
