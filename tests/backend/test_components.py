"""The components-phase definitions (all draft until lab-verified).

Each definition is planned on a clean mocked server (apt and dnf), checked
against its conflicts, and installed end to end through the engine with
the mocks configured like the real packages. The new step types
(firewall_enable, swap_file, setup hooks) are also tested for failure and
rollback.
"""

import json
import shlex
import subprocess
import unittest
from pathlib import Path

from tests.backend.test_journal_rollback import FakeServer, MOCK, PLAN_A, ROOT

DEFS = ROOT / "definitions/components"
TEMPLATES = ROOT / "definitions/templates"
EXTRA_TOOLS = ("ss", "ufw", "firewall-offline-cmd", "fallocate", "mkswap", "postgresql-setup")
NEW = ("apache", "certbot", "chrony", "fail2ban", "firewall", "mariadb", "mysql", "nginx",
       "nodejs", "php", "postgresql", "python", "redis", "swap")
NO_PORTS = '{"listening_tcp_ports": []}'
NO_PANELS = '{"panels": []}'

# How each component's packages behave on the mocked server.
MOCKS = {
    "apt": {
        "pkg_units": {"nginx": ["nginx.service"], "apache2": ["apache2.service"],
                      "mariadb-server": ["mariadb.service"], "mysql-server": ["mysql.service"],
                      "postgresql": ["postgresql.service"], "redis-server": ["redis-server.service"],
                      "fail2ban": ["fail2ban.service"], "chrony": ["chrony.service"],
                      "php-fpm": ["php8.3-fpm.service"]},
        "autostart": {"php-fpm": ["php8.3-fpm.service"]},
        "pkg_paths": {"nginx": ["/etc/nginx/conf.d"], "mariadb-server": ["/etc/mysql/mariadb.conf.d"],
                      "fail2ban": ["/etc/fail2ban/jail.d"]},
    },
    "dnf": {
        "pkg_units": {"nginx": ["nginx.service"], "httpd": ["httpd.service"],
                      "mariadb-server": ["mariadb.service"], "mysql8.4-server": ["mysqld.service"],
                      "postgresql-server": ["postgresql.service"], "valkey": ["valkey.service"],
                      "fail2ban": ["fail2ban.service"], "chrony": ["chronyd.service"],
                      "php-fpm": ["php-fpm.service"], "firewalld": ["firewalld.service"]},
        "autostart": {},
        "pkg_paths": {"nginx": ["/etc/nginx/conf.d"], "mariadb-server": ["/etc/my.cnf.d"],
                      "fail2ban": ["/etc/fail2ban/jail.d"]},
    },
}
COMMON = {
    "pkg_bins": {"nginx": ["nginx"], "certbot": ["certbot"], "mariadb-server": ["mariadb"],
                 "mysql-server": ["mysql"], "mysql8.4-server": ["mysql"], "postgresql": ["psql"],
                 "postgresql-server": ["psql"], "redis-server": ["redis-server"], "valkey": ["valkey-server"],
                 "php-cli": ["php"], "nodejs": ["node"],
                 "fail2ban": ["fail2ban-client"], "chrony": ["chronyc"]},
    "bin_versions": {"nginx": "nginx version: nginx/1.24.0", "certbot": "certbot 2.9.0",
                     "mariadb": "mariadb  Ver 15.1 Distrib 10.11.8-MariaDB", "mysql": "mysql  Ver 8.0.40",
                     "psql": "psql (PostgreSQL) 16.4", "redis-server": "Redis server v=7.0.15 sha=0",
                     "valkey-server": "Valkey server v=8.0.1 sha=0", "php": "PHP 8.3.6 (cli)",
                     "node": "v18.19.1", "python3": "Python 3.12.3", "fail2ban-client": "Fail2Ban v1.0.2",
                     "chronyc": "chronyc (chrony) version 4.5 (+READLINE)"},
    "unit_ports": {"nginx.service": [80, 443], "apache2.service": [80], "httpd.service": [80],
                   "mariadb.service": [3306], "mysql.service": [3306], "mysqld.service": [3306],
                   "postgresql.service": [5432], "redis-server.service": [6379], "valkey.service": [6379]},
}


def load(component):
    return json.loads((DEFS / f"{component}.json").read_text())


