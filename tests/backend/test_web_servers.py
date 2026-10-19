
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "backend/linux/modules/software/web_servers.sh"


class WebServerCollectorTests(unittest.TestCase):

    def test_litespeed_conflict_does_not_confirm_either_edition(self):
        """Conflicting package evidence must not confirm either edition."""
        overrides = """
find_litespeed_package_edition() { echo unidentified; }
find_litespeed_evidence() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(overrides)

        for edition in ("openlitespeed", "litespeed"):
            server = result["web_servers"][edition]
            self.assertFalse(server["available"])
            self.assertEqual(server["detection_status"], "unidentified")

    def test_confirmed_openlitespeed_does_not_confirm_enterprise(self):
        """Only the edition supported by package evidence is confirmed."""
        overrides = """
find_litespeed_package_edition() { echo openlitespeed; }
find_web_server_binary() { echo /usr/local/lsws/bin/lshttpd; }
find_litespeed_evidence() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(overrides)

        openlitespeed = result["web_servers"]["openlitespeed"]
        enterprise = result["web_servers"]["litespeed"]

        self.assertTrue(openlitespeed["available"])
        self.assertEqual(openlitespeed["detection_status"], "detected")

        self.assertFalse(enterprise["available"])
        self.assertEqual(enterprise["detection_status"], "unidentified")


    def test_openlitespeed_confirmed_discovery(self):
        """Confirmed OpenLiteSpeed requires package and executable."""
        overrides = """
find_litespeed_package_edition() { echo openlitespeed; }
find_web_server_binary() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(
            overrides,
            "detect_web_server openlitespeed",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["detection_status"], "detected")
        self.assertEqual(result["service_name"], "lsws.service")

    def test_litespeed_enterprise_confirmed_discovery(self):
        """Confirmed Enterprise requires package and executable."""
        overrides = """
find_litespeed_package_edition() { echo litespeed; }
find_web_server_binary() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(
            overrides,
            "detect_web_server litespeed",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["detection_status"], "detected")
        self.assertEqual(result["service_name"], "lsws.service")


    def test_litespeed_package_conflict(self):
        """Conflicting installed packages must not select an edition."""
        overrides = """
dpkg-query() {
    case "${@: -1}" in
        openlitespeed|lsws)
            printf 'ii '
            ;;
        *)
            return 1
            ;;
    esac
}
"""
        result = self.run_collector_text(
            overrides,
            "find_litespeed_package_edition",
        )
        self.assertEqual(result, "unidentified")



    def test_litespeed_enterprise_package_identification(self):
        """An installed lsws package identifies LiteSpeed Enterprise."""
        overrides = """
dpkg-query() {
    if [[ "${@: -1}" == "lsws" ]]; then
        printf 'ii '
    else
        return 1
    fi
}
"""
        result = self.run_collector_text(
            overrides,
            "find_litespeed_package_edition",
        )
        self.assertEqual(result, "litespeed")

    def test_litespeed_package_absent(self):
        """Missing package metadata must not invent an edition."""
        overrides = """
dpkg-query() { return 1; }
"""
        result = self.run_collector_text(
            overrides,
            "find_litespeed_package_edition || true",
        )
        self.assertEqual(result, "")


    def test_openlitespeed_package_identification(self):
        """Installed OpenLiteSpeed package identifies its edition."""
        overrides = """
dpkg-query() {
    if [[ "${@: -1}" == "openlitespeed" ]]; then
        printf 'ii '
    else
        return 1
    fi
}
"""
        result = self.run_collector_text(
            overrides,
            "find_litespeed_package_edition",
        )
        self.assertEqual(result, "openlitespeed")

    def test_openlitespeed_candidate_is_not_confirmed_edition(self):
        """An executable candidate alone must not prove the edition."""
        overrides = """
    find_openlitespeed_candidate() {
        echo /usr/local/lsws/bin/openlitespeed
    }
    find_web_server_service() { return 1; }
    """

        result = self.run_collector(
            overrides,
            "detect_web_server openlitespeed",
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["detection_status"], "unidentified")
        self.assertIsNone(result["path"])

    def test_litespeed_family_unidentified(self):
        """Shared LiteSpeed evidence must not imply an edition."""
        overrides = """
find_litespeed_evidence() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(
            overrides,
            "detect_web_server litespeed",
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["detection_status"], "unidentified")
        self.assertEqual(result["service_name"], "lsws.service")
        self.assertEqual(result["service_state"], "active")

    def test_openlitespeed_family_unidentified(self):
        """Shared family evidence must not prove OpenLiteSpeed."""
        overrides = """
