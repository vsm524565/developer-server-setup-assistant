"""Web UI on the master server (4.46): login, CSRF, jobs, server enrolment."""

import http.client
import contextlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bridge.components.approval import ApprovalError
from bridge.webui import enrol
from bridge.webui.app import App, Config, Handler, serve
from bridge.webui.auth import AuthError, Sessions, Users
from bridge.webui.jobs import Job


class AuthTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.users = Users(Path(self.tmp.name) / "users.json")
        self.users.add("alice", "correct horse battery")

    def test_users_file_is_private_and_hashed(self):
        path = Path(self.tmp.name) / "users.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("correct horse", path.read_text())
        self.assertTrue(self.users.verify("alice", "correct horse battery"))
        self.assertFalse(self.users.verify("alice", "wrong password!!"))
        self.assertFalse(self.users.verify("nobody", "correct horse battery"))

    def test_short_passwords_and_odd_names_are_refused(self):
        with self.assertRaises(AuthError):
            self.users.add("bob", "short")
        with self.assertRaises(AuthError):
            self.users.add("Bob Smith", "long enough password")

    def test_open_users_file_is_not_trusted(self):
        (Path(self.tmp.name) / "users.json").chmod(0o644)
        with self.assertRaises(AuthError):
            self.users.verify("alice", "correct horse battery")

    def test_sessions_expire_when_idle_and_lock_after_failures(self):
        now = [1000.0]
        sessions = Sessions(self.users, clock=lambda: now[0])
        token = sessions.login("alice", "correct horse battery")
        self.assertEqual(sessions.get(token)["user"], "alice")
        now[0] += 31 * 60
        self.assertIsNone(sessions.get(token), "idle for more than 30 minutes")
        for _ in range(5):
            with self.assertRaises(AuthError):
                sessions.login("alice", "wrong password!!")
        with self.assertRaisesRegex(AuthError, "Too many"):
            sessions.login("alice", "correct horse battery")
        now[0] += 6 * 60
        self.assertTrue(sessions.login("alice", "correct horse battery"))


class HttpBase(unittest.TestCase):
    """The real handler on 127.0.0.1, plain HTTP (as behind a reverse proxy)."""

    @staticmethod
    def stored(plan):
        """The plan page loads the stored plan before any preflight."""
        return mock.patch("bridge.components.run.load_plan", return_value=(b"{}", plan, Path("/x")))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(Path(self.tmp.name) / "webui")
        # App() looks at stored plans when it starts: never the real state folder.
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = App(self.config)
        self.app.users.add("alice", "correct horse battery")
        handler = type("H", (Handler,), {"app": self.app, "secure": False})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read().decode()
        conn.close()
        return response, data

    def login(self):
        response, _ = self.request("POST", "/login", "user=alice&password=correct+horse+battery")
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";")[0]


class HttpTests(HttpBase):

    def test_everything_needs_login(self):
        for path in ("/", "/jobs", "/servers/add", "/plans/x"):
            response, _ = self.request("GET", path)
            self.assertEqual((response.status, response.getheader("Location")), (303, "/login"))
        response, _ = self.request("POST", "/plan", "operation=install")
        self.assertEqual(response.getheader("Location"), "/login")

    def test_security_headers(self):
        response, body = self.request("GET", "/login")
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertNotIn("<script>", body, "no inline scripts")

    def test_wrong_password_is_refused_and_audited(self):
        response, body = self.request("POST", "/login", "user=alice&password=nope+nope+nope")
        self.assertEqual(response.status, 401)
        self.assertIn("Wrong user name or password", body)
        audit = self.config.audit.read_text()
        self.assertIn('"action": "login_failed"', audit)
        self.assertNotIn("nope", audit, "passwords are never logged")

    def test_post_without_csrf_token_is_refused(self):
        cookie = self.login()
        response, _ = self.request("POST", "/plan", "operation=install&targets=lab1", cookie)
        self.assertEqual(response.status, 403)
        response, page = self.request("GET", "/servers/add", cookie=cookie)
        self.assertEqual(response.status, 200)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        response, _ = self.request("POST", "/logout", f"csrf={token}", cookie)
        self.assertEqual(response.status, 303)
        response, _ = self.request("GET", "/", cookie=cookie)
        self.assertEqual(response.getheader("Location"), "/login", "logged out")

    def test_server_page_shows_the_record(self):
        cookie = self.login()
        record = {"schema_version": "1.0", "components": {"git": {
            "how": "adopted", "recorded_at": "2026-11-20T00:00:00Z",
            "packages": [{"package": "git", "version": "1:2.43.0"}]}}}
        with mock.patch("bridge.components.run.read_record", return_value=record) as read:
            response, body = self.request("GET", "/servers/ubuntu24", cookie=cookie)
        self.assertEqual(response.status, 200, body)
        read.assert_called_once_with("ubuntu24", str(self.config.inventory))
        self.assertIn("Installed by DSSA", body)
        self.assertIn("git 1:2.43.0", body)
        response, _ = self.request("GET", "/servers/Bad%20Name", cookie=cookie)
        self.assertEqual(response.status, 400)

    def test_every_get_page_answers(self):
        # A page that raises an unexpected error must not drop the connection.
        cookie = self.login()
        for path in ("/", "/jobs", "/servers/add"):
            with self.subTest(path=path):
                response, _ = self.request("GET", path, cookie=cookie)
                self.assertEqual(response.status, 200)

    def test_unexpected_error_answers_500(self):
        cookie = self.login()
        with mock.patch("bridge.components.run.read_record", side_effect=TypeError("boom")), \
             mock.patch("traceback.print_exc"):
            response, body = self.request("GET", "/servers/ubuntu24", cookie=cookie)
        self.assertEqual(response.status, 500)
        self.assertNotIn("boom", body, "details stay in the terminal")

    def test_planning_errors_show_the_backend_reason(self):
        # Lab run 4.47: "Planning failed (exit 3)" alone hid a clock refusal.
        cookie = self.login()
        token = re.search(r'name="csrf" value="([^"]+)"', self.request("GET", "/servers/add", cookie=cookie)[1]).group(1)
        error = __import__("bridge.components.run", fromlist=["RunError"]).RunError(
            "Planning failed (exit 3).", ["The clock on debian13 is 456 s behind this computer (limit 300 s)."])
        with mock.patch("bridge.components.run.make_plan", side_effect=error):
            response, body = self.request("POST", "/plan", f"csrf={token}&targets=debian13&components=git", cookie)
        self.assertEqual(response.status, 400)
        self.assertIn("456 s behind this computer", body)

    def test_static_files_only(self):
        response, body = self.request("GET", "/static/app.js")
        self.assertEqual(response.status, 200)
        self.assertIn("poll", body)
        response, _ = self.request("GET", "/static/../auth.py")
        self.assertEqual(response.status, 404)

    def test_plain_http_is_refused_off_loopback(self):
        with self.assertRaises(SystemExit):
            serve(self.app, "0.0.0.0", 0)