def template_bundle():
    return json.dumps([{"id": str(p.relative_to(TEMPLATES)), "content": p.read_text().rstrip("\n")}
                       for p in sorted(TEMPLATES.rglob("*")) if p.is_file() and p.name != "README.md"])


class ComponentServer(FakeServer):

    def setUp(self):
        super().setUp()
        for tool in EXTRA_TOOLS:
            (self.tools / tool).symlink_to(MOCK)
        (self.root / "proc").mkdir()
        (self.root / "proc/meminfo").write_text("MemTotal:         131072 kB\n")
        (self.root / "etc/systemd/system").mkdir(parents=True)
        self.os("ubuntu", "24.04")

    def os(self, os_id, version):
        (self.root / "etc/os-release").write_text(f'ID={os_id}\nVERSION_ID="{version}"\n')

    def configure(self, manager):
        for name, value in {**COMMON, **MOCKS[manager]}.items():
            self.write(f"{name}.json", value)

    def bash(self, body, with_root=True):
        hooks = ROOT / "backend/linux/engine/hooks/components.sh"
        prelude = (f"source {shlex.quote(str(hooks))}\n"
                   f"DSSA_TEMPLATE_BUNDLE={shlex.quote(template_bundle())}\n")
        return super().bash(prelude + body, with_root)

    def plan(self, component, manager, ports=NO_PORTS):
        result = self.bash(f"plan_component_from_definition {shlex.quote(json.dumps(load(component)))} "
                           f"{manager} {shlex.quote(NO_PANELS)} {shlex.quote(ports)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def install(self, component, manager):
        definition = load(component)
        variant = next(v["variant_id"] for v in definition["variants"]
                       if v["platform"]["package_manager"] == manager)
        result = self.bash(f"""
begin_transaction {PLAN_A} {manager} '[]' || exit 90
r="$(apply_component {PLAN_A} {manager} {shlex.quote(json.dumps(definition))} {variant} true)"; rc=$?
end_transaction {PLAN_A} "[$r]" || exit 91
printf '%s\\n' "$r"; exit $rc""")
        self.assertNotIn(result.returncode, (90, 91), result.stderr)
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1]), result


class PlanTests(ComponentServer):

    def test_every_new_component_plans_install_on_a_clean_server(self):
        for manager, os_id, version in (("apt", "ubuntu", "24.04"), ("dnf", "almalinux", "10.0")):
            self.os(os_id, version)
            for component in NEW:
                with self.subTest(manager=manager, component=component):
                    entry = self.plan(component, manager)
                    self.assertEqual(entry["decision"], "INSTALL", entry["reasons"])
                    self.assertEqual(entry["action"], "REVIEW", "draft until the lab run")

    def test_conflicts(self):
        self.write("packages.json", {"base-files": "1.0", "systemd-timesyncd": "1.0", "mysql-server": "8.0"})
        self.assertIn("Conflicting package requires review: systemd-timesyncd", self.plan("chrony", "apt")["reasons"])
        self.assertIn("Conflicting package requires review: mysql-server", self.plan("mariadb", "apt")["reasons"])
        busy = self.plan("nginx", "apt", '{"listening_tcp_ports": [80]}')
        self.assertEqual(busy["decision"], "REVIEW")
        self.assertIn("TCP port 80 is in use.", busy["reasons"])
        self.os("debian", "13")
        self.assertEqual(self.plan("mysql", "apt")["decision"], "BLOCKED", "Debian has no mysql-server")

    def test_swap_and_firewall_are_detected_by_state(self):
        self.assertEqual(self.plan("swap", "apt")["decision"], "INSTALL")
        (self.root / "proc/swaps").write_text("Filename Type Size Used Priority\n/swap.img file 1 0 -2\n")
        self.assertEqual(self.plan("swap", "apt")["decision"], "SKIP")
        self.assertEqual(self.plan("firewall", "apt")["decision"], "INSTALL", "ufw installed but off")
        (self.root / "etc/ufw").mkdir(parents=True)
        (self.root / "etc/ufw/ufw.conf").write_text("ENABLED=yes\n")
        self.assertEqual(self.plan("firewall", "apt")["decision"], "SKIP")