find_litespeed_evidence() { echo /usr/local/lsws/bin/lshttpd; }
find_web_server_service() { echo lsws.service; }
get_web_server_service_state() { echo inactive; }
"""
        result = self.run_collector(
            overrides,
            "detect_web_server openlitespeed",
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["detection_status"], "unidentified")
        self.assertEqual(result["service_state"], "inactive")

    def run_collector(self, overrides="", expression="discover_web_servers"):
        """Execute the Bash collector with optional test overrides."""
        script = f"""
        source "{COLLECTOR}"
        {overrides}
        {expression}
        """
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def run_collector_text(self, overrides="", expression=""):
        """Execute a Bash collector helper returning plain text."""
        script = f"""
source "{COLLECTOR}"
{overrides}
{expression}
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def test_response_structure(self):
        """All supported web servers must appear in the response."""
        result = self.run_collector()

        self.assertEqual(
            set(result["web_servers"]),
            {"nginx", "apache", "openlitespeed", "litespeed"},
        )

    def test_nginx_absent(self):
        """Absent binaries and services must not be reported as available."""
        overrides = """
find_web_server_binary() { return 1; }
find_web_server_service() { return 1; }
"""
        result = self.run_collector(overrides, "detect_web_server nginx")

        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertIsNone(result["service_name"])
        self.assertEqual(result["service_state"], "unknown")

    def test_nginx_active(self):
        """An installed and running server must be identified."""
        overrides = """
find_web_server_binary() { echo /usr/sbin/nginx; }
find_web_server_service() { echo nginx.service; }
get_web_server_version() { echo 'nginx version: nginx/1.24.0'; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(overrides, "detect_web_server nginx")

        self.assertTrue(result["available"])
        self.assertEqual(result["path"], "/usr/sbin/nginx")
        self.assertEqual(result["service_name"], "nginx.service")
        self.assertEqual(result["service_state"], "active")

    def test_nginx_inactive(self):
        """Installed software must remain available when its service stops."""
        overrides = """
find_web_server_binary() { echo /usr/sbin/nginx; }
find_web_server_service() { echo nginx.service; }
get_web_server_version() { echo 'nginx version: nginx/1.24.0'; }
get_web_server_service_state() { echo inactive; }
"""
        result = self.run_collector(overrides, "detect_web_server nginx")

        self.assertTrue(result["available"])
        self.assertEqual(result["service_state"], "inactive")

    def test_nginx_failed(self):
        """A failed service must not be classified as absent."""
        overrides = """
find_web_server_binary() { echo /usr/sbin/nginx; }
find_web_server_service() { echo nginx.service; }
get_web_server_version() { echo 'nginx version: nginx/1.24.0'; }
get_web_server_service_state() { echo failed; }
"""
        result = self.run_collector(overrides, "detect_web_server nginx")

        self.assertTrue(result["available"])
        self.assertEqual(result["service_state"], "failed")

    def test_service_without_detected_binary(self):
        """A systemd unit may exist without a binary in known paths."""
        overrides = """
find_web_server_binary() { return 1; }
find_web_server_service() { echo nginx.service; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(overrides, "detect_web_server nginx")

        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["service_name"], "nginx.service")
        self.assertEqual(result["service_state"], "active")

    def test_apache_detection(self):
        """Apache must use the same discovery contract."""
        overrides = """
find_web_server_binary() { echo /usr/sbin/apache2; }
find_web_server_service() { echo apache2.service; }
get_web_server_version() { echo 'Server version: Apache/2.4'; }
get_web_server_service_state() { echo active; }
"""
        result = self.run_collector(overrides, "detect_web_server apache")

        self.assertTrue(result["available"])
        self.assertEqual(result["path"], "/usr/sbin/apache2")
        self.assertEqual(result["service_name"], "apache2.service")

    def test_unsupported_server(self):
        """Unknown server identifiers must be rejected."""
        script = f'''
source "{COLLECTOR}"
detect_web_server unsupported
'''
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