class WorkspaceCatalogTests(HttpBase):
    """Web UI integration pass 1: workspace, saved servers, catalog and one
    operation reaching the planner (docs/webui/claude-integration-handoff.md)."""

    TARGETS = [{"target_id": "ubuntu24", "host": "192.0.2.24", "lab": True},
               {"target_id": "prod1", "host": "192.0.2.80", "lab": False}]
    RECORD = {"schema_version": "1.0", "components": {"git": {
        "how": "adopted", "recorded_at": "2026-11-20T00:00:00Z", "packages": []}}}
    PLAN_ID = "11111111-2222-4333-8444-555555555555"

    def setUp(self):
        super().setUp()
        self.config.home.mkdir(parents=True, exist_ok=True)
        self.config.inventory.write_text("{}")
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": state.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("list_targets", self.TARGETS), ("read_record", self.RECORD)):
            patcher = mock.patch(f"bridge.components.run.{name}", return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def csrf(self, cookie):
        return re.search(r'name="csrf" value="([^"]+)"', self.request("GET", "/servers/add", cookie=cookie)[1]).group(1)

    def plan(self, components, runnable, action="INSTALL", reasons=("Not installed.",)):
        return {"plan_id": self.PLAN_ID, "operation": "install", "package_manager": "apt",
                "created_at": "2026-11-25T00:00:00Z", "runnable": runnable,
                "target": {"target_id": "ubuntu24", "host_key_sha256": "SHA256:x"},
                "steps": [{"component": c, "action": action, "variant_id": "apt", "reasons": list(reasons)}
                          for c in components]}

    def test_sign_in_lands_on_the_workspace(self):
        cookie = self.login()
        response, body = self.request("GET", "/", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn('href="/servers"', body)
        self.assertIn('href="/servers/add"', body)
        self.assertIn("Saved servers", body)
        self.assertIn("Add server", body)
        response, body = self.request("GET", "/servers/add", cookie=cookie)
        self.assertIn('action="/servers/scan"', body, "enrolment is unchanged")
        response, body = self.request("GET", "/batch", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn('action="/plan"', body, "the batch form stays available")

    def test_saved_servers_populated_and_empty(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn('href="/servers/ubuntu24/apps"', body)
        self.assertIn('href="/servers/prod1/apps"', body)
        self.assertIn("192.0.2.24", body)
        self.list_targets.return_value = []
        response, body = self.request("GET", "/servers", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("No saved servers yet", body)
        self.assertIn('href="/servers/add"', body)
        self.config.inventory.unlink()
        self.list_targets.reset_mock()
        response, body = self.request("GET", "/servers", cookie=cookie)
        self.assertIn("No saved servers yet", body)
        self.list_targets.assert_not_called()

    def test_catalog_search_category_and_installed_state(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers/ubuntu24/apps", cookie=cookie)
        self.assertEqual(response.status, 200)
        for app in ("Nginx", "MariaDB", "Git", "aaPanel"):
            self.assertIn(app, body)
        self.assertRegex(body, r'href="/servers/ubuntu24/apps\?app=git"[^>]*>.*?Installed \(adopted\)')
        response, body = self.request("GET", "/servers/ubuntu24/apps?q=maria", cookie=cookie)
        self.assertIn("MariaDB", body)
        self.assertNotIn("Nginx", body)
        response, body = self.request("GET", "/servers/ubuntu24/apps?category=control_panel", cookie=cookie)
        self.assertIn("aaPanel", body)
        self.assertNotIn("MariaDB", body)
        response, body = self.request("GET", "/servers/ubuntu24/apps?q=zzzz", cookie=cookie)
        self.assertIn("No matching applications", body)
        self.assertEqual(self.read_record.call_count, 1, "the record is read once, then reused briefly")

    def plan_form(self, body):
        return re.search(r'<form method="post" action="/servers/ubuntu24/plan" id="plan-form">.*?</form>',
                         body, re.S).group(0)

    def test_keyboard_selection_of_one_app_and_its_actions(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers/ubuntu24/apps?app=git&q=gi", cookie=cookie)
        # Rows are links (Tab + Enter); the chosen one is marked for assistive tech.
        self.assertRegex(body, r'<a class="app-row" href="/servers/ubuntu24/apps\?app=git&amp;q=gi"[^>]*aria-current="true"')
        self.assertEqual(body.count('aria-current="true"'), 1)
        self.assertIn('<h2 id="selected-app" tabindex="-1">Git</h2>', body)
        # Each row has its own checkbox (Tab, Space) that belongs to the plan form.
        self.assertRegex(body, r'<input type="checkbox" class="pick" form="plan-form" name="component" value="git" '
                               r'data-id="git" data-name="Git" data-installed="1" data-draft="0" aria-label="Select Git">')
        form = self.plan_form(body)
        self.assertIn("Selected (<span id=\"sel-count\" tabindex=\"-1\">0</span>)", form)
        self.assertEqual(re.findall(r'name="operation" value="(\w+)"', form), ["install", "adopt", "update", "remove"])
        response, body = self.request("GET", "/servers/ubuntu24/apps?app=nginx&sel=nginx", cookie=cookie)
        form = self.plan_form(body)
        self.assertIn('<li>Nginx</li>', form)
        self.assertIn('id="chosen-hint" data-id="nginx">In your selection.', body,
                      "the script keeps this in step with the checkbox")
        self.assertRegex(body, r'value="nginx" [^>]*checked>')
        self.assertIn('<div id="draft-choice"><label><input type="checkbox" name="allow_draft"', form,
                      "draft on a lab server")
        self.assertIn('value="install" data-needs="0" required checked', form)

    def test_selection_survives_filtering_and_choosing_a_row(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers/ubuntu24/apps?q=maria&sel=git&sel=nginx&sel=nosuch&sel=git",
                                      cookie=cookie)
        form = self.plan_form(body)
        self.assertIn("Selected (<span id=\"sel-count\" tabindex=\"-1\">2</span>)", form, "unknown and repeated names dropped")
        self.assertIn("<li>Git</li><li>Nginx</li>", form)
        # Hidden by the filter, still in the plan.
        self.assertIn('<input type="hidden" class="pick" name="component" value="git"', form)
        self.assertIn('<input type="hidden" class="pick" name="component" value="nginx"', form)
        self.assertIn('href="/servers/ubuntu24/apps?app=mariadb&amp;q=maria&amp;sel=git&amp;sel=nginx"', body)
        self.assertEqual(body.count('name="sel" value="git" class="keep-sel"'), 1, "the filter form keeps it")
        self.assertIn('id="sel-mixed">', form, "git installed, nginx not: one plan has one action")
        response, body = self.request("GET", "/servers/ubuntu24/apps?sel=git", cookie=cookie)
        self.assertIn('value="update" data-needs="1" required checked', self.plan_form(body))
        self.assertIn('<div id="draft-choice" hidden>', self.plan_form(body))

    def test_record_unavailable_offers_every_action(self):
        cookie = self.login()
        self.read_record.side_effect = __import__("bridge.components.run", fromlist=["RunError"]).RunError(
            "Reading the install record failed (exit 255).")
        response, body = self.request("GET", "/servers/prod1/apps?app=nginx", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("Installed state unavailable: Reading the install record failed (exit 255). Every action", body)
        self.assertEqual(re.findall(r'name="operation" value="(\w+)"', body), ["install", "adopt", "update", "remove"])
        self.assertIn('data-installed=""', body, "unknown state: the script restricts nothing")
        self.assertNotIn('name="allow_draft"', body, "not a lab server")

    def test_descriptions_are_shown_and_searchable(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers/ubuntu24/apps?q=virus", cookie=cookie)
        self.assertIn("ClamAV", body)
        self.assertIn("virus scanner", body)
        self.assertNotIn("MariaDB", body)
        response, body = self.request("GET", "/servers/ubuntu24/apps?app=mariadb", cookie=cookie)
        self.assertIn("listening on localhost only", body)

    def test_a_recorded_panel_marks_what_it_owns(self):
        cookie = self.login()
        self.read_record.return_value = {"schema_version": "1.0", "components": {
            "hestia": {"how": "installed", "recorded_at": "2026-11-25T00:00:00Z", "packages": []}}}
        response, body = self.request("GET", "/servers/ubuntu24/apps?app=nginx", cookie=cookie)
        self.assertRegex(body, r'apps\?app=nginx"[^>]*>.*?Managed by HestiaCP')
        self.assertIn("Managed by HestiaCP: use the panel", body)
        self.assertNotRegex(body, r'apps\?app=git"[^>]*>(?:(?!</li>).)*Managed by', "git is not panel-owned")
        self.assertNotRegex(body, r'apps\?app=swap"[^>]*>(?:(?!</li>).)*Managed by', "swap is allowed next to a panel")
        self.assertRegex(body, r'apps\?app=hestia"[^>]*>.*?Installed</span>')
        self.assertNotIn("Installed (installed)", body)

    def test_without_a_recorded_panel_nothing_is_marked(self):
        cookie = self.login()
        response, body = self.request("GET", "/servers/ubuntu24/apps", cookie=cookie)
        self.assertNotIn("Managed by", body)
        self.assertIn("A control panel installed without DSSA is found when you plan", body)

    def test_unknown_server_and_bad_names(self):
        cookie = self.login()
        response, _ = self.request("GET", "/servers/nope/apps", cookie=cookie)
        self.assertEqual(response.status, 404)
        response, _ = self.request("GET", "/servers/Bad%20Name/apps", cookie=cookie)
        self.assertEqual(response.status, 400)

    def test_one_operation_reaches_the_planner(self):
        cookie = self.login()
        token = self.csrf(cookie)
        plan = self.plan(["nginx"], ["nginx"])
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", plan)) as make, \
             mock.patch("bridge.components.run.save_plan") as save:
            response, _ = self.request("POST", "/servers/ubuntu24/plan",
                                       f"csrf={token}&component=nginx&operation=install&allow_draft=1", cookie)
        self.assertEqual((response.status, response.getheader("Location")), (303, f"/plans/{self.PLAN_ID}"))
        make.assert_called_once_with("ubuntu24", ["nginx"], True, str(self.config.inventory), "install", False)
        save.assert_called_once_with(b"{}")
        audit = [json.loads(line) for line in self.config.audit.read_text().splitlines()]
        self.assertEqual(audit[-1]["components"], ["nginx"])
        self.assertEqual(audit[-1]["target"], "ubuntu24")

    def test_drafts_are_never_allowed_off_lab_or_for_other_operations(self):
        cookie = self.login()
        token = self.csrf(cookie)
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", self.plan(["nginx"], []))) as make, \
             mock.patch("bridge.components.run.save_plan"):
            self.request("POST", "/servers/prod1/plan", f"csrf={token}&component=nginx&operation=install&allow_draft=1", cookie)
            self.request("POST", "/servers/ubuntu24/plan", f"csrf={token}&component=nginx&operation=remove&allow_draft=1", cookie)
        self.assertEqual([c.args[2] for c in make.call_args_list], [False, False])

    def test_several_applications_make_one_plan(self):
        cookie = self.login()
        token = self.csrf(cookie)
        plan = self.plan(["nginx", "git"], ["nginx", "git"])
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", plan)) as make, \
             mock.patch("bridge.components.run.save_plan"):
            response, _ = self.request("POST", "/servers/ubuntu24/plan",
                                       f"csrf={token}&component=nginx&component=git&component=nginx"
                                       f"&operation=install&allow_draft=1", cookie)
        self.assertEqual((response.status, response.getheader("Location")), (303, f"/plans/{self.PLAN_ID}"))
        make.assert_called_once_with("ubuntu24", ["nginx", "git"], True, str(self.config.inventory), "install", False)
        audit = [json.loads(line) for line in self.config.audit.read_text().splitlines()]
        self.assertEqual(audit[-1]["components"], ["nginx", "git"])

    def test_skipped_steps_are_verified_and_report_waits_for_a_run(self):
        cookie = self.login()
        plan = self.plan(["git", "nginx"], ["git", "nginx"])
        plan["steps"][0]["action"] = "SKIP"
        check = {"issues": [], "passed": True, "verification_only": False, "requires_approval": True}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            _, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
            self.assertIn("<dt>Changes</dt><dd>nginx</dd>", body)
            self.assertIn("<dt>Verified only</dt><dd>git</dd>", body)
            self.assertNotIn(f"/plans/{self.PLAN_ID}/report", body)
            ran = mock.Mock(plan_id=self.PLAN_ID)
            self.app.jobs = mock.Mock(recent=lambda limit=20: [ran])
            _, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
            self.assertIn(f"/plans/{self.PLAN_ID}/report", body)
            self.assertIn(f"/plans/{self.PLAN_ID}/resume", body)

    def test_too_many_applications_for_one_plan(self):
        ids = [a["id"] for a in self.app.catalog()][:26]
        with mock.patch("bridge.components.run.make_plan") as make:
            with self.assertRaisesRegex(ValueError, "at most 25 applications"):
                self.app.make_app_plan("alice", "ubuntu24", ids, "install")
        make.assert_not_called()

    def test_bad_selections_are_refused_before_planning(self):
        cookie = self.login()
        token = self.csrf(cookie)
        too_many = "".join(f"&component={a}" for a in [
            "nginx", "git", "apache", "mariadb", "mysql", "postgresql", "redis", "memcached", "php", "nodejs",
            "python", "java", "composer", "docker", "docker_compose", "caddy", "haproxy", "varnish", "certbot",
            "fail2ban", "firewall", "chrony", "swap", "postfix", "clamav", "openlitespeed"])
        bodies = (f"csrf={token}&component=nginx&component=git&operation=install&operation=remove",
                  f"csrf={token}{too_many}&operation=install",
                  f"csrf={token}&operation=install",
                  f"csrf={token}&component=nosuchapp&operation=install",
                  f"csrf={token}&component=nginx&operation=format")
        with mock.patch("bridge.components.run.make_plan") as make:
            for body in bodies:
                with self.subTest(body=body):
                    response, page_body = self.request("POST", "/servers/ubuntu24/plan", body, cookie)
                    self.assertEqual(response.status, 400)
                    self.assertIn('href="/servers/ubuntu24/apps"', page_body)
            response, _ = self.request("POST", "/servers/nope/plan", f"csrf={token}&component=git&operation=install", cookie)
            self.assertEqual(response.status, 400)
        make.assert_not_called()

    def test_planning_error_keeps_the_backend_reason(self):
        cookie = self.login()
        token = self.csrf(cookie)
        error = __import__("bridge.components.run", fromlist=["RunError"]).RunError(
            "Planning failed (exit 3).", ["The clock on ubuntu24 is 456 s behind this computer (limit 300 s)."])
        with mock.patch("bridge.components.run.make_plan", side_effect=error):
            response, body = self.request("POST", "/servers/ubuntu24/plan",
                                          f"csrf={token}&component=git&operation=install", cookie)
        self.assertEqual(response.status, 400)
        self.assertIn("456 s behind this computer", body)
        self.assertIn('href="/servers/ubuntu24/apps"', body)

    def test_blocked_plan_shows_reasons_and_offers_no_approval(self):
        cookie = self.login()
        plan = self.plan(["hestia"], [], action="BLOCKED",
                         reasons=["Not a fresh server: nginx is installed.", "Removal is not offered for panels."])
        check = {"issues": ["Nothing in the plan is runnable."], "passed": False,
                 "verification_only": False, "requires_approval": False}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            response, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("Not a fresh server: nginx is installed.", body)
        self.assertIn("Removal is not offered for panels.", body)
        self.assertIn("Nothing in this plan can run on <b>ubuntu24</b>", body)
        self.assertNotIn("Nothing in the plan is runnable.", body, "the notice says it once")
        self.assertNotIn("/report", body, "never ran: no Report or Resume rollback")
        self.assertNotIn("/approve", body)
        self.assertNotIn(f"/plans/{self.PLAN_ID}/apply", body)
        self.assertIn('href="/servers/ubuntu24/apps"', body)

    def test_runnable_plan_still_needs_approval_then_a_separate_apply(self):
        cookie = self.login()
        plan = self.plan(["git"], ["git"])
        check = {"issues": [], "passed": True, "verification_only": False, "requires_approval": True}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            response, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
        self.assertIn(f'action="/plans/{self.PLAN_ID}/approve"', body)
        self.assertIn('name="reviewed" value="1" required', body, "decision 3a: tick to approve")
        self.assertNotIn('name="confirm"', body, "no typed APPROVE in the web UI")
        self.assertIn("<dt>Server</dt><dd>ubuntu24</dd>", body)
        self.assertIn("<dt>Changes</dt><dd>git</dd>", body)
        self.assertNotIn("Verified only", body)
        self.assertNotIn(f'action="/plans/{self.PLAN_ID}/apply"', body, "Apply only after approval")

    def test_approve_needs_the_ticked_box(self):
        cookie = self.login()
        token = self.csrf(cookie)
        with mock.patch.object(App, "approve", return_value={}) as approve:
            self.request("POST", f"/plans/{self.PLAN_ID}/approve", f"csrf={token}&reviewed=1&name_git=git", cookie)
            self.request("POST", f"/plans/{self.PLAN_ID}/approve", f"csrf={token}&confirm=APPROVE", cookie)
        typed = [c.args[2] for c in approve.call_args_list]
        self.assertEqual(typed[0], {"git": "git", "_confirm": "APPROVE"})
        self.assertEqual(typed[1]["_confirm"], "", "typing APPROVE without the box confirms nothing")

    def test_another_users_approval_is_shown_but_not_offered(self):
        cookie = self.login()
        plan = self.plan(["git"], ["git"])
        check = {"issues": [], "passed": True, "verification_only": False, "requires_approval": True}
        folder = Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals" / self.PLAN_ID
        folder.mkdir(parents=True)
        (folder / "approval.json").write_text(json.dumps({"approved_by": "bob"}))
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            response, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
        self.assertIn("Approved by <b>bob</b>; only bob can apply it.", body)
        self.assertNotIn(f'action="/plans/{self.PLAN_ID}/apply"', body)
        (folder / "approval.json").write_text(json.dumps({"approved_by": "alice"}))
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            response, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
        self.assertIn(f'action="/plans/{self.PLAN_ID}/apply"', body)

    def test_catalog_pages_need_login(self):
        for path in ("/servers", "/servers/ubuntu24/apps", "/batch"):
            response, _ = self.request("GET", path)
            self.assertEqual((response.status, response.getheader("Location")), (303, "/login"))
        response, _ = self.request("POST", "/servers/ubuntu24/plan", "component=git&operation=install")
        self.assertEqual(response.getheader("Location"), "/login")


class JobTests(unittest.TestCase):

    def test_typed_input_reaches_the_terminal_and_markers_are_hidden(self):
        script = ('printf "[sudo] password for tester: "; IFS= read -r line; '
                  'echo; echo "got ${#line} characters"; '
                  'echo "DSSA_STATUS finished"; echo "DSSA_EXIT 0"')
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)
            job = Job("apply", ["bash", "-c", script], env, "11111111-1111-4111-8111-111111111111",
                      "lab1", "alice").start()
            deadline = time.time() + 10
            while "password for" not in job.view()["text"] and time.time() < deadline:
                time.sleep(0.05)
            job.send("s3cret-pass")
            while job.view()["running"] and time.time() < deadline:
                time.sleep(0.05)
            view = job.view()
        self.assertFalse(view["running"])
        self.assertEqual(view["exit_code"], 0)
        self.assertIn("got 11 characters", view["text"])
        self.assertNotIn("DSSA_EXIT", view["text"])
        # The output is the program's stdout, not the terminal's echo.
        self.assertNotIn("s3cret-pass", view["text"])

    def test_input_after_the_run_is_refused(self):
        job = Job("apply", ["bash", "-c", "true"], dict(os.environ), "11111111-1111-4111-8111-111111111111",
                  "lab1", "alice").start()
        deadline = time.time() + 10
        while job.view()["running"] and time.time() < deadline:
            time.sleep(0.05)
        with self.assertRaises(ValueError):
            job.send("late")


class EnrolTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.key = self.base / "master"
        self.key.write_text("private\n")
        Path(f"{self.key}.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample dssa-master\n")
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        out = ""
        if argv[0] == "ssh-keyscan":
            out = "# comment\n203.0.113.5 ssh-ed25519 AAAAhostkey\n"
        elif argv[:2] == ["ssh-keygen", "-l"]:
            out = "256 SHA256:" + "A" * 43 + " no comment (ED25519)\n"
        return mock.Mock(returncode=0, stdout=out, stderr="")

    def test_scan_then_enrol(self):
        keys = enrol.scan("203.0.113.5", 22, runner=self.runner)
        self.assertEqual(keys[0]["fingerprint"], "SHA256:" + "A" * 43)
        entry = enrol.enrol("web1", "203.0.113.5", 22, "deploy", "the-password", keys[0]["fingerprint"],
                            keys, self.key, self.base / "known_hosts", self.base / "inventory.json",
                            runner=self.runner)
        self.assertEqual(entry["ssh_key"], str(self.key))
        inventory = json.loads((self.base / "inventory.json").read_text())
        self.assertEqual(inventory["servers"][0]["target_id"], "web1")
        self.assertEqual(stat.S_IMODE((self.base / "inventory.json").stat().st_mode), 0o600)
        self.assertIn("203.0.113.5 ssh-ed25519 AAAAhostkey", (self.base / "known_hosts").read_text())
        password_call = [c for c in self.calls if c[0][0] == "ssh" and "PubkeyAuthentication=no" in c[0]][0]
        self.assertNotIn("the-password", " ".join(password_call[0]), "never on the command line")
        self.assertEqual(password_call[1]["env"].get("SSH_ASKPASS_REQUIRE"), "force")
        for path in self.base.rglob("*"):
            if path.is_file():
                self.assertNotIn("the-password", path.read_text(errors="replace"))

    def test_unconfirmed_fingerprint_is_refused(self):
        keys = enrol.scan("203.0.113.5", 22, runner=self.runner)
        with self.assertRaises(enrol.EnrolError):
            enrol.enrol("web1", "203.0.113.5", 22, "deploy", "pw", "SHA256:" + "B" * 43, keys,
                        self.key, self.base / "known_hosts", self.base / "inventory.json", runner=self.runner)
        self.assertFalse((self.base / "known_hosts").exists(), "nothing trusted")

    def test_bad_input_is_refused(self):
        for host, port, user in (("bad host", 22, "u"), ("h", 0, "u"), ("h", 22, "Bad User")):
            with self.subTest(host=host, port=port, user=user), self.assertRaises(enrol.EnrolError):
                enrol.enrol("web1", host, port, user, "pw", "x", [], self.key, self.base / "k",
                            self.base / "i.json", runner=self.runner)


class TemporaryAccessTests(unittest.TestCase):
    """Requirements item 2: a key for this setup only (decided 2026-11-25).
    The server side runs in a local shell with a throwaway HOME."""

    FP = "SHA256:" + "A" * 43
    KEYS = [{"fingerprint": FP, "line": "203.0.113.5 ssh-ed25519 AAAAhostkey", "type": "ssh-ed25519"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "server-home"
        (self.home / ".ssh").mkdir(parents=True, mode=0o700)
        self.keys_file = self.home / ".ssh/authorized_keys"
        self.keys_file.write_text("ssh-ed25519 AAAAexisting someone@laptop\n")
        self.calls, self.client, self.fail_key_login = [], "192.0.2.7 51234 22", False
        self.shell = "bash"

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] != "ssh":
            return __import__("subprocess").run(argv, **kwargs)
        if self.fail_key_login and "BatchMode=yes" in argv:
            return mock.Mock(returncode=255, stdout="", stderr="Permission denied")
        env = dict(os.environ, HOME=str(self.home), SSH_CLIENT=self.client)
        if getattr(self, "fake_bin", None):
            env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        return __import__("subprocess").run([self.shell, "-c", argv[-1]], env=env, capture_output=True, text=True)

    def connect(self, folder="t/tmp1"):
        return enrol.temporary_access("tmp1", "203.0.113.5", 22, "deploy", "the-password", self.FP, self.KEYS,
                                      self.base / folder, "alice", runner=self.runner, now=lambda: 1_800_000_000)

    def test_key_line_expires_is_limited_and_is_removed_again(self):
        meta = self.connect()
        folder = self.base / "t/tmp1"
        line = self.keys_file.read_text().splitlines()[-1]
        expected = time.strftime("%Y%m%d%H%M", time.localtime(1_800_000_000 + 3600))
        self.assertTrue(line.startswith(f'expiry-time="{expected}",from="192.0.2.7",no-port-forwarding,'
                                        'no-agent-forwarding,no-X11-forwarding ssh-ed25519 '), line)
        self.assertTrue(line.endswith(" " + meta["tag"]))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["inventory.json", "key", "key.pub", "known_hosts", "meta.json"])
        entry = json.loads((folder / "inventory.json").read_text())["servers"][0]
        self.assertEqual((entry["target_id"], entry["ssh_key"]), ("tmp1", str(folder / "key")))
        self.assertIn("203.0.113.5 ssh-ed25519 AAAAhostkey", (folder / "known_hosts").read_text())
        for argv, kwargs in self.calls:
            self.assertNotIn("the-password", " ".join(argv), "never on a command line")
        for path in folder.rglob("*"):
            self.assertNotIn("the-password", path.read_text(errors="replace"))
        self.assertTrue(enrol.end_temporary_access(folder, runner=self.runner))
        self.assertEqual(self.keys_file.read_text(), "ssh-ed25519 AAAAexisting someone@laptop\n")
        self.assertFalse(folder.exists())

    def test_no_client_address_installs_nothing(self):
        self.client = ""
        with self.assertRaises(enrol.EnrolError):
            self.connect()
        self.assertEqual(self.keys_file.read_text(), "ssh-ed25519 AAAAexisting someone@laptop\n")
        self.assertFalse((self.base / "t/tmp1").exists())

    def test_key_login_failure_removes_the_line(self):
        self.fail_key_login = True
        with self.assertRaisesRegex(enrol.EnrolError, "key login does not work"):
            self.connect()
        self.assertEqual(self.keys_file.read_text(), "ssh-ed25519 AAAAexisting someone@laptop\n",
                         "removed through the password session")
        self.assertFalse((self.base / "t/tmp1").exists())

    def test_failed_removal_keeps_the_folder_for_a_retry(self):
        self.connect()
        folder = self.base / "t/tmp1"
        self.fail_key_login = True  # after a removal the key itself no longer logs in
        with self.assertRaisesRegex(enrol.RemovalUnknown, "did not confirm the removal \\(Permission denied\\)"):
            enrol.end_temporary_access(folder, runner=self.runner)
        self.assertTrue((folder / "meta.json").exists())
        enrol.discard_temporary_access(folder)
        self.assertFalse(folder.exists())

    # --- round 10: a confirmed refusal versus an answer that never came ----

    def test_a_confirmed_refusal_is_told_apart(self):
        meta = self.connect()
        folder = self.base / "t/tmp1"
        # Round 12: a symlinked authorized_keys is never replaced: the line stays.
        real = self.home / "real_keys"
        real.write_text(self.keys_file.read_text())
        self.keys_file.unlink()
        self.keys_file.symlink_to(real)
        self.assertFalse(enrol.end_temporary_access(folder, runner=self.runner))
        self.assertTrue(self.keys_file.is_symlink(), "still the user's symlink")
        self.assertIn(meta["tag"], real.read_text())
        self.assertTrue((folder / "meta.json").exists())

    def test_a_lost_reply_after_a_real_removal_is_unknown(self):
        meta = self.connect()
        folder = self.base / "t/tmp1"

        def lost_reply(argv, **kwargs):
            self.runner(argv, **kwargs)  # the server removed the line...
            return mock.Mock(returncode=255, stdout="", stderr="Connection reset by peer")  # ...the reply was lost
        with self.assertRaisesRegex(enrol.RemovalUnknown, "Connection reset by peer"):
            enrol.end_temporary_access(folder, runner=lost_reply)
        self.assertNotIn(meta["tag"], self.keys_file.read_text(), "gone on the server")
        self.assertTrue((folder / "meta.json").exists(), "kept: DSSA cannot know that")

    # --- round 11: REMOVED only after the line is confirmed absent ----------

    def remove_under(self, shell, setup):
        """Connect, apply setup (permissions), run the real remove command in
        shell; return (result or the exception, authorized_keys afterwards)."""

        self.shell = "bash"
        meta = self.connect()
        folder = self.base / "t/tmp1"
        undo = setup() or (lambda: None)
        self.shell = shell
        try:
            outcome = enrol.end_temporary_access(folder, runner=self.runner)
        except enrol.RemovalUnknown as error:
            outcome = error
        finally:
            undo()
        text = self.keys_file.read_text() if self.keys_file.exists() else None
        return meta, outcome, text, folder

    def each_shell(self):
        for shell in ("bash", "dash"):
            with self.subTest(shell=shell):
                yield shell
                __import__("shutil").rmtree(self.base / "t", ignore_errors=True)
                (self.home / ".ssh").mkdir(mode=0o700, exist_ok=True)
                self.keys_file.write_text("ssh-ed25519 AAAAexisting someone@laptop\n")

    def chmod(self, path, mode, back):
        def apply():
            path.chmod(mode)
            return lambda: path.chmod(back)
        return apply

    def test_an_unreadable_authorized_keys_is_unknown_and_left_intact(self):
        for shell in self.each_shell():
            meta, outcome, text, folder = self.remove_under(shell, self.chmod(self.keys_file, 0o200, 0o600))
            self.assertIsInstance(outcome, enrol.RemovalUnknown)
            self.assertIn("could not check whether the key line is gone", str(outcome))
            self.assertIn("someone@laptop", text, "never truncated")
            self.assertIn(meta["tag"], text)
            self.assertTrue((folder / "meta.json").exists())

    def test_an_unsearchable_ssh_folder_is_unknown(self):
        for shell in self.each_shell():
            meta, outcome, text, folder = self.remove_under(shell, self.chmod(self.home / ".ssh", 0o600, 0o700))
            self.assertIsInstance(outcome, enrol.RemovalUnknown)
            self.assertIn(meta["tag"], text)

    def test_a_failed_rewrite_is_a_confirmed_refusal_and_truncates_nothing(self):
        for shell in self.each_shell():  # ~/.ssh read-only: the temporary file cannot be made
            meta, outcome, text, folder = self.remove_under(shell, self.chmod(self.home / ".ssh", 0o500, 0o700))
            self.assertIs(outcome, False)
            self.assertEqual(text.count("\n"), 2, "both lines still there")
            self.assertFalse((self.home / ".ssh/authorized_keys.dssa-tmp").exists())

    def test_a_missing_file_or_folder_is_a_confirmed_removal(self):
        for shell in self.each_shell():
            def drop_file():
                self.keys_file.unlink()
            _, outcome, _, folder = self.remove_under(shell, drop_file)
            self.assertIs(outcome, True)
            self.assertFalse(folder.exists())
        for shell in self.each_shell():
            def drop_folder():
                __import__("shutil").rmtree(self.home / ".ssh")
            _, outcome, _, _ = self.remove_under(shell, drop_folder)
            self.assertIs(outcome, True)

    def test_a_normal_removal_is_confirmed_in_both_shells(self):
        for shell in self.each_shell():
            meta, outcome, text, folder = self.remove_under(shell, lambda: None)
            self.assertIs(outcome, True)
            self.assertEqual(text, "ssh-ed25519 AAAAexisting someone@laptop\n")

    # --- round 12: an atomic replacement after a complete staged write -----

    def staged_files(self):
        return sorted(p.name for p in (self.home / ".ssh").iterdir() if ".dssa" in p.name)

    def failing(self, command, script="exit 1"):
        """Put a failing (or lying) command first on the server's PATH."""
        self.fake_bin = self.base / "fake-bin"
        self.fake_bin.mkdir(exist_ok=True)
        tool = self.fake_bin / command
        tool.write_text(f"#!/bin/sh\n{script}\n")
        tool.chmod(0o755)
        return lambda: setattr(self, "fake_bin", None)

    def test_a_read_only_file_is_replaced_atomically_with_its_mode_kept(self):
        for shell in self.each_shell():
            self.keys_file.write_text("ssh-ed25519 AAAAexisting someone@laptop\nssh-ed25519 AAAAother x@y")
            def read_only():
                self.inode = self.keys_file.stat().st_ino
                self.keys_file.chmod(0o400)
            meta, outcome, text, _ = self.remove_under(shell, read_only)
            self.assertIs(outcome, True)
            self.assertEqual(text, "ssh-ed25519 AAAAexisting someone@laptop\nssh-ed25519 AAAAother x@y\n",
                             "unrelated lines kept, also a last line without a newline")
            self.assertEqual(stat.S_IMODE(self.keys_file.stat().st_mode), 0o400, "its mode kept")
            self.assertNotEqual(self.keys_file.stat().st_ino, self.inode, "replaced by rename, not written in place")
            self.assertEqual(self.staged_files(), [])
            self.keys_file.chmod(0o600)

    def test_a_failed_step_leaves_authorized_keys_untouched(self):
        steps = {"mv": "exit 1", "chmod": "exit 1", "mktemp": "exit 1", "stat": "exit 1",
                 "wc": "echo 99"}  # a short or long staged copy
        for command, script in steps.items():
            for shell in self.each_shell():
                with self.subTest(step=command):
                    before = None

                    def setup():
                        nonlocal before
                        before = (self.keys_file.read_text(), self.keys_file.stat().st_ino)
                        return self.failing(command, script)
                    meta, outcome, text, folder = self.remove_under(shell, setup)
                    self.assertIs(outcome, False, "the line is still there: a confirmed refusal")
                    self.assertEqual((text, self.keys_file.stat().st_ino), before, "untouched, same file")
                    self.assertEqual(self.staged_files(), [], "the staged copy is removed")
                    self.assertTrue((folder / "meta.json").exists())

    def test_a_timeout_is_unknown(self):
        self.connect()
        timeout = mock.Mock(side_effect=__import__("subprocess").TimeoutExpired("ssh", 30))
        with self.assertRaisesRegex(enrol.RemovalUnknown, "no answer from the server"):
            enrol.end_temporary_access(self.base / "t/tmp1", runner=timeout)

    def test_unconfirmed_fingerprint_and_bad_names_are_refused(self):
        with self.assertRaises(enrol.EnrolError):
            enrol.temporary_access("tmp1", "203.0.113.5", 22, "deploy", "pw", "SHA256:" + "B" * 43, self.KEYS,
                                   self.base / "t/x", "alice", runner=self.runner)
        with self.assertRaises(enrol.EnrolError):
            enrol.temporary_access("Bad Name", "203.0.113.5", 22, "deploy", "pw", self.FP, self.KEYS,
                                   self.base / "t/y", "alice", runner=self.runner)
        self.assertEqual(self.calls, [])


class FakeJobs:
    def __init__(self):
        self.started = []

    def start(self, job):
        self.started.append(job)
        return job

    def recent(self, limit=20):
        return []

    def get(self, job_id):
        return None


class TemporaryServerTests(HttpBase):
    """The web UI side of item 2: pages, ownership, cleanup."""

    def setUp(self):
        super().setUp()
        self.app.users.add("bob", "another long password")
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": state.name, "DSSA_SSH_KEY": "/operator/key"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("list_targets", []),):
            patcher = mock.patch(f"bridge.components.run.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.removed = []
        patcher = mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=self.fake_end)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.end_result = True

    def fake_end(self, folder, runner=None):
        self.removed.append(Path(folder).name)
        if self.end_result:
            __import__("shutil").rmtree(folder, ignore_errors=True)
        return self.end_result

    @staticmethod
    def fake_access(name, host, port, user, password, fingerprint, keys, folder, owner, lab, window):
        folder = Path(folder)
        folder.mkdir(parents=True, mode=0o700)
        entry = {"target_id": name, "host": host, "ssh_user": user, "ssh_port": port,
                 "ssh_key": str(folder / "key"), "platform": "linux", "enabled": True, "lab": lab}
        meta = {"target_id": name, "owner": owner, "tag": "dssa-temp-" + os.urandom(8).hex(), "created": int(time.time()),
                "expires": int(time.time()) + window, "host": host, "entry": entry}
        (folder / "meta.json").write_text(json.dumps(meta))
        return meta

    def login_as(self, user, password):
        response, _ = self.request("POST", "/login", f"user={user}&password={password.replace(' ', '+')}")
        return response.getheader("Set-Cookie").split(";")[0]

    def token(self, cookie):
        return re.search(r'name="csrf" value="([^"]+)"', self.request("GET", "/servers/add", cookie=cookie)[1]).group(1)

    def connect(self, cookie, save=False):
        token = self.token(cookie)
        keys = [{"fingerprint": "SHA256:" + "A" * 43, "line": "203.0.113.5 ssh-ed25519 AAAA", "type": "ssh-ed25519"}]
        with mock.patch("bridge.webui.enrol.scan", return_value=keys):
            _, body = self.request("POST", "/servers/scan", f"csrf={token}&name=tmp1&host=203.0.113.5&port=22&user=deploy"
                                   + ("&save=1" if save else "") + "&lab=1", cookie)
        pending = re.search(r'name="pending" value="([^"]+)"', body).group(1)
        with mock.patch("bridge.webui.enrol.temporary_access", side_effect=self.fake_access) as temp, \
             mock.patch("bridge.webui.enrol.enrol", return_value={"target_id": "tmp1", "host": "203.0.113.5"}) as saved:
            response, _ = self.request("POST", "/servers/enrol",
                                       f"csrf={token}&pending={pending}&fingerprint=SHA256%3A{'A' * 43}&password=pw", cookie)
        return response, body, temp, saved

    def wait_until(self, predicate):
        deadline = time.time() + 5
        while not predicate() and time.time() < deadline:
            time.sleep(0.02)
        return predicate()

    def test_add_server_offers_saving_unticked(self):
        cookie = self.login()
        _, body = self.request("GET", "/servers/add", cookie=cookie)
        self.assertIn('name="save" value="1"> Save this server for future use', body)
        self.assertNotIn('name="save" value="1" checked', body)

    def test_not_saving_connects_for_this_setup_only(self):
        cookie = self.login()
        response, body, temp, saved = self.connect(cookie)
        self.assertIn("used once to install a key for this setup only", body)
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")
        temp.assert_called_once()
        saved.assert_not_called()
        self.assertEqual(temp.call_args.args[-1], 3600, "60-minute window")
        self.assertFalse(self.config.inventory.exists(), "no inventory entry")
        self.assertIn('"action": "temp_access_created"', self.config.audit.read_text())
        self.assertNotIn("pw", [json.loads(l).get("password") for l in self.config.audit.read_text().splitlines()])
        _, body = self.request("GET", "/", cookie=cookie)
        self.assertIn("This setup only", body)
        self.assertIn('action="/servers/tmp1/end"', body)
        _, body = self.request("GET", "/servers", cookie=cookie)
        self.assertNotIn("tmp1", body, "not a saved server")

    def test_ticking_save_keeps_todays_enrolment(self):
        cookie = self.login()
        response, body, temp, saved = self.connect(cookie, save=True)
        self.assertIn("used once to install the master", body)
        saved.assert_called_once()
        temp.assert_not_called()
        self.assertEqual(response.getheader("Location"), "/servers/tmp1")

    def test_only_the_owner_sees_and_uses_it(self):
        cookie = self.login()
        self.connect(cookie)
        other = self.login_as("bob", "another long password")
        _, body = self.request("GET", "/", cookie=other)
        self.assertNotIn("tmp1", body)
        response, _ = self.request("GET", "/servers/tmp1/apps", cookie=other)
        self.assertEqual(response.status, 404)
        with mock.patch("bridge.components.run.make_plan") as make:
            response, _ = self.request("POST", "/servers/tmp1/plan",
                                       f"csrf={self.token(other)}&component=git&operation=install", other)
            self.assertEqual(response.status, 400)
            with self.assertRaisesRegex(Exception, "another user's temporary server"):
                self.app.make_plans("bob", ["tmp1"], ["git"], "install", False)
        make.assert_not_called()
        response, _ = self.request("POST", "/servers/tmp1/end",
                                   f"csrf={self.token(other)}&connection={self.app.temporary['tmp1']['tag']}", other)
        self.assertEqual(response.status, 400, "even with the right connection tag")
        self.assertEqual(self.removed, [])

    def test_planning_uses_its_own_inventory_known_hosts_and_key(self):
        cookie = self.login()
        self.connect(cookie)
        folder = self.config.home / "temporary/tmp1"
        plan = {"plan_id": "11111111-2222-4333-8444-555555555555"}
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", plan)) as make, \
             mock.patch("bridge.components.run.save_plan"):
            response, _ = self.request("POST", "/servers/tmp1/plan",
                                       f"csrf={self.token(cookie)}&component=git&operation=install", cookie)
        self.assertEqual(response.status, 303)
        make.assert_called_once_with("tmp1", ["git"], False, str(folder / "inventory.json"), "install", False,
                                     known_hosts=str(folder / "known_hosts"))
        env = self.app.env("tmp1")
        self.assertEqual(env["DSSA_INVENTORY_FILE"], str(folder / "inventory.json"))
        self.assertEqual(env["DSSA_KNOWN_HOSTS"], str(folder / "known_hosts"))
        self.assertNotIn("DSSA_SSH_KEY", env, "the operator's key would override the temporary one")
        from bridge.components import run as run_module
        self.assertNotIn("DSSA_SSH_KEY", run_module._env("inv", "kh"))
        self.assertEqual(run_module._env("inv")["DSSA_SSH_KEY"], "/operator/key", "saved servers unchanged")

    def test_end_access_and_sign_out_remove_the_key(self):
        cookie = self.login()
        self.connect(cookie)
        tag = re.search(r'name="connection" value="([^"]+)"', self.request("GET", "/", cookie=cookie)[1]).group(1)
        self.assertEqual(tag, self.app.temporary["tmp1"]["tag"], "the form carries its connection")
        response, _ = self.request("POST", "/servers/tmp1/end", f"csrf={self.token(cookie)}&connection={tag}", cookie)
        self.assertEqual(response.getheader("Location"), "/")
        self.assertEqual(self.removed, ["tmp1"])
        self.assertNotIn("tmp1", self.app.temporary)
        self.connect(cookie)
        self.request("POST", "/logout", f"csrf={self.token(cookie)}", cookie)
        self.assertTrue(self.wait_until(lambda: "tmp1" not in self.app.temporary))
        self.assertEqual(self.removed, ["tmp1", "tmp1"])
        self.assertIn('"reason": "signed out"', self.config.audit.read_text())

    def test_sweeper_ends_access_near_expiry_or_without_a_session(self):
        cookie = self.login()
        self.connect(cookie)
        self.app.sweep_temporary({"alice"})
        self.assertIn("tmp1", self.app.temporary, "fresh and signed in: kept")
        self.app.temporary["tmp1"]["expires"] = time.time() + 60
        self.app.sweep_temporary({"alice"})
        self.assertNotIn("tmp1", self.app.temporary, "within 5 minutes of expiry: ended")
        self.connect(cookie)
        self.app.sweep_temporary(set())
        self.assertNotIn("tmp1", self.app.temporary, "owner signed out: ended")

    def test_failed_removal_is_kept_until_expiry_then_discarded(self):
        cookie = self.login()
        self.connect(cookie)
        self.end_result = False
        self.assertFalse(self.app.end_temporary("alice", "tmp1", "test", self.app.temporary["tmp1"]["tag"]))
        self.assertIn("tmp1", self.app.temporary)
        self.assertIn('"action": "temp_access_remove_failed"', self.config.audit.read_text())
        self.app.temporary["tmp1"]["expires"] = time.time() - 1
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "test", self.app.temporary["tmp1"]["tag"]))
        self.assertFalse((self.config.home / "temporary/tmp1").exists())
        self.assertIn('"action": "temp_access_expired"', self.config.audit.read_text())

    def test_a_restart_finds_leftover_access_and_the_sweep_ends_it(self):
        cookie = self.login()
        self.connect(cookie)
        restarted = App(self.config)
        self.assertIn("tmp1", restarted.temporary)
        restarted.sweep_temporary(restarted.sessions.active_users())
        self.assertNotIn("tmp1", restarted.temporary)
        self.assertEqual(self.removed, ["tmp1"])

    def test_a_successful_run_ends_access_and_a_failed_one_keeps_it(self):
        cookie = self.login()
        self.connect(cookie)
        self.app.jobs = FakeJobs()
        self.app.bind_plan("alice", "11111111-2222-4333-8444-555555555555", "tmp1")
        plan = {"target": {"target_id": "tmp1", "host_key_sha256": "SHA256:x"}}
        check = {"passed": True, "requires_approval": True, "verification_only": False, "issues": []}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)), \
             mock.patch("bridge.components.run.check_login", return_value=0) as login, \
             mock.patch("bridge.webui.app.consume_approval") as consume, \
             mock.patch("bridge.webui.app.approver", return_value="alice"), \
             mock.patch("pathlib.Path.read_bytes", return_value=b"{}"):
            job = self.app.start_apply("alice", "11111111-2222-4333-8444-555555555555")
            with self.assertRaisesRegex(Exception, "another user's temporary server"):
                self.app.start_apply("bob", "11111111-2222-4333-8444-555555555555")
        self.assertEqual(login.call_args.kwargs, {"known_hosts": str(self.config.home / "temporary/tmp1/known_hosts")})
        self.assertEqual(consume.call_args.kwargs["expected_operator"], "alice",
                         "the approver is checked again when the approval is claimed")
        job.on_finish(mock.Mock(code=4))
        time.sleep(0.2)
        self.assertIn("tmp1", self.app.temporary, "kept for Report and Resume rollback")
        job.on_finish(mock.Mock(code=0))
        self.assertTrue(self.wait_until(lambda: "tmp1" not in self.app.temporary))

    def test_only_the_owner_sees_its_plans_and_runs(self):
        """Lab finding (items 2–3, step 10): another user could open the plan page."""
        plan_id = "11111111-2222-4333-8444-555555555555"
        cookie = self.login()
        self.connect(cookie)
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", {"plan_id": plan_id})), \
             mock.patch("bridge.components.run.save_plan"):
            self.app.make_plans("alice", ["tmp1"], ["git"], "install", False)
        other = self.login_as("bob", "another long password")
        plan = {"target": {"target_id": "tmp1", "host_key_sha256": "SHA256:x"}, "steps": [], "operation": "install"}
        check = {"passed": True, "requires_approval": True, "verification_only": False, "issues": []}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)):
            response, body = self.request("GET", f"/plans/{plan_id}", cookie=other)
        self.assertEqual(response.status, 404)
        self.assertNotIn("tmp1", body)
        run = mock.Mock(id="0123456789abcdef", target="tmp1", plan_id=plan_id, user="alice", finished=None)
        self.app.jobs = mock.Mock(get=lambda job_id: run, recent=lambda limit=20: [run])
        response, _ = self.request("GET", "/jobs/0123456789abcdef", cookie=other)
        self.assertEqual(response.status, 404)
        response, _ = self.request("GET", "/jobs/0123456789abcdef/poll?offset=0", cookie=other)
        self.assertEqual(response.status, 404)
        self.assertNotIn("0123456789abcdef", self.request("GET", "/jobs", cookie=other)[1])
        self.assertTrue(self.app.may_see("alice", "tmp1", plan_id))
        # After access ends the plan stays its owner's.
        self.app.temporary.pop("tmp1")
        self.assertTrue(self.app.may_see("alice", "tmp1", plan_id))
        self.assertFalse(self.app.may_see("bob", "tmp1", plan_id))
        self.assertFalse(self.app.may_see("bob", "tmp1", None, "alice"))
        with mock.patch.object(App, "targets", return_value=[{"target_id": "web1"}]):
            self.assertTrue(self.app.may_see("bob", "web1", "99999999-2222-4333-8444-555555555555"),
                            "saved servers stay shared")

    def test_a_reused_name_never_inherits_plans_runs_or_approvals(self):
        """Codex review 2026-11-26: visibility followed the current server
        name. Plans now stay bound to their owner and the connection's tag,
        also across a restart."""
        plan_id = "11111111-2222-4333-8444-555555555555"
        alice = self.login()
        self.connect(alice)
        with mock.patch("bridge.components.run.make_plan", return_value=(b"{}", {"plan_id": plan_id})), \
             mock.patch("bridge.components.run.save_plan"):
            self.app.make_plans("alice", ["tmp1"], ["git"], "install", False)
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "ended by the user", self.app.temporary["tmp1"]["tag"]))
        # Bob connects a server with the same name.
        bob = self.login_as("bob", "another long password")
        self.connect(bob)
        self.assertEqual(self.app.temporary["tmp1"]["owner"], "bob")
        run = mock.Mock(id="0123456789abcdef", target="tmp1", plan_id=plan_id, user="alice", finished=0)
        self.app.jobs = mock.Mock(get=lambda job_id: run, recent=lambda limit=20: [run])
        self.assertFalse(self.app.may_see("bob", "tmp1", plan_id))
        self.assertFalse(self.app.may_see("bob", "tmp1", plan_id, "alice"))
        response, _ = self.request("GET", "/jobs/0123456789abcdef", cookie=bob)
        self.assertEqual(response.status, 404)
        self.assertNotIn("0123456789abcdef", self.request("GET", "/jobs", cookie=bob)[1])
        plan = {"target": {"target_id": "tmp1", "host_key_sha256": "SHA256:x"}, "steps": [], "operation": "install"}
        check = {"passed": True, "requires_approval": True, "verification_only": False, "issues": []}
        with self.stored(plan), mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)), \
             mock.patch("bridge.components.run.load_plan", return_value=(b"{}", plan, Path("/x"))), \
             mock.patch("bridge.components.run.check_login") as login:
            response, _ = self.request("GET", f"/plans/{plan_id}", cookie=bob)
            self.assertEqual(response.status, 404)
            for action in (lambda u: self.app.approve(u, plan_id, {"_confirm": "APPROVE"}),
                           lambda u: self.app.start_apply(u, plan_id),
                           lambda u: self.app.start_remote(u, "report", plan_id)):
                with self.assertRaisesRegex(Exception, "another user's temporary server"):
                    action("bob")
        login.assert_not_called()
        # Alice still owns it, even after a restart, but not through Bob's connection.
        restarted = App(self.config)
        self.assertEqual(restarted.plan_owners[plan_id]["owner"], "alice")
        self.assertTrue(restarted.may_see("alice", "tmp1", plan_id))
        self.assertFalse(restarted.may_see("bob", "tmp1", plan_id))
        restarted.temporary["tmp1"]["owner"] = "alice"  # as if Alice had reconnected
        with self.assertRaisesRegex(Exception, "earlier connection to tmp1"):
            restarted.require_plan_owner("alice", plan_id, "tmp1")
        self.assertEqual(
            (self.config.home / "temporary-plans" / f"{plan_id}.json").stat().st_mode & 0o777, 0o600)

    # --- binding before the plan is stored (Codex follow-up, 2026-11-26) ---

    PID = "22222222-2222-4333-8444-555555555555"

    def plan_with(self, during=None, save=None):
        """make_plans on tmp1 with a planner that runs `during` mid-planning."""

        def planner(*args, **kwargs):
            if during:
                during()
            return b"{}", {"plan_id": self.PID}
        with mock.patch("bridge.components.run.make_plan", side_effect=planner), \
             mock.patch("bridge.components.run.save_plan", side_effect=save) as saved:
            try:
                return self.app.make_plans("alice", ["tmp1"], ["git"], "install", False), saved
            except Exception as error:  # noqa: BLE001 - returned for the assertions
                return error, saved

    def record_path(self):
        return self.config.home / "temporary-plans" / f"{self.PID}.json"

    def test_the_binding_is_written_before_the_plan_is_stored(self):
        self.connect(self.login())
        tag = self.app.temporary["tmp1"]["tag"]
        seen = []
        result, saved = self.plan_with(save=lambda data: seen.append(json.loads(self.record_path().read_text())))
        self.assertEqual(result, [self.PID])
        saved.assert_called_once()
        self.assertEqual(seen, [{"owner": "alice", "connection": tag, "target": "tmp1", "source": "bound"}])
        plan_line = [json.loads(l) for l in self.config.audit.read_text().splitlines()][-1]
        self.assertEqual((plan_line["action"], plan_line["connection"]), ("plan", tag))
        created = [json.loads(l) for l in self.config.audit.read_text().splitlines()
                   if '"temp_access_created"' in l][-1]
        self.assertEqual(created["connection"], tag, "future upgrades have the connection as evidence")

    def test_access_ending_during_planning_discards_the_plan(self):
        self.connect(self.login())
        result, saved = self.plan_with(during=lambda: self.app.temporary.pop("tmp1"))
        self.assertRegex(str(result), "ended or changed while planning")
        saved.assert_not_called()
        self.assertFalse(self.record_path().exists())
        self.assertNotIn(self.PID, self.app.plan_owners)

    def test_a_replaced_connection_during_planning_discards_the_plan(self):
        self.connect(self.login())

        def replace():
            self.app.temporary["tmp1"] = dict(self.app.temporary["tmp1"], tag="dssa-temp-" + "f" * 16)
        result, saved = self.plan_with(during=replace)
        self.assertRegex(str(result), "ended or changed while planning")
        saved.assert_not_called()
        self.assertFalse(self.record_path().exists())

    def test_a_name_becoming_temporary_during_planning_discards_the_plan(self):
        with mock.patch.object(App, "targets", return_value=[{"target_id": "tmp1"}]):
            def appear():
                self.app.temporary["tmp1"] = {"owner": "alice", "tag": "dssa-temp-" + "a" * 16}
            result, saved = self.plan_with(during=appear)
        self.assertRegex(str(result), "became a temporary server while planning")
        saved.assert_not_called()

    def test_a_failed_binding_write_stores_no_plan(self):
        self.connect(self.login())
        with mock.patch.object(App, "write_plan_owner", side_effect=OSError("disk full")):
            result, saved = self.plan_with()
        self.assertIsInstance(result, OSError)
        saved.assert_not_called()
        self.assertFalse(self.record_path().exists())

    def test_a_failed_plan_write_removes_its_binding(self):
        self.connect(self.login())

        def fail(data):
            raise OSError("disk full")
        result, saved = self.plan_with(save=fail)
        self.assertIsInstance(result, OSError)
        self.assertFalse(self.record_path().exists())
        self.assertNotIn(self.PID, self.app.plan_owners)

    def test_a_partial_binding_file_is_removed(self):
        self.connect(self.login())
        with mock.patch("json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.app.bind_plan("alice", self.PID, "tmp1")
        self.assertFalse(self.record_path().exists())
        self.assertNotIn(self.PID, self.app.plan_owners)

    # --- actions stay on the authorized connection (Codex review, round 3) ---

    APID = "33333333-2222-4333-8444-555555555555"

    def action_setup(self, target="tmp1", bind=True):
        """A temporary plan of alice's, approved by her, ready to act on."""

        if bind:
            self.connect(self.login())
            self.app.bind_plan("alice", self.APID, target)
        self.app.jobs = FakeJobs()
        plan = {"plan_id": self.APID, "target": {"target_id": target, "host_key_sha256": "SHA256:x"},
                "steps": [], "operation": "install", "runnable": ["git"]}
        self.check_result = (b"{}", plan, Path("/x"), set(),
                             {"passed": True, "requires_approval": True, "verification_only": False, "issues": []})
        return contextlib.ExitStack(), plan

    def patches(self, stack, plan, check=None, login=None):
        stack.enter_context(mock.patch("bridge.components.run.load_plan", return_value=(b"{}", plan, Path("/x"))))
        self.check = stack.enter_context(mock.patch.object(
            App, "check", side_effect=check or (lambda *_: self.check_result)))
        self.login_check = stack.enter_context(mock.patch(
            "bridge.components.run.check_login", side_effect=login or (lambda *a, **k: 0)))
        self.consume = stack.enter_context(mock.patch("bridge.webui.app.consume_approval"))
        self.approved = stack.enter_context(mock.patch("bridge.webui.app.approve_plan", return_value={}))
        stack.enter_context(mock.patch("bridge.webui.app.approver", return_value="alice"))
        stack.enter_context(mock.patch("pathlib.Path.read_bytes", return_value=b"{}"))

    def replace(self):
        self.app.temporary["tmp1"] = dict(self.app.temporary["tmp1"], tag="dssa-temp-" + "f" * 16)

    def assert_nothing_used(self):
        self.consume.assert_not_called()
        self.assertEqual(self.app.jobs.started, [])
        self.assertEqual(self.app.actions, {}, "the action is unregistered after a refusal")

    def test_apply_refuses_when_the_connection_is_replaced_during_preflight(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan, check=lambda *_: (self.replace(), self.check_result)[1])
            with self.assertRaisesRegex(Exception, "ended or was replaced during this action"):
                self.app.start_apply("alice", self.APID)
        self.login_check.assert_not_called()
        self.assert_nothing_used()

    def test_apply_refuses_when_the_connection_is_replaced_during_login(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan, login=lambda *a, **k: (self.replace(), 0)[1])
            with self.assertRaisesRegex(Exception, "no approval was consumed"):
                self.app.start_apply("alice", self.APID)
        self.assert_nothing_used()

    def test_apply_refuses_when_access_ends_during_login(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan, login=lambda *a, **k: (self.app.temporary.pop("tmp1"), 0)[1])
            with self.assertRaisesRegex(Exception, "ended or was replaced"):
                self.app.start_apply("alice", self.APID)
        self.assert_nothing_used()

    def test_access_cannot_end_while_an_action_is_in_progress(self):
        stack, plan = self.action_setup()
        attempts = []

        def login(*args, **kwargs):
            attempts.append(self.app.end_temporary("alice", "tmp1", "signed out", self.app.temporary["tmp1"]["tag"]))
            return 0
        with stack:
            self.patches(stack, plan, login=login)
            job = self.app.start_apply("alice", self.APID)
        self.assertEqual(attempts, [False], "sign-out waits for the action")
        self.assertEqual(self.removed, [])
        self.assertNotIn("ending", self.app.temporary["tmp1"])
        self.assertEqual(self.consume.call_args.kwargs["expected_operator"], "alice")
        self.assertEqual(self.login_check.call_args.kwargs,
                         {"known_hosts": str(self.config.home / "temporary/tmp1/known_hosts")})
        self.assertEqual(job.env["DSSA_KNOWN_HOSTS"], str(self.config.home / "temporary/tmp1/known_hosts"))
        self.assertEqual(self.app.actions, {})

    def test_no_action_starts_on_an_ending_or_nearly_expired_connection(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            self.app.temporary["tmp1"]["ending"] = True
            for act in (lambda: self.app.start_apply("alice", self.APID),
                        lambda: self.app.start_remote("alice", "report", self.APID),
                        lambda: self.app.approve("alice", self.APID, {"_confirm": "APPROVE"})):
                with self.assertRaisesRegex(Exception, "ended or was replaced"):
                    act()
            del self.app.temporary["tmp1"]["ending"]
            self.app.temporary["tmp1"]["expires"] = int(time.time()) + 60
            with self.assertRaisesRegex(Exception, "ends in under 5 minutes"):
                self.app.start_apply("alice", self.APID)
        self.check.assert_not_called()
        self.approved.assert_not_called()
        self.assert_nothing_used()

    def test_approve_refuses_when_the_connection_is_replaced_during_preflight(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan, check=lambda *_: (self.replace(), self.check_result)[1])
            with self.assertRaisesRegex(Exception, "ended or was replaced"):
                self.app.approve("alice", self.APID, {"_confirm": "APPROVE"})
        self.approved.assert_not_called()
        self.assertEqual(self.app.actions, {})

    def test_report_and_resume_use_the_authorized_connection(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            job = self.app.start_remote("alice", "report", self.APID)
            self.assertEqual(job.env["DSSA_KNOWN_HOSTS"], str(self.config.home / "temporary/tmp1/known_hosts"))
            self.replace()
            with self.assertRaisesRegex(Exception, "earlier connection to tmp1"):
                self.app.start_remote("alice", "resume", self.APID)
        self.assertEqual(len(self.app.jobs.started), 1)
        self.assertEqual(self.app.actions, {})

    def test_a_saved_server_plan_refuses_if_its_name_turns_temporary_mid_action(self):
        stack, plan = self.action_setup(target="web1", bind=False)
        self.login()
        with stack, mock.patch.object(App, "targets", return_value=[{"target_id": "web1"}]):
            def turn(*_):
                self.app.temporary["web1"] = {"owner": "bob", "tag": "dssa-temp-" + "e" * 16,
                                              "expires": int(time.time()) + 3600}
                return self.check_result
            self.patches(stack, plan, check=turn)
            with self.assertRaisesRegex(Exception, "became a temporary server during this action"):
                self.app.start_apply("alice", self.APID)
        self.login_check.assert_not_called()
        self.assert_nothing_used()

    # --- cleanup bound to its connection (Codex review, round 5) -----------

    def tag(self, target="tmp1"):
        return self.app.temporary[target]["tag"]

    def test_overlapping_cleanup_removes_once(self):
        self.connect(self.login())
        tag = self.tag()
        inside, release = threading.Event(), threading.Event()
        real_end = self.fake_end

        def slow_end(folder, runner=None):
            inside.set()
            release.wait(5)
            return real_end(folder, runner)
        results = []
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=slow_end):
            first = threading.Thread(target=lambda: results.append(
                self.app.end_temporary("alice", "tmp1", "signed out", tag)))
            first.start()
            self.assertTrue(inside.wait(5))
            # A second removal (sweep, logout, manual end) backs off.
            self.assertFalse(self.app.end_temporary("alice", "tmp1", "expiry", tag))
            # The name stays taken until the removal has finished.
            bob = self.login_as("bob", "another long password")
            response, _, _, _ = self.connect(bob)
            self.assertEqual(response.status, 400)
            release.set()
            first.join(5)
        self.assertEqual(results, [True])
        self.assertEqual(self.removed, ["tmp1"], "removed exactly once")
        self.assertNotIn("tmp1", self.app.temporary)

    def test_stale_cleanup_never_touches_a_replacement(self):
        alice = self.login()
        self.connect(alice)
        old = self.tag()
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "ended by the user", old))
        bob = self.login_as("bob", "another long password")
        self.connect(bob)
        new = self.tag()
        self.assertNotEqual(old, new)
        # Late callers holding the old tag: job completion, logout, a sweep.
        for reason in ("run finished", "signed out", "expiry"):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", reason, old), "theirs is already gone")
        self.assertEqual(self.removed, ["tmp1"], "the replacement's key was never removed")
        self.assertEqual((self.app.temporary["tmp1"]["owner"], self.tag()), ("bob", new))

    def test_a_removal_never_forgets_a_connection_that_replaced_it(self):
        self.connect(self.login())
        old = self.tag()
        newer = dict(self.app.temporary["tmp1"], tag="dssa-temp-" + "d" * 16)

        def replace_while_removing(folder, runner=None):
            self.removed.append("tmp1")
            self.app.temporary["tmp1"] = newer
            return True
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=replace_while_removing):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", old))
        self.assertIs(self.app.temporary["tmp1"], newer)

    def test_one_connection_attempt_per_name_at_a_time(self):
        alice = self.login()
        self.app.connecting.add("tmp1")
        response, _, temp, _ = self.connect(alice)
        self.assertEqual(response.status, 400)
        temp.assert_not_called()
        self.app.connecting.discard("tmp1")
        response, _, temp, _ = self.connect(alice)
        self.assertEqual(response.status, 303)
        self.assertEqual(self.app.connecting, set())

    def test_run_completion_cleanup_leaves_a_replacement_alone(self):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            job = self.app.start_apply("alice", self.APID)
        self.replace()
        job.on_finish(mock.Mock(code=0))
        time.sleep(0.2)
        self.assertEqual(self.removed, [])
        self.assertEqual(self.tag(), "dssa-temp-" + "f" * 16)

    # --- a job that cannot start (Codex review, round 5) ------------------

    def test_a_job_that_cannot_start_is_left_finished(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        registry = jobs.Jobs()
        job = jobs.Job("apply", ["/nonexistent/dssa-run"], {}, "p", "tmp1", "alice")
        with self.assertRaises(jobs.JobStartError):
            registry.start(job)
        self.assertIsNotNone(job.finished)
        self.assertEqual(job.code, 127)
        self.assertIn("the run could not start", job.view()["text"])
        self.assertFalse(job.view()["running"])
        self.assertIs(registry.get(job.id), job, "still listed, as finished")
        with self.assertRaisesRegex(ValueError, "finished"):
            job.send("password")

    def test_a_failed_start_after_consumption_is_reported_and_blocks_no_cleanup(self):
        stack, plan = self.action_setup()
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        with stack:
            self.patches(stack, plan)
            self.app.jobs = jobs.Jobs()
            with mock.patch("bridge.webui.jobs.subprocess.Popen", side_effect=OSError("no pty left")):
                with self.assertRaisesRegex(Exception, r"The run could not start \(no pty left\)\. Nothing ran on "
                                                       r"the server\. The approval was used and cannot be used again"):
                    self.app.start_apply("alice", self.APID)
        self.consume.assert_called_once()
        self.assertFalse(self.app.running_on("tmp1"))
        self.assertEqual(self.app.actions, {})
        failed = [json.loads(l) for l in self.config.audit.read_text().splitlines() if '"apply_start_failed"' in l]
        self.assertEqual((len(failed), failed[0]["approval_used"]), (1, True))
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()), "cleanup not blocked")

    def test_a_failed_report_start_is_reported(self):
        stack, plan = self.action_setup()
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        with stack:
            self.patches(stack, plan)
            self.app.jobs = jobs.Jobs()
            with mock.patch("bridge.webui.jobs.os.openpty", side_effect=OSError("no pty left")):
                with self.assertRaisesRegex(Exception, "The report could not start"):
                    self.app.start_remote("alice", "report", self.APID)
        self.assertFalse(self.app.running_on("tmp1"))

    # --- failure paths (Codex review, round 6) ----------------------------

    def test_a_failing_removal_never_strands_the_connection_in_ending(self):
        self.connect(self.login())
        tag = self.tag()
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=ValueError("broken meta.json")):
            self.assertFalse(self.app.end_temporary("alice", "tmp1", "signed out", tag))
        self.assertNotIn("ending", self.app.temporary["tmp1"])
        failed = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "remove_failed" in l]
        self.assertEqual(failed[-1]["error"], "broken meta.json")
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", tag), "a retry still works")
        self.assertNotIn("tmp1", self.app.temporary)

    def test_a_failing_audit_never_strands_the_connection(self):
        self.connect(self.login())
        tag = self.tag()
        with mock.patch.object(App, "audit", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "was removed, but the audit log could not be written"):
                self.app.end_temporary("alice", "tmp1", "signed out", tag)
        self.assertNotIn("tmp1", self.app.temporary, "removed, so forgotten")
        self.connect(self.login())
        self.end_result = False
        with mock.patch.object(App, "audit", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.app.end_temporary("alice", "tmp1", "signed out", self.tag())
        self.assertNotIn("ending", self.app.temporary["tmp1"], "not removed, so usable and retryable")

    def test_manual_end_only_ends_the_connection_its_form_showed(self):
        cookie = self.login()
        self.connect(cookie)
        old = re.search(r'name="connection" value="([^"]+)"', self.request("GET", "/", cookie=cookie)[1]).group(1)
        self.replace()  # the name was reused by a newer connection
        response, body = self.request("POST", "/servers/tmp1/end", f"csrf={self.token(cookie)}&connection={old}",
                                      cookie)
        self.assertEqual(response.status, 400)
        self.assertIn("has already ended, so nothing was removed", body)
        response, body = self.request("POST", "/servers/tmp1/end", f"csrf={self.token(cookie)}", cookie)
        self.assertIn("This page is out of date", body)
        self.assertEqual(self.removed, [])
        self.assertEqual(self.tag(), "dssa-temp-" + "f" * 16, "the newer connection is untouched")

    def test_saved_enrolment_takes_the_same_name_reservation(self):
        cookie = self.login()
        self.connect(cookie)  # tmp1 is connected for this setup
        response, _, _, saved = self.connect(cookie, save=True)
        self.assertEqual(response.status, 400)
        saved.assert_not_called()
        self.app.temporary.clear()
        pending = {"name": "web9", "host": "203.0.113.9", "port": 22, "user": "deploy", "keys": [], "lab": True}
        seen = []

        def enrol_while_racing(*args, **kwargs):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.app.add_temporary("bob", dict(pending), "pw", "SHA256:x")
            seen.append(set(self.app.connecting))
            return {"target_id": "web9", "host": "203.0.113.9"}
        with mock.patch("bridge.webui.enrol.enrol", side_effect=enrol_while_racing), \
             mock.patch("bridge.webui.enrol.temporary_access") as temp:
            self.app.add_saved("alice", pending, "pw", "SHA256:x")
        temp.assert_not_called()
        self.assertEqual((seen, self.app.connecting), ([{"web9"}], set()))
        with mock.patch.object(App, "targets", return_value=[{"target_id": "web9"}]), \
             mock.patch("bridge.webui.enrol.enrol") as again:
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.app.add_saved("alice", pending, "pw", "SHA256:x")
        again.assert_not_called()

    def test_a_job_whose_teardown_fails_is_still_left_finished(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        process = mock.Mock(pid=4242)
        process.kill.side_effect = OSError("kill failed")
        process.wait.side_effect = OSError("wait failed")
        process.stdout.close.side_effect = OSError("close failed")
        job = jobs.Job("apply", ["bash"], {}, "p", "tmp1", "alice")
        with mock.patch("bridge.webui.jobs.subprocess.Popen", return_value=process), \
             mock.patch("bridge.webui.jobs.os.killpg", side_effect=OSError("killpg failed")) as killpg, \
             mock.patch("bridge.webui.jobs.threading.Thread", side_effect=RuntimeError("no threads")):
            with self.assertRaises(jobs.JobStartError) as raised:
                jobs.Jobs().start(job)
        self.assertTrue(raised.exception.launched, "the process existed: outcome uncertain")
        killpg.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual((job.code, job.view()["running"]), (127, False))
        self.assertIn("could not be followed", job.view()["text"])
        self.assertFalse(raised.exception.stopped, "wait failed: never said to be stopped")
        self.assertIn("could not confirm that it stopped", job.view()["text"])

    def test_a_job_that_never_launched_says_so(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        job = jobs.Job("apply", ["/nonexistent/dssa-run"], {}, "p", "tmp1", "alice")
        with self.assertRaises(jobs.JobStartError) as raised:
            job.start()
        self.assertFalse(raised.exception.launched)

    def apply_failure(self, **patched):
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            for name, value in patched.items():
                stack.enter_context(mock.patch(name, **value))
            with self.assertRaises(ApprovalError) as raised:
                self.app.start_apply("alice", self.APID)
        failed = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "_start_failed" in l]
        return str(raised.exception), failed

    def test_an_uncertain_launch_is_reported_as_uncertain(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        error = jobs.JobStartError("lost the pty", launched=True, stopped=True)
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            self.app.jobs = mock.Mock(start=mock.Mock(side_effect=error), recent=lambda limit=20: [])
            with self.assertRaises(ApprovalError) as raised:
                self.app.start_apply("alice", self.APID)
        message = str(raised.exception)
        self.assertIn("was started, but DSSA could not follow it and stopped it (lost the pty)", message)
        self.assertIn("Part of it may have run on the server", message)
        self.assertNotIn("Nothing ran", message)
        self.assertIn("The approval was used and cannot be used again", message)
        last = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "_start_failed" in l][-1]
        self.assertEqual((last["launched"], last["approval_used"], last["stage"]),
                         (True, True, "starting the run"))

    def test_a_claim_that_fails_after_its_marker_reports_the_approval_used(self):
        marker = Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals" / self.APID

        def claim_then_fail(*args, **kwargs):
            marker.mkdir(parents=True)
            (marker / "consumed").write_text("now\n")
            raise OSError("approval audit failed")
        message, failed = self.apply_failure(**{"bridge.webui.app.consume_approval": {"side_effect": claim_then_fail}})
        self.assertIn("was not started: claiming the approval failed (approval audit failed)", message)
        self.assertIn("The approval was used and cannot be used again", message)
        self.assertEqual((failed[-1]["approval_used"], failed[-1]["launched"]), (True, False))
        self.assertEqual(self.app.jobs.started, [])

    def test_a_claim_that_fails_before_its_marker_reports_the_approval_unused(self):
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": OSError("read-only state folder")}})
        self.assertIn("The approval was not used; you can try again", message)
        self.assertIs(failed[-1]["approval_used"], False)
        self.assertEqual(self.app.jobs.started, [])

    def test_a_failed_apply_audit_starts_nothing_and_says_so(self):
        real = App.audit

        def audit(app, user, action, **details):
            if action in ("apply", "apply_start_failed"):
                raise OSError("disk full")
            return real(app, user, action, **details)
        message, _ = self.apply_failure(**{"bridge.webui.app.App.audit": {"autospec": True, "side_effect": audit}})
        self.assertIn("was not started: preparing the run failed (disk full). Nothing ran on the server", message)
        self.assertIn("The approval was used and cannot be used again", message)
        self.assertIn("could not be written to the audit log either (disk full)", message)
        self.assertEqual(self.app.jobs.started, [])
        self.assertEqual(self.app.actions, {})

    def test_a_refused_claim_is_passed_on_unchanged(self):
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": ApprovalError("Approval has already been used.")}})
        self.assertEqual(message, "Approval has already been used.")
        self.assertEqual(failed, [])

    # --- round 7 (Codex review of round 6) --------------------------------

    def marker_dir(self):
        return Path(os.environ["XDG_STATE_HOME"]) / "developer-server-setup-assistant/approvals" / self.APID

    def lock_dir(self, folder):
        folder.chmod(0o000)
        self.addCleanup(folder.chmod, 0o700)

    def test_an_approval_audit_error_after_the_marker_reports_it_used(self):
        marker = self.marker_dir()

        def claim_then_audit_fails(*args, **kwargs):
            marker.mkdir(parents=True)
            (marker / "consumed").write_text("now\n")
            error = ApprovalError("approval audit failed")
            error.approval_consumed = True  # as consume_approval marks it (round 8)
            raise error
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": claim_then_audit_fails}})
        self.assertIn("claiming the approval failed (approval audit failed)", message)
        self.assertIn("The approval was used and cannot be used again", message)
        self.assertIs(failed[-1]["approval_used"], True)
        self.assertEqual(self.app.jobs.started, [])
        self.assertTrue((marker / "consumed").exists(), "single use kept")

    # --- round 8 -----------------------------------------------------------

    def test_a_concurrent_consumer_that_wins_keeps_already_used(self):
        marker = self.marker_dir()

        def another_consumer_wins(*args, **kwargs):
            marker.mkdir(parents=True)
            (marker / "consumed").write_text("other\n")
            raise ApprovalError("Approval has already been used.")
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": another_consumer_wins}})
        self.assertEqual((message, failed), ("Approval has already been used.", []))
        self.assertEqual(self.app.jobs.started, [])
        self.assertEqual((marker / "consumed").read_text(), "other\n", "single use kept")

    def stick(self):
        """Real permissions (non-root): the folder and its parent read-only,
        so neither deleting nor moving it aside works."""

        for folder in (self.config.home / "temporary/tmp1", self.config.home / "temporary"):
            folder.chmod(0o500)
            self.addCleanup(folder.chmod, 0o700)

    def repair(self):
        for folder in (self.config.home / "temporary", self.config.home / "temporary/tmp1"):
            folder.chmod(0o700)

    def end_with_a_stuck_folder(self):
        """End access whose key is removed but whose folder cannot be
        deleted or moved aside."""

        self.stick()
        with mock.patch("bridge.webui.enrol.end_temporary_access", return_value=True):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
        self.assertTrue((self.config.home / "temporary/tmp1/meta.json").exists())
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "temp_access_removed" in l][-1]
        self.assertIn("could not be deleted or moved aside", line["leftover"])

    def restart(self):
        self.app = App(self.config, jobs=FakeJobs())
        self.httpd.RequestHandlerClass.app = self.app  # sessions are lost, as on a real restart
        return self.app

    def test_a_restart_never_reloads_an_ended_connection_with_a_stuck_folder(self):
        self.connect(self.login())
        tag = self.tag()
        self.end_with_a_stuck_folder()
        self.assertTrue((self.config.home / "temporary-ended" / tag).exists())
        self.assertNotIn("tmp1", self.restart().temporary)

    def test_without_its_ended_record_the_audit_line_still_keeps_it_ended(self):
        self.connect(self.login())
        with mock.patch.object(App, "mark_ended", return_value=OSError("read-only")):
            self.end_with_a_stuck_folder()
        self.assertFalse((self.config.home / "temporary-ended").exists())
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "temp_access_removed" in l][-1]
        self.assertEqual(line["ended_record_error"], "read-only")
        self.assertNotIn("tmp1", self.restart().temporary)

    def test_after_a_restart_and_repaired_permissions_the_name_connects_again(self):
        cookie = self.login()
        self.connect(cookie)
        self.end_with_a_stuck_folder()
        self.restart()
        cookie = self.login()
        response, body, temp, _ = self.connect(cookie)
        self.assertEqual(response.status, 400, "still stuck: refused clearly")
        temp.assert_not_called()
        self.assertNotIn("tmp1", self.app.temporary)
        self.assertIn('"temp_leftover_blocked"', self.config.audit.read_text())
        self.repair()
        response, _, temp, _ = self.connect(cookie)
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")
        temp.assert_called_once()
        self.assertIn('"temp_leftover_cleared"', self.config.audit.read_text())
        fresh = self.tag()
        self.assertNotIn(fresh, self.app.ended_connections())
        self.assertEqual(self.restart().temporary["tmp1"]["tag"], fresh, "the new connection reloads")

    def test_a_restart_keeps_a_failed_removal_for_its_retry(self):
        self.connect(self.login())
        tag = self.tag()
        self.end_result = False
        self.assertFalse(self.app.end_temporary("alice", "tmp1", "signed out", tag))
        self.assertNotIn(tag, self.app.ended_connections())
        self.assertEqual(self.app.cleanup_intents(), set(), "answered 'not removed': stays active")
        self.assertEqual(self.restart().temporary["tmp1"]["tag"], tag, "reloaded for the retry")
        self.end_result = True
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "expiry", tag))
        self.assertNotIn("tmp1", self.app.temporary)

    # --- round 9: a stop between removing the key and recording it ----------

    class Stopped(BaseException):
        """DSSA stopping mid-removal: no except or audit line runs for it."""

    def stop_after_removing(self, folder, runner=None):
        self.removed.append(Path(folder).name)  # the server removed the key line...
        raise self.Stopped()                    # ...and DSSA stopped before its folder, record or audit

    def interrupted_removal(self):
        cookie = self.login()
        self.connect(cookie)
        tag = self.tag()
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=self.stop_after_removing):
            with self.assertRaises(self.Stopped):
                self.app.end_temporary("alice", "tmp1", "signed out", tag)
        folder = self.config.home / "temporary/tmp1"
        self.assertTrue((folder / "meta.json").exists(), "the original folder is still there")
        self.assertEqual(self.app.cleanup_intents(), {tag}, "the intent was saved before the server was asked")
        self.assertNotIn(tag, self.app.ended_connections(), "no ended record, no audit line")
        return tag

    def test_the_cleanup_intent_is_saved_before_the_server_is_asked(self):
        self.connect(self.login())
        tag = self.tag()
        seen = []

        def removal(folder, runner=None):
            seen.append(set(self.app.cleanup_intents()))
            return self.fake_end(folder, runner)
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=removal):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", tag))
        self.assertEqual(seen, [{tag}])
        self.assertEqual(self.app.cleanup_intents(), set(), "done: the ended record speaks for it")
        self.assertIn(tag, self.app.ended_connections())

    def test_without_a_saved_intent_nothing_is_removed(self):
        self.connect(self.login())
        with mock.patch.object(App, "mark_cleanup", return_value=OSError("read-only")):
            self.assertFalse(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
        self.assertEqual(self.removed, [], "the server was not asked")
        self.assertIn("tmp1", self.app.temporary)
        self.assertNotIn("ending", self.app.temporary["tmp1"])
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "remove_failed" in l][-1]
        self.assertIn("could not be recorded before removing: read-only", line["error"])

    def test_a_removal_interrupted_after_the_key_went_is_retried_after_a_restart(self):
        tag = self.interrupted_removal()
        app = self.restart()
        self.assertNotIn("tmp1", app.temporary, "never active again")
        self.assertEqual(app.cleanup_pending["tmp1"]["tag"], tag, "kept for the retry")
        self.assertNotIn("tmp1", app.temporary_of("alice"))
        with self.assertRaisesRegex(ValueError, "still being removed"):
            app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        app.sweep_temporary(app.sessions.active_users())
        self.assertEqual(self.removed, ["tmp1", "tmp1"], "removed again (idempotent on the server)")
        self.assertEqual(app.cleanup_pending, {})
        self.assertFalse((self.config.home / "temporary/tmp1").exists())
        self.assertEqual(app.cleanup_intents(), set())
        self.assertIn(tag, app.ended_connections())
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "temp_access_removed" in l][-1]
        self.assertEqual((line["connection"], line["resumed"], line["reason"]),
                         (tag, True, "cleanup resumed after a restart"))
        response, _, temp, _ = self.connect(self.login())  # the same name connects again
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")
        fresh = self.tag()
        self.assertNotEqual(fresh, tag)
        self.assertEqual(self.restart().temporary["tmp1"]["tag"], fresh, "the new connection is active")

    def test_a_resumed_removal_that_fails_stays_pending_and_retryable(self):
        tag = self.interrupted_removal()
        app = self.restart()
        self.end_result = False
        app.sweep_temporary(set())
        self.assertIn("tmp1", app.cleanup_pending, "still pending, still not active")
        self.assertNotIn("ending", app.cleanup_pending["tmp1"])
        self.assertEqual(app.cleanup_intents(), {tag})
        self.assertNotIn("tmp1", self.restart().temporary, "and so after another restart")
        app = self.app
        self.assertIn("tmp1", app.cleanup_pending)
        self.end_result = True
        app.sweep_temporary(set())
        self.assertEqual((app.cleanup_pending, app.cleanup_intents()), ({}, set()))
        response, _, _, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")

    def test_a_pending_removal_whose_key_expired_is_discarded(self):
        self.interrupted_removal()
        app = self.restart()
        app.cleanup_pending["tmp1"]["expires"] = int(time.time()) - 1
        self.end_result = False
        app.sweep_temporary(set())
        self.assertEqual(app.cleanup_pending, {})
        self.assertFalse((self.config.home / "temporary/tmp1").exists())
        self.assertIn('"temp_access_expired"', self.config.audit.read_text())

    # --- round 10: lost replies, overlapping retries, unrecorded retirement --

    def lost(self, folder, runner=None):
        self.removed.append(Path(folder).name)
        raise enrol.RemovalUnknown("no answer from the server: timed out")

    def test_a_lost_reply_keeps_the_cleanup_pending(self):
        self.connect(self.login())
        tag = self.tag()
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=self.lost):
            self.assertFalse(self.app.end_temporary("alice", "tmp1", "ended by the user", tag))
        self.assertNotIn("tmp1", self.app.temporary, "the key may be gone: not active")
        self.assertEqual(self.app.cleanup_pending["tmp1"]["tag"], tag)
        self.assertNotIn("ending", self.app.cleanup_pending["tmp1"])
        self.assertEqual(self.app.cleanup_intents(), {tag})
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines()
                if '"temp_access_remove_unknown"' in l][-1]
        self.assertEqual((line["pending"], line["error"]), (True, "no answer from the server: timed out"))
        with self.assertRaisesRegex(ValueError, "still being removed"):
            self.app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        self.assertIn("tmp1", self.restart().cleanup_pending, "and after a restart")
        self.app.sweep_temporary(set())  # the server answers now
        self.assertEqual((self.app.cleanup_pending, self.app.cleanup_intents()), ({}, set()))
        response, _, _, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")

    def test_a_confirmed_refusal_stays_active_and_retryable(self):
        self.connect(self.login())
        tag = self.tag()
        self.end_result = False
        self.assertFalse(self.app.end_temporary("alice", "tmp1", "ended by the user", tag))
        self.assertIn("tmp1", self.app.temporary)
        self.assertEqual((self.app.cleanup_pending, self.app.cleanup_intents()), ({}, set()))
        self.end_result = True
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "ended by the user", tag))

    def test_the_intent_is_settled_before_a_retry_can_start(self):
        """A retry of the same tag must never start between clearing "ending"
        and settling the intent: it would write its own intent, which the
        first caller's settlement would then delete."""

        self.connect(self.login())
        tag = self.tag()
        calls, reached, seen, results = [], threading.Event(), [], []

        def removal(folder, runner=None):
            calls.append(Path(folder).name)
            if len(calls) == 1:
                return False  # the first removal is refused: its intent is dropped
            seen.append(tag in self.app.cleanup_intents())  # the retry's own intent
            reached.set()
            __import__("shutil").rmtree(folder, ignore_errors=True)
            return True
        retry = threading.Thread(target=lambda: results.append(
            self.app.end_temporary("alice", "tmp1", "signed out", tag)))
        real_drop = App.drop_cleanup

        def drop_while_a_retry_tries_to_start(app, connection):
            if not retry.is_alive() and not results:
                self.assertTrue(app.lock.locked(), "settled under the lock")
                self.assertTrue(app.temporary["tmp1"].get("ending"), "before ending is cleared")
                retry.start()
                self.assertFalse(reached.wait(0.5), "a retry started before the intent was settled")
            return real_drop(app, connection)
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=removal), \
             mock.patch.object(App, "drop_cleanup", autospec=True, side_effect=drop_while_a_retry_tries_to_start):
            self.assertFalse(self.app.end_temporary("alice", "tmp1", "ended by the user", tag))
            retry.join(5)
        self.assertEqual((calls, results, seen), (["tmp1", "tmp1"], [True], [True]),
                         "the retry ran afterwards, and its intent was not deleted under it")
        self.assertEqual(self.app.cleanup_intents(), set())
        self.assertNotIn("tmp1", self.app.temporary)

    def test_overlapping_retries_of_a_pending_cleanup_remove_once(self):
        self.connect(self.login())
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=self.lost):
            self.app.end_temporary("alice", "tmp1", "signed out", self.tag())
        inside, release = threading.Event(), threading.Event()

        def slow(folder, runner=None):
            inside.set()
            release.wait(5)
            return self.fake_end(folder, runner)
        with mock.patch("bridge.webui.enrol.end_temporary_access", side_effect=slow):
            first = threading.Thread(target=self.app.resume_cleanup)
            first.start()
            self.assertTrue(inside.wait(5))
            self.app.resume_cleanup()  # overlaps: backs off while the first runs
            release.set()
            first.join(5)
        self.assertEqual(self.removed, ["tmp1", "tmp1"], "the lost one, then one retry")
        self.assertEqual((self.app.cleanup_pending, self.app.cleanup_intents()), ({}, set()))

    def test_unrecorded_retirement_with_its_folder_left_keeps_the_name_across_restart(self):
        self.connect(self.login())
        tag = self.tag()
        self.stick()
        with mock.patch("bridge.webui.enrol.end_temporary_access", return_value=True), \
             mock.patch.object(App, "mark_ended", return_value=OSError("read-only")):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", tag), "the key is gone")
        self.assertTrue((self.config.home / "temporary/tmp1/meta.json").exists())
        self.assertEqual(self.app.cleanup_intents(), {tag}, "kept")
        self.assertIn("tmp1", self.app.cleanup_pending)
        self.assertIn(tag, self.app.ended_connections(), "its audit line exists")
        app = self.restart()
        self.assertNotIn("tmp1", app.temporary)
        self.assertIn("tmp1", app.cleanup_pending, "the intent wins over the audit line")
        with self.assertRaisesRegex(ValueError, "still being removed"):
            app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        self.repair()
        before = list(self.removed)
        app.sweep_temporary(set())
        self.assertEqual(self.removed, before, "already removed: the server is not asked again")
        self.assertEqual((app.cleanup_pending, app.cleanup_intents()), ({}, set()))
        self.assertTrue((self.config.home / "temporary-ended" / tag).exists())
        self.assertFalse((self.config.home / "temporary/tmp1").exists())
        response, _, _, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")

    def test_unrecorded_retirement_without_a_folder_left_is_done(self):
        self.connect(self.login())
        with mock.patch.object(App, "mark_ended", return_value=OSError("read-only")):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
        self.assertEqual((self.app.cleanup_pending, self.app.cleanup_intents()), ({}, set()))

    # --- round 11: unreadable metadata; retirement without SSH -------------

    def meta_file(self):
        return self.config.home / "temporary/tmp1/meta.json"

    def test_an_unreadable_meta_keeps_its_intent_and_name_across_restart(self):
        tag = self.interrupted_removal()
        self.meta_file().chmod(0o000)
        self.addCleanup(lambda: self.meta_file().exists() and self.meta_file().chmod(0o600))
        app = self.restart()
        self.assertNotIn("tmp1", app.temporary)
        self.assertTrue(app.cleanup_pending["tmp1"]["unreadable"])
        self.assertEqual(app.cleanup_pending["tmp1"]["tag"], tag, "known from its intent")
        self.assertEqual(app.cleanup_pending["tmp1"]["owner"], "alice")
        self.assertEqual(app.cleanup_intents(), {tag}, "the intent is kept")
        with self.assertRaisesRegex(ValueError, "still being removed"):
            app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        before = list(self.removed)
        app.sweep_temporary(set())
        self.assertEqual(self.removed, before, "nothing to log in with yet: the server is not asked")
        self.assertIn("tmp1", self.restart().cleanup_pending, "still reserved after another restart")
        self.meta_file().chmod(0o600)  # repaired
        self.app.sweep_temporary(set())
        self.assertEqual(self.removed, before + ["tmp1"])
        self.assertEqual((self.app.cleanup_pending, self.app.cleanup_intents()), ({}, set()))
        response, _, _, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")

    def test_an_unreadable_meta_without_an_intent_is_reserved_not_active(self):
        self.connect(self.login())
        self.meta_file().chmod(0o000)
        self.addCleanup(lambda: self.meta_file().exists() and self.meta_file().chmod(0o600))
        app = self.restart()
        self.assertNotIn("tmp1", app.temporary)
        self.assertIn("tmp1", app.cleanup_pending)
        with self.assertRaisesRegex(ValueError, "still being removed"):
            app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        self.meta_file().chmod(0o600)
        app.sweep_temporary(set())
        self.assertEqual((app.cleanup_pending, self.removed), ({}, ["tmp1"]))

    def test_an_intent_that_cannot_be_read_is_never_dropped(self):
        tag = self.interrupted_removal()
        intent = self.config.home / "temporary-cleanup" / tag
        intent.chmod(0o000)
        self.addCleanup(intent.chmod, 0o600)
        app = self.restart()
        self.assertIn("tmp1", app.cleanup_pending, "matched through the folder's own meta.json")
        self.assertTrue(intent.exists())

    def test_a_confirmed_removal_is_retired_without_ssh_when_records_fail(self):
        self.connect(self.login())
        tag = self.tag()
        self.stick()
        with mock.patch.object(App, "mark_ended", return_value=OSError("read-only")), \
             mock.patch.object(App, "audit", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "was removed, but the audit log could not be written"):
                self.app.end_temporary("alice", "tmp1", "signed out", tag)
        self.assertEqual(self.removed, ["tmp1"])
        self.assertNotIn(tag, self.app.ended_connections(), "neither record was written")
        self.assertTrue(self.app.cleanup_pending["tmp1"]["retire_only"])
        self.assertIs(self.app.read_cleanup(tag)["removed"], True, "kept in its intent")
        app = self.restart()
        self.assertTrue(app.cleanup_pending["tmp1"]["retire_only"], "and after a restart")
        self.repair()
        app.sweep_temporary(set())
        self.assertEqual(self.removed, ["tmp1"], "retired locally; the server was not asked again")
        self.assertEqual((app.cleanup_pending, app.cleanup_intents()), ({}, set()))
        self.assertIn(tag, app.ended_connections())
        response, _, _, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")

    # --- round 12: unreadable intents; invalid meta.json -------------------

    def test_an_unreadable_intent_blocks_ssh_and_stays_intact(self):
        tag = self.interrupted_removal()
        intent = self.config.home / "temporary-cleanup" / tag
        content = intent.read_bytes()
        intent.chmod(0o000)
        self.addCleanup(lambda: intent.exists() and intent.chmod(0o600))
        app = self.restart()
        before = list(self.removed)
        app.sweep_temporary(set())
        app.sweep_temporary(set())
        self.assertEqual(self.removed, before, "no retry while its intent cannot be read")
        self.assertIn("tmp1", app.cleanup_pending)
        intent.chmod(0o600)
        self.assertEqual(intent.read_bytes(), content, "never overwritten")
        app.sweep_temporary(set())  # readable again: reread, then retried
        self.assertEqual(self.removed, before + ["tmp1"])
        self.assertEqual((app.cleanup_pending, app.cleanup_intents()), ({}, set()))

    def test_a_broken_intent_is_reread_and_can_turn_the_retry_local(self):
        tag = self.interrupted_removal()
        intent = self.config.home / "temporary-cleanup" / tag
        intent.write_text("{not json")
        app = self.restart()
        before = list(self.removed)
        app.sweep_temporary(set())
        self.assertEqual((self.removed, intent.read_text()), (before, "{not json"), "no SSH, intact")
        repaired = {"target": "tmp1", "connection": tag, "owner": "alice", "removed": True}
        intent.write_text(json.dumps(repaired))  # says the key was already removed
        app.sweep_temporary(set())
        self.assertEqual(self.removed, before, "retired locally: still no SSH")
        self.assertEqual(app.cleanup_pending, {})
        self.assertIn(tag, app.ended_connections())

    def test_a_non_object_meta_keeps_the_name_through_startup_and_sweep(self):
        for broken in ("[]", '"tmp1"', "42", '{"target_id": "other"}', '{"target_id": "tmp1"}'):
            with self.subTest(meta=broken):
                self.app.temporary.clear()
                self.app.cleanup_pending.clear()
                self.connect(self.login())
                good = self.meta_file().read_text()
                self.meta_file().write_text(broken)
                app = self.restart()  # never crashes
                self.assertNotIn("tmp1", app.temporary)
                self.assertTrue(app.cleanup_pending["tmp1"]["unreadable"])
                app.sweep_temporary(set())
                self.assertIn("tmp1", app.cleanup_pending, "still reserved after a sweep")
                with self.assertRaisesRegex(ValueError, "still being removed"):
                    app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
                self.meta_file().write_text(good)  # repaired
                app.sweep_temporary(set())
                self.assertEqual(app.cleanup_pending, {})
                self.assertFalse((self.config.home / "temporary/tmp1").exists())

    def test_an_approval_used_earlier_is_refused_unchanged(self):
        self.marker_dir().mkdir(parents=True)
        (self.marker_dir() / "consumed").write_text("earlier\n")
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": ApprovalError("Approval has already been used.")}})
        self.assertEqual((message, failed), ("Approval has already been used.", []))

    def test_an_unreadable_marker_before_the_claim_uses_nothing(self):
        self.marker_dir().mkdir(parents=True)
        self.lock_dir(self.marker_dir())
        message, failed = self.apply_failure()
        self.consume.assert_not_called()
        self.assertIn("checking the approval failed (its single-use marker cannot be read", message)
        self.assertIn("The approval was not used", message)
        self.assertIs(failed[-1]["approval_used"], False)
        self.assertEqual(self.app.jobs.started, [])

    def test_an_unreadable_marker_after_a_failed_claim_counts_as_maybe_used(self):
        marker = self.marker_dir()

        def claim_then_lose_the_marker(*args, **kwargs):
            marker.mkdir(parents=True)
            (marker / "consumed").write_text("now\n")
            self.lock_dir(marker)
            raise OSError("fsync failed")  # from writing the marker: not flagged
        message, failed = self.apply_failure(
            **{"bridge.webui.app.consume_approval": {"side_effect": claim_then_lose_the_marker}})
        self.assertIn("the marker cannot be read", message)
        self.assertIn("could not tell whether the approval was used; treat it as used", message)
        self.assertIsNone(failed[-1]["approval_used"])
        self.assertEqual(self.app.jobs.started, [])

    def test_a_folder_that_cannot_be_deleted_is_moved_aside(self):
        self.connect(self.login())
        folder = self.config.home / "temporary/tmp1"
        with mock.patch("bridge.webui.enrol.end_temporary_access", return_value=True), \
             mock.patch("bridge.webui.enrol.discard_temporary_access"), \
             mock.patch("bridge.webui.app.shutil.rmtree"):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
        self.assertFalse(folder.exists(), "out of the name's way")
        aside = list((self.config.home / "temporary-leftover").iterdir())
        self.assertEqual(len(aside), 1)
        self.assertTrue(aside[0].name.startswith("tmp1-"))
        line = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "temp_access_removed" in l][-1]
        self.assertIn(str(aside[0]), line["leftover"])
        self.app.load_temporary()
        self.assertNotIn("tmp1", self.app.temporary, "never loaded as a connection")
        response, _, temp, _ = self.connect(self.login())
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps", "the name connects again")

    def test_a_leftover_folder_blocks_nothing_once_it_can_be_cleared(self):
        self.connect(self.login())
        folder = self.config.home / "temporary/tmp1"
        with mock.patch("bridge.webui.enrol.end_temporary_access", return_value=True), \
             mock.patch("bridge.webui.enrol.discard_temporary_access"), \
             mock.patch("bridge.webui.app.os.rename", side_effect=OSError("read-only")):
            self.assertTrue(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
            self.assertTrue(folder.exists())
            self.assertNotIn("tmp1", self.app.temporary)
            with self.assertRaisesRegex(ValueError, "earlier connection to tmp1 could not be cleared"):
                self.app.add_temporary("alice", {"name": "tmp1"}, "pw", "SHA256:x")
        self.assertEqual(self.app.connecting, set(), "the reservation is released")
        audit = self.config.audit.read_text()
        self.assertIn("could not be deleted or moved aside (read-only)", audit)
        self.assertIn('"temp_leftover_blocked"', audit)
        response, _, temp, _ = self.connect(self.login())  # deleting works again
        self.assertEqual(response.getheader("Location"), "/servers/tmp1/apps")
        self.assertIn('"temp_leftover_cleared"', self.config.audit.read_text())
        self.assertEqual(self.app.temporary["tmp1"]["owner"], "alice")

    def test_a_failed_removal_keeps_its_folder_for_the_retry(self):
        self.connect(self.login())
        self.end_result = False
        self.assertFalse(self.app.end_temporary("alice", "tmp1", "signed out", self.tag()))
        self.assertTrue((self.config.home / "temporary/tmp1/meta.json").exists())
        self.assertFalse((self.config.home / "temporary-leftover").exists())

    def test_a_confirmed_stop_is_reported_as_stopped(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        process = mock.Mock(pid=4242, returncode=-9)
        job = jobs.Job("apply", ["bash"], {}, "p", "tmp1", "alice")
        with mock.patch("bridge.webui.jobs.subprocess.Popen", return_value=process), \
             mock.patch("bridge.webui.jobs.os.killpg"), \
             mock.patch("bridge.webui.jobs.threading.Thread", side_effect=RuntimeError("no threads")):
            with self.assertRaises(jobs.JobStartError) as raised:
                job.start()
        self.assertEqual((raised.exception.launched, raised.exception.stopped), (True, True))
        self.assertIn("so DSSA stopped it", job.view()["text"])

    def test_a_raising_teardown_keeps_launched_and_the_finished_state(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        job = jobs.Job("apply", ["bash"], {}, "p", "tmp1", "alice")
        with mock.patch("bridge.webui.jobs.subprocess.Popen", return_value=mock.Mock(pid=4242)), \
             mock.patch("bridge.webui.jobs.threading.Thread", side_effect=RuntimeError("no threads")), \
             mock.patch.object(jobs.Job, "_teardown", side_effect=AttributeError("no stdout")), \
             mock.patch.object(jobs.Job, "fail", side_effect=RuntimeError("output broken")):
            with self.assertRaises(jobs.JobStartError) as raised:
                job.start()
        self.assertEqual((raised.exception.launched, raised.exception.stopped), (True, False))
        self.assertEqual((job.code, job.finished is not None), (127, True))
        self.assertFalse(job.view()["running"])

    def test_an_unconfirmed_stop_is_never_called_stopped(self):
        jobs = __import__("bridge.webui.jobs", fromlist=["Jobs"])
        error = jobs.JobStartError("lost the pty", launched=True, stopped=False)
        stack, plan = self.action_setup()
        with stack:
            self.patches(stack, plan)
            self.app.jobs = mock.Mock(start=mock.Mock(side_effect=error), recent=lambda limit=20: [])
            with self.assertRaises(ApprovalError) as raised:
                self.app.start_apply("alice", self.APID)
        self.assertIn("could not confirm that it stopped (lost the pty)", str(raised.exception))
        self.assertNotIn("stopped it", str(raised.exception))
        last = [json.loads(l) for l in self.config.audit.read_text().splitlines() if "_start_failed" in l][-1]
        self.assertEqual((last["launched"], last["stopped"]), (True, False))

    def test_preflight_uses_the_captured_connection(self):
        self.connect(self.login())
        action = SimpleNamespace(host="203.0.113.5", access={"known_hosts": "/captured/known_hosts"})
        self.app.temporary["tmp1"] = dict(self.app.temporary["tmp1"], host="198.51.100.7")
        plan = {"target": {"target_id": "tmp1"}}
        with mock.patch("bridge.components.run.load_plan", return_value=(b"{}", plan, Path("/x"))), \
             mock.patch("bridge.webui.app.known_host_fingerprints", return_value={"SHA256:x"}) as keys, \
             mock.patch("bridge.webui.app.preflight_plan", return_value={}):
            self.app.check(self.APID, action)
        keys.assert_called_once_with("203.0.113.5", known_hosts="/captured/known_hosts")

    def test_a_saved_server_plan_never_acts_through_a_temporary_connection(self):
        cookie = self.login()
        self.connect(cookie)
        with self.assertRaisesRegex(Exception, "not made on the temporary server tmp1"):
            self.app.require_plan_owner("alice", "99999999-2222-4333-8444-555555555555", "tmp1")

    def test_only_the_user_who_started_a_run_types_into_it(self):
        """Lab finding (items 2–3): anyone signed in could answer another run's sudo prompt."""
        run = mock.Mock(id="0123456789abcdef", target="web1", plan_id="p", user="alice", finished=None)
        self.app.jobs = mock.Mock(get=lambda job_id: run, recent=lambda limit=20: [run])
        with mock.patch.object(App, "targets", return_value=[{"target_id": "web1"}]):
            other = self.login_as("bob", "another long password")
            response, body = self.request("POST", "/jobs/0123456789abcdef/input",
                                          f"csrf={self.token(other)}&line=secret", other)
            self.assertEqual(response.status, 400)
            self.assertIn("Only alice, who started this run, can type into it.", body)
            run.send.assert_not_called()
            cookie = self.login()
            response, _ = self.request("POST", "/jobs/0123456789abcdef/input",
                                       f"csrf={self.token(cookie)}&line=pw", cookie)
            self.assertEqual(response.status, 200)
            run.send.assert_called_once_with("pw")

    def test_logout_is_audited(self):
        cookie = self.login()
        self.request("POST", "/logout", f"csrf={self.token(cookie)}", cookie)
        self.assertIn('"action": "logout"', self.config.audit.read_text())



class LegacyPlanTests(HttpBase):
    """Plans made on temporary servers before ownership records existed
    (Codex follow-up, 2026-11-26): restored from the audit log where it is
    reliable, restricted where it is not, repeatable, never inherited."""

    IDS = {name: f"{i}{i}{i}{i}{i}{i}{i}{i}-1111-4111-8111-111111111111"
           for i, name in enumerate(["owned", "other_user", "cli", "saved", "gone", "overlap", "after"], 1)}

    def setUp(self):
        super().setUp()
        self.state = Path(os.environ["XDG_STATE_HOME"])
        plans = self.state / "developer-server-setup-assistant/plans"
        plans.mkdir(parents=True)
        times = {"owned": ("tmp1", "T03:10"), "other_user": ("tmp1", "T03:20"), "cli": ("tmp1", "T03:25"),
                 "saved": ("web1", "T03:30"), "gone": ("old1", "T03:35"), "overlap": ("tmp2", "T05:10"),
                 "after": ("tmp1", "T04:30")}
        for name, (target, at) in times.items():
            (plans / f"{self.IDS[name]}.json").write_text(json.dumps(
                {"plan_id": self.IDS[name], "target": {"target_id": target}, "created_at": f"2026-11-26{at}:00Z"}))
        lines = [
            {"action": "temp_access_created", "target": "tmp1", "user": "alice",
             "time": "2026-11-26T03:00:00Z", "expires": "2026-11-26T04:00:00Z"},
            self.plan_line("owned", "tmp1", "alice", "T03:10"),
            self.plan_line("other_user", "tmp1", "bob", "T03:20"),
            self.plan_line("saved", "web1", "bob", "T03:30"),
            self.plan_line("gone", "old1", "bob", "T03:35"),
            {"action": "temp_access_removed", "target": "tmp1", "user": "alice", "time": "2026-11-26T03:40:00Z"},
            # Never closed, then the name connected again before it expired.
            {"action": "temp_access_created", "target": "tmp2", "user": "alice",
             "time": "2026-11-26T05:00:00Z", "expires": "2026-11-26T06:00:00Z"},
            {"action": "temp_access_created", "target": "tmp2", "user": "alice",
             "time": "2026-11-26T05:05:00Z", "expires": "2026-11-26T06:05:00Z"},
            self.plan_line("overlap", "tmp2", "alice", "T05:10"),
            self.plan_line("after", "tmp1", "alice", "T04:30"),
        ]
        self.config.home.mkdir(parents=True, exist_ok=True)
        self.config.audit.write_text("".join(json.dumps(l) + "\n" for l in lines))
        self.saved = [{"target_id": "web1"}]
        patcher = mock.patch.object(App, "targets", side_effect=lambda: list(self.saved))
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan_line(self, name, target, user, at):
        return {"action": "plan", "plan_id": self.IDS[name], "target": target, "user": user,
                "time": f"2026-11-26{at}:00Z"}

    def test_the_upgrade_restores_or_restricts_each_plan(self):
        app = App(self.config)
        owners = {name: app.plan_owners.get(pid) for name, pid in self.IDS.items()}
        self.assertEqual(owners["owned"], {"owner": "alice", "connection": None, "target": "tmp1",
                                           "source": "legacy-audit"})
        for name in ("other_user", "cli", "gone", "overlap", "after"):
            with self.subTest(name=name):
                self.assertEqual(owners[name]["source"], "ambiguous")
                self.assertIsNone(owners[name]["owner"])
        self.assertIsNone(owners["saved"], "saved-server plans stay shared, as before")
        record = self.config.home / "temporary-plans" / f"{self.IDS['owned']}.json"
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        upgraded = [json.loads(l) for l in self.config.audit.read_text().splitlines()
                    if '"plan_owner_upgraded"' in l]
        self.assertEqual(len(upgraded), 6)

    def test_the_upgrade_is_repeatable_and_never_overwrites(self):
        App(self.config)
        record = self.config.home / "temporary-plans" / f"{self.IDS['owned']}.json"
        record.write_text(json.dumps({"owner": "alice", "connection": "dssa-temp-" + "1" * 16,
                                      "target": "tmp1", "source": "bound"}))
        restarted = App(self.config)
        self.assertEqual(restarted.upgrade_legacy_plans(), {}, "nothing left to do")
        self.assertEqual(restarted.plan_owners[self.IDS["owned"]]["source"], "bound")
        self.assertEqual(sum('"plan_owner_upgraded"' in l for l in self.config.audit.read_text().splitlines()), 6)

    def test_legacy_plans_survive_restart_and_name_reuse(self):
        App(self.config)
        app = App(self.config)  # a restart
        owned, unclear = self.IDS["owned"], self.IDS["cli"]
        # Bob reuses the name as a temporary server, then as a saved one.
        app.temporary["tmp1"] = {"owner": "bob", "tag": "dssa-temp-" + "b" * 16, "target_id": "tmp1"}
        self.assertTrue(app.may_see("alice", "tmp1", owned))
        self.assertFalse(app.may_see("bob", "tmp1", owned))
        self.assertFalse(app.may_see("bob", "tmp1", owned, "bob"))
        app.temporary.pop("tmp1")
        self.saved.append({"target_id": "tmp1"})
        self.assertFalse(app.may_see("bob", "tmp1", owned), "a saved server never inherits it either")
        for user in ("alice", "bob"):
            self.assertFalse(app.may_see(user, "tmp1", unclear), "ambiguous: nobody sees it")
        # No action: the old connection has ended, or nobody is known.
        app.temporary["tmp1"] = {"owner": "alice", "tag": "dssa-temp-" + "a" * 16, "target_id": "tmp1"}
        with self.assertRaisesRegex(Exception, "before ownership was recorded"):
            app.require_plan_owner("alice", owned, "tmp1")
        with self.assertRaisesRegex(Exception, "Nobody can use this plan"):
            app.require_plan_owner("alice", unclear, "tmp1")
        with self.assertRaisesRegex(Exception, "another user's temporary server"):
            app.require_plan_owner("bob", owned, "tmp1")
        self.assertTrue(app.may_see("bob", "web1", self.IDS["saved"]), "saved servers stay shared")

    def test_the_dry_run_command_writes_nothing(self):
        from bridge.webui.__main__ import main
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DSSA_WEBUI_HOME": str(self.config.home)}), \
             mock.patch("sys.stdout", out):
            self.assertEqual(main(["plan-owners", "--dry-run"]), 0)
        self.assertIn("6 plan(s) would be recorded", out.getvalue())
        self.assertIn(f"{self.IDS['owned']}  tmp1", out.getvalue())
        self.assertFalse((self.config.home / "temporary-plans").exists())
        with mock.patch.dict(os.environ, {"DSSA_WEBUI_HOME": str(self.config.home)}), \
             mock.patch("sys.stdout", io.StringIO()):
            main(["plan-owners"])
        self.assertEqual(len(list((self.config.home / "temporary-plans").iterdir())), 6)

    # --- reading a historical plan (Codex review of 839a377) ---------------

    def full_plan(self, name, target="tmp1"):
        return {"plan_id": self.IDS[name], "operation": "install", "package_manager": "apt",
                "created_at": "2026-11-26T03:10:00Z", "runnable": ["haproxy"],
                "target": {"target_id": target, "host_key_sha256": "SHA256:x"},
                "steps": [{"component": "haproxy", "action": "INSTALL", "variant_id": "apt",
                           "reasons": ["Not installed."]}]}

    def sign_in(self, user, password):
        response, _ = self.request("POST", "/login", f"user={user}&password={password.replace(' ', '+')}")
        return response.getheader("Set-Cookie").split(";")[0]

    def upgraded(self):
        self.app.load_plan_owners()
        self.app.upgrade_legacy_plans()

    def no_preflight(self):
        return mock.patch.object(App, "check", side_effect=AssertionError("no SSH or preflight for history"))

    def test_the_owner_reads_a_legacy_plan_after_its_connection_ended(self):
        self.upgraded()
        plan = self.full_plan("owned")
        runs = self.state / "developer-server-setup-assistant/runs" / self.IDS["owned"]
        runs.mkdir(parents=True)
        (runs / "summary.json").write_text(json.dumps(
            {"status": "completed", "results": [{"component": "haproxy", "outcome": "installed"}]}))
        cookie = self.login()
        with self.stored(plan), self.no_preflight():
            response, body = self.request("GET", f"/plans/{self.IDS['owned']}", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("<dt>Server</dt><dd>tmp1</dd>", body)
        self.assertIn("haproxy", body)
        self.assertIn("Already run: status <b>completed</b> (haproxy: installed)", body)
        self.assertIn("earlier temporary connection to <b>tmp1</b> before ownership was recorded", body)
        for action in ("approve", "apply", "report", "resume"):
            self.assertNotIn(f"/plans/{self.IDS['owned']}/{action}", body, "read only")

    def test_a_bound_plan_is_read_only_once_its_connection_ended(self):
        plan_id = self.IDS["owned"]
        self.app.write_plan_owner(plan_id, {"owner": "alice", "connection": "dssa-temp-" + "c" * 16,
                                            "target": "tmp1", "source": "bound"})
        cookie = self.login()
        with self.stored(self.full_plan("owned")), self.no_preflight():
            response, body = self.request("GET", f"/plans/{plan_id}", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("earlier temporary connection to <b>tmp1</b>, which has ended", body)

    def test_another_user_gets_404_even_after_reusing_the_name(self):
        self.upgraded()
        self.app.users.add("bob", "another long password")
        bob = self.sign_in("bob", "another long password")
        alice = self.login()
        self.app.temporary["tmp1"] = {"owner": "bob", "tag": "dssa-temp-" + "b" * 16, "target_id": "tmp1",
                                      "host": "203.0.113.9", "entry": {"target_id": "tmp1"}}
        with self.stored(self.full_plan("owned")), self.no_preflight():
            response, body = self.request("GET", f"/plans/{self.IDS['owned']}", cookie=bob)
            self.assertEqual(response.status, 404)
            self.assertNotIn("haproxy", body)
            # The owner still reads it, without using Bob's connection.
            response, body = self.request("GET", f"/plans/{self.IDS['owned']}", cookie=alice)
            self.assertEqual(response.status, 200)
        self.app.temporary.pop("tmp1")
        self.saved.append({"target_id": "tmp1"})
        with self.stored(self.full_plan("owned")), self.no_preflight():
            response, _ = self.request("GET", f"/plans/{self.IDS['owned']}", cookie=bob)
        self.assertEqual(response.status, 404, "a saved server of that name does not inherit it either")

    def test_report_and_other_actions_stay_blocked_for_a_legacy_plan(self):
        self.upgraded()
        cookie = self.login()
        token = re.search(r'name="csrf" value="([^"]+)"', self.request("GET", "/servers/add", cookie=cookie)[1]).group(1)
        self.app.jobs = mock.Mock(start=mock.Mock(side_effect=AssertionError("nothing may start")),
                                  recent=lambda limit=20: [])
        with self.stored(self.full_plan("owned")), self.no_preflight():
            for action in ("report", "resume", "apply", "approve"):
                with self.subTest(action=action):
                    response, body = self.request("POST", f"/plans/{self.IDS['owned']}/{action}",
                                                  f"csrf={token}&reviewed=1", cookie)
                    self.assertEqual(response.status, 400)
                    self.assertIn("before ownership was recorded", body)

    def test_a_stored_plan_without_any_audit_evidence(self):
        self.config.audit.write_text("")
        app = App(self.config)
        self.assertIsNone(app.plan_owners.get(self.IDS["saved"]), "a saved server's plan stays shared")
        self.assertEqual(app.plan_owners[self.IDS["owned"]]["source"], "ambiguous",
                         "tmp1 is not saved: restricted, not guessed")


if __name__ == "__main__":
    unittest.main()
