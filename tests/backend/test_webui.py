"""Web UI on the master server (4.46): login, CSRF, jobs, server enrolment."""

import http.client
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
from unittest import mock

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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
            response, body = self.request("GET", f"/plans/{self.PLAN_ID}", cookie=cookie)
        self.assertIn("Approved by <b>bob</b>; only bob can apply it.", body)
        self.assertNotIn(f'action="/plans/{self.PLAN_ID}/apply"', body)
        (folder / "approval.json").write_text(json.dumps({"approved_by": "alice"}))
        with mock.patch.object(App, "check", return_value=(b"{}", plan, None, [], check)):
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

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] != "ssh":
            return __import__("subprocess").run(argv, **kwargs)
        if self.fail_key_login and "BatchMode=yes" in argv:
            return mock.Mock(returncode=255, stdout="", stderr="Permission denied")
        env = dict(os.environ, HOME=str(self.home), SSH_CLIENT=self.client)
        return __import__("subprocess").run(["bash", "-c", argv[-1]], env=env, capture_output=True, text=True)

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
        self.fail_key_login = True
        self.assertFalse(enrol.end_temporary_access(folder, runner=self.runner))
        self.assertTrue((folder / "meta.json").exists())
        enrol.discard_temporary_access(folder)
        self.assertFalse(folder.exists())

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
        response, _ = self.request("POST", "/servers/tmp1/end", f"csrf={self.token(other)}", other)
        self.assertEqual(response.status, 400)
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
        response, _ = self.request("POST", "/servers/tmp1/end", f"csrf={self.token(cookie)}", cookie)
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
        self.assertFalse(self.app.end_temporary("alice", "tmp1", "test"))
        self.assertIn("tmp1", self.app.temporary)
        self.assertIn('"action": "temp_access_remove_failed"', self.config.audit.read_text())
        self.app.temporary["tmp1"]["expires"] = time.time() - 1
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "test"))
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)), \
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)):
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
        self.assertTrue(self.app.end_temporary("alice", "tmp1", "ended by the user"))
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
        with mock.patch.object(App, "check", return_value=(b"{}", plan, Path("/x"), set(), check)), \
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

    def test_a_stored_plan_without_any_audit_evidence(self):
        self.config.audit.write_text("")
        app = App(self.config)
        self.assertIsNone(app.plan_owners.get(self.IDS["saved"]), "a saved server's plan stays shared")
        self.assertEqual(app.plan_owners[self.IDS["owned"]]["source"], "ambiguous",
                         "tmp1 is not saved: restricted, not guessed")


if __name__ == "__main__":
    unittest.main()