class InstallTests(ComponentServer):

    def check(self, component, manager):
        self.configure(manager)
        code, result, run = self.install(component, manager)
        self.assertEqual(code, 0, run.stderr)
        self.assertEqual(result["outcome"], "installed", run.stderr)
        return run

    def test_every_new_component_installs_and_verifies_on_apt(self):
        for component in NEW:
            with self.subTest(component=component):
                self.setUp()  # a fresh server for each
                self.check(component, "apt")

    def test_every_new_component_installs_and_verifies_on_dnf(self):
        for component in NEW:
            with self.subTest(component=component):
                self.setUp()
                self.os("almalinux", "10.0")
                self.check(component, "dnf")

    def test_managed_files_are_written(self):
        self.check("mariadb", "apt")
        self.assertIn("bind-address = 127.0.0.1",
                      (self.root / "etc/mysql/mariadb.conf.d/dssa-bind.cnf").read_text())

    def test_postgresql_on_el_runs_initdb(self):
        self.os("almalinux", "10.0")
        run = self.check("postgresql", "dnf")
        self.assertTrue((self.root / "var/lib/pgsql/data/PG_VERSION").exists())
        self.assertIn("postgresql-setup --initdb", (self.state / "calls.log").read_text())


class FirewallTests(ComponentServer):

    def test_ufw_allows_ssh_and_web_before_it_is_enabled(self):
        self.configure("apt")
        self.write("ssh_ports.json", [22, 2222])
        code, _, run = self.install("firewall", "apt")
        self.assertEqual(code, 0, run.stderr)
        ufw = json.loads((self.state / "ufw.json").read_text())
        self.assertTrue(ufw["active"])
        self.assertEqual(ufw["rules"], ["22/tcp", "2222/tcp", "80/tcp", "443/tcp"])
        calls = (self.state / "calls.log").read_text().splitlines()
        enable = next(i for i, c in enumerate(calls) if c.startswith("ufw --force enable"))
        self.assertTrue(all(i < enable for i, c in enumerate(calls) if c.startswith("ufw allow")))

    def test_no_ssh_port_found_means_no_firewall(self):
        self.configure("apt")
        self.write("ssh_ports.json", [])
        code, result, run = self.install("firewall", "apt")
        self.assertEqual(code, 3)
        self.assertIn("Cannot find the port sshd listens on", run.stderr)
        self.assertFalse(json.loads((self.state / "ufw.json").read_text() if (self.state / "ufw.json").exists()
                                    else '{"active": false}')["active"])

    def test_failed_enable_removes_the_rules_it_added(self):
        self.configure("apt")
        self.write("fail.json", {"ufw_enable": True})
        code, result, run = self.install("firewall", "apt")
        self.assertEqual(code, 3)
        self.assertEqual(result["outcome"], "failed_rolled_back")
        ufw = json.loads((self.state / "ufw.json").read_text())
        self.assertEqual((ufw["active"], ufw["rules"]), (False, []))

    def test_firewalld_ports_are_added_before_it_starts(self):
        self.os("almalinux", "10.0")
        self.configure("dnf")
        code, _, run = self.install("firewall", "dnf")
        self.assertEqual(code, 0, run.stderr)
        self.assertEqual(json.loads((self.state / "firewalld.json").read_text())["ports"],
                         ["22/tcp", "80/tcp", "443/tcp"])
        self.assertEqual(self.read("units.json")["firewalld.service"]["active"], "active")


class SwapTests(ComponentServer):

    def test_swap_file_through_its_own_unit(self):
        code, result, run = self.install("swap", "apt")
        self.assertEqual(code, 0, run.stderr)
        self.assertTrue((self.root / "swapfile.dssa").exists())
        unit = (self.root / "etc/systemd/system/swapfile.dssa.swap").read_text()
        self.assertIn("What=/swapfile.dssa", unit)
        self.assertIn("/swapfile.dssa", (self.root / "proc/swaps").read_text())
        self.assertIn('"size_mb": 256', (self.root / "var/lib/dssa/transactions" / PLAN_A / "journal.jsonl").read_text()
                      .replace('"size_mb":256', '"size_mb": 256'))
        self.assertFalse((self.root / "etc/fstab").exists(), "fstab is never edited")

    def test_failed_mkswap_removes_the_file(self):
        self.write("fail.json", {"mkswap": True})
        code, result, run = self.install("swap", "apt")
        self.assertEqual(code, 3)
        self.assertFalse((self.root / "swapfile.dssa").exists())
        self.assertFalse((self.root / "etc/systemd/system/swapfile.dssa.swap").exists())


if __name__ == "__main__":
    unittest.main()
