"""Web UI on the master server (4.46): login, CSRF, jobs, server enrolment."""

import http.client
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


class HttpTests(unittest.TestCase):
    """The real handler on 127.0.0.1, plain HTTP (as behind a reverse proxy)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(Path(self.tmp.name) / "webui")
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

    def test_static_files_only(self):
        response, body = self.request("GET", "/static/app.js")
        self.assertEqual(response.status, 200)
        self.assertIn("poll", body)
        response, _ = self.request("GET", "/static/../auth.py")
        self.assertEqual(response.status, 404)

    def test_plain_http_is_refused_off_loopback(self):
        with self.assertRaises(SystemExit):
            serve(self.app, "0.0.0.0", 0)


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


if __name__ == "__main__":
    unittest.main()
