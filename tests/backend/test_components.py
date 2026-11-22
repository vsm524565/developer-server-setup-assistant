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
EXTRA_TOOLS = ("ss", "ufw", "firewall-offline-cmd", "fallocate", "mkswap", "postgresql-setup",
               "debconf-set-selections")
NEW = ("apache", "certbot", "chrony", "fail2ban", "firewall", "mariadb", "mysql", "nginx",
       "nodejs", "php", "postgresql", "python", "redis", "swap",
       # second batch (OS repositories)
       "clamav", "composer", "haproxy", "java", "memcached", "postfix", "varnish",
       # third batch (vendor repositories)
       "caddy", "elasticsearch", "mongodb", "openlitespeed", "opensearch", "php82", "php83", "php84")
NO_PORTS = '{"listening_tcp_ports": []}'
NO_PANELS = '{"panels": []}'

# How each component's packages behave on the mocked server.
MOCKS = {
    "apt": {
        "pkg_units": {"nginx": ["nginx.service"], "apache2": ["apache2.service"],
                      "mariadb-server": ["mariadb.service"], "mysql-server": ["mysql.service"],
                      "postgresql": ["postgresql.service"], "redis-server": ["redis-server.service"],
                      "fail2ban": ["fail2ban.service"], "chrony": ["chrony.service"],
                      "php-fpm": ["php8.3-fpm.service"], "memcached": ["memcached.service"],
                      "varnish": ["varnish.service"], "haproxy": ["haproxy.service"],
                      "postfix": ["postfix.service"], "clamav-freshclam": ["clamav-freshclam.service"],
                      "caddy": ["caddy.service"], "elasticsearch": ["elasticsearch.service"],
                      "mongodb-org": ["mongod.service"], "openlitespeed": ["lsws.service"],
                      "opensearch": ["opensearch.service"], "php8.2-fpm": ["php8.2-fpm.service"],
                      "php8.3-fpm": ["php8.3-fpm.service"], "php8.4-fpm": ["php8.4-fpm.service"]},
        "autostart": {"php-fpm": ["php8.3-fpm.service"]},
        "pkg_paths": {"nginx": ["/etc/nginx/conf.d"], "mariadb-server": ["/etc/mysql/mariadb.conf.d"],
                      "fail2ban": ["/etc/fail2ban/jail.d"]},
    },
    "dnf": {
        "pkg_units": {"nginx": ["nginx.service"], "httpd": ["httpd.service"],
                      "mariadb-server": ["mariadb.service"], "mysql8.4-server": ["mysqld.service"],
                      "postgresql-server": ["postgresql.service"], "valkey": ["valkey.service"],
                      "fail2ban": ["fail2ban.service"], "chrony": ["chronyd.service"],
                      "php-fpm": ["php-fpm.service"], "firewalld": ["firewalld.service"],
                      "memcached": ["memcached.service"], "varnish": ["varnish.service"],
                      "haproxy": ["haproxy.service"], "postfix": ["postfix.service"],
                      "clamav-freshclam": ["clamav-freshclam.service"],
                      "caddy": ["caddy.service"], "elasticsearch": ["elasticsearch.service"],
                      "mongodb-org": ["mongod.service"], "openlitespeed": ["lsws.service"],
                      "opensearch": ["opensearch.service"], "php82-php-fpm": ["php82-php-fpm.service"],
                      "php83-php-fpm": ["php83-php-fpm.service"], "php84-php-fpm": ["php84-php-fpm.service"]},
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
                 "fail2ban": ["fail2ban-client"], "chrony": ["chronyc"],
                 "memcached": ["memcached"], "haproxy": ["haproxy"], "clamav": ["clamscan"],
                 "composer": ["composer"], "openjdk-21-jdk-headless": ["java"],
                 "java-21-openjdk-headless": ["java"], "mongodb-org": ["mongod"],
                 "php8.2-cli": ["php8.2"], "php8.3-cli": ["php8.3"], "php8.4-cli": ["php8.4"],
                 "php82-php-cli": ["php82"], "php83-php-cli": ["php83"], "php84-php-cli": ["php84"]},
    "bin_versions": {"nginx": "nginx version: nginx/1.24.0", "certbot": "certbot 2.9.0",
                     "mariadb": "mariadb  Ver 15.1 Distrib 10.11.8-MariaDB", "mysql": "mysql  Ver 8.0.40",
                     "psql": "psql (PostgreSQL) 16.4", "redis-server": "Redis server v=7.0.15 sha=0",
                     "valkey-server": "Valkey server v=8.0.1 sha=0", "php": "PHP 8.3.6 (cli)",
                     "node": "v18.19.1", "python3": "Python 3.12.3", "fail2ban-client": "Fail2Ban v1.0.2",
                     "chronyc": "chronyc (chrony) version 4.5 (+READLINE)",
                     "memcached": "memcached 1.6.24", "haproxy": "HAProxy version 2.8.5-1 2024/04/05",
                     "clamscan": "ClamAV 1.0.7/27000/Thu Nov 20 2026", "composer": "Composer version 2.7.1 2024-02-09",
                     "java": 'openjdk version "21.0.5" 2024-10-15', "mongod": "db version v8.0.4",
                     "php8.2": "PHP 8.2.28 (cli)", "php8.3": "PHP 8.3.21 (cli)", "php8.4": "PHP 8.4.8 (cli)",
                     "php82": "PHP 8.2.28 (cli)", "php83": "PHP 8.3.21 (cli)", "php84": "PHP 8.4.8 (cli)"},
    "unit_ports": {"nginx.service": [80, 443], "apache2.service": [80], "httpd.service": [80],
                   "mariadb.service": [3306], "mysql.service": [3306], "mysqld.service": [3306],
                   "postgresql.service": [5432], "redis-server.service": [6379], "valkey.service": [6379],
                   "memcached.service": [11211], "varnish.service": [6081], "postfix.service": [25],
                   "caddy.service": [80, 443], "elasticsearch.service": [9200], "mongod.service": [27017],
                   "lsws.service": [8088, 7080], "opensearch.service": [9200]},
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
        for folder in ("etc/yum.repos.d", "etc/pki/rpm-gpg", "etc/apt/keyrings"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.os("ubuntu", "24.04")

    def os(self, os_id, version):
        (self.root / "etc/os-release").write_text(f'ID={os_id}\nVERSION_ID="{version}"\n')

    def configure(self, manager):
        for name, value in {**COMMON, **MOCKS[manager]}.items():
            self.write(f"{name}.json", value)

    def bash(self, body, with_root=True):
        hooks = ROOT / "backend/linux/engine/hooks/components.sh"
        plan = ROOT / "backend/linux/engine/plan.sh"
        prelude = (f"source {shlex.quote(str(hooks))}\nsource {shlex.quote(str(plan))}\n"
                   f"DSSA_TEMPLATE_BUNDLE={shlex.quote(template_bundle())}\n")
        return super().bash(prelude + body, with_root)

    def plan(self, component, manager, ports=NO_PORTS):
        result = self.bash(f"plan_component_from_definition {shlex.quote(json.dumps(load(component)))} "
                           f"{manager} {shlex.quote(NO_PANELS)} {shlex.quote(ports)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def install(self, component, manager):
        definition = load(component)
        # The variant the planner would choose here (OS and version count).
        entry = self.plan(component, manager)
        variant = entry["variant_id"]
        self.assertIsNotNone(variant, entry["reasons"])
        # The mocked gpg reports this variant's key fingerprint.
        for step in next(v for v in definition["variants"] if v["variant_id"] == variant)["steps"]:
            if step["type"].endswith("_vendor_repository"):
                (self.state / "fingerprint").write_text(step["key_fingerprint"] + "\n")
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
        # Lab run 4.47: EL 10's parallel PHP 8.4 packages block the OS PHP.
        self.os("almalinux", "10.0")
        self.write("packages.json", {"base-files": "1.0", "php8.4-common": "8.4.27"})
        self.assertIn("Conflicting package requires review: php8.4-common", self.plan("php", "dnf")["reasons"])

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

    def test_postfix_is_local_only_on_apt(self):
        self.check("postfix", "apt")
        answers = (self.state / "debconf.txt").read_text()
        self.assertIn("postfix postfix/main_mailer_type select Local only", answers)
        calls = (self.state / "calls.log").read_text().splitlines()
        answered = next(i for i, c in enumerate(calls) if c.startswith("debconf-set-selections"))
        installed = next(i for i, c in enumerate(calls) if c.startswith("apt-get") and " install " in f" {c} " and "postfix" in c)
        self.assertLess(answered, installed, "answered before the package asks")

    def test_composer_brings_php(self):
        result = self.bash(f"""jq -c "$DSSA_PLAN_ORDER_JQ" <<< {shlex.quote(json.dumps({
            "definitions": {"composer": load("composer"), "php": load("php")}, "selection": ["composer"]}))}""")
        self.assertEqual([o["id"] for o in json.loads(result.stdout)], ["php", "composer"])

    def test_opensearch_password_is_root_only_and_never_logged(self):
        run = self.check("opensearch", "apt")
        stored = self.root / "root/.dssa/opensearch-initial-admin-password"
        self.assertEqual(oct(stored.stat().st_mode & 0o777), "0o600")
        password = stored.read_text().strip()
        self.assertEqual(len(password), 28)
        self.assertNotIn(password, run.stderr + run.stdout)
        journal = (self.root / "var/lib/dssa/transactions" / PLAN_A / "journal.jsonl").read_text()
        self.assertNotIn(password, journal)

    def test_vendor_keys_are_the_verified_ones(self):
        # Each vendor step carries the fingerprint checked on 2026-11-21.
        expected = {"caddy": "65760C51EDEA2017CEA2CA15155B6D79CA56EA34",
                    "openlitespeed": "3E892522DB44E1B063D366C5011AA62DEDA1F085",
                    "mongodb": "4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05",
                    "elasticsearch": "46095ACC8548582C1A2699A9D27D666CD88E42B4",
                    "opensearch": "A8B2D9E04CD51FEF6AA2DB53BA81D99981191457"}
        for component, fpr in expected.items():
            steps = [st for v in load(component)["variants"] for st in v["steps"]
                     if st["type"].endswith("_vendor_repository")]
            self.assertTrue(steps and all(st["key_fingerprint"] == fpr for st in steps), component)
        php = {st["repository_id"]: st["key_fingerprint"] for v in load("php84")["variants"]
               for st in v["steps"] if st["type"].endswith("_vendor_repository")}
        self.assertEqual(php, {"ondrej-php": "B8DC7E53946656EFBCE4C1DD71DAEAAB4AD4CAB6",
                               "sury-php": "15058500A0235D97F5D10063B188E2B695BD4743",
                               "remi-safe": "CF1DF0057CE85DFF5B2F2A37C2FD3B2C2A0948E4"})

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
