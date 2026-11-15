
"""Isolated Docker installer repository-stage failure tests."""

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "backend/linux/modules/install/docker.sh"


class DockerInstallFailureTests(unittest.TestCase):
    """Exercise installer failures without real APT or repository changes."""

    def run_failure(self, failure):
        """Run an isolated copy of the installer in a temporary filesystem."""


        with tempfile.TemporaryDirectory(prefix="dssa-docker-test-") as tmp:
            root = Path(tmp)

            # Read the actual Docker installer before modifying the test copy.
            source = INSTALLER.read_text()

            # Ensure the expected production safeguards are present.
            self.assertEqual(source.count('[[ "$EUID" -eq 0 ]]'), 1)
            self.assertEqual(source.count("source /etc/os-release"), 1)
            self.assertIn("/etc/apt/", source)

            # Replace privilege and operating-system evidence in the test only.
            source = source.replace(
                '[[ "$EUID" -eq 0 ]]',
                "true",
                1,
            )

            source = source.replace(
                "source /etc/os-release",
                "ID=ubuntu\nVERSION_ID=24.04",
                1,
            )

            # Simulate a clean host without Docker executables.
            executable_check = (
                'if command -v "$cmd" >/dev/null 2>&1; then'
            )

            self.assertEqual(
                source.count(executable_check),
                1,
                "Docker executable guard changed; review the fixture.",
            )

            source = source.replace(
                executable_check,
                "if false; then",
                1,
            )

            # Redirect Docker-specific paths to the temporary test directory.
            source = source.replace("/etc/apt/", f"{tmp}/etc/apt/")
            source = source.replace("/etc/docker", f"{tmp}/etc/docker")
            source = source.replace("/var/lib/docker", f"{tmp}/var/lib/docker")
            source = source.replace(
                "/var/lib/containerd",
                f"{tmp}/var/lib/containerd",
            )

            # Ensure no original APT paths remain in the test copy.
            self.assertNotIn(
                "/etc/apt/",
                source.replace(f"{tmp}/etc/apt/", ""),
            )

            (root / "etc/apt/sources.list.d").mkdir(parents=True)
            (root / "etc/apt/keyrings").mkdir(parents=True)

            script = r'''
set -euo pipefail

FAILURE="$1"
TEST_ROOT="$2"

package_is_installed() {
    return 1
}

verify_docker() {
    return 1
}

uname() {
    if [[ "${1:-}" == "-m" ]]; then
        echo x86_64
    else
        command uname "$@"
    fi
}

dpkg() {
    if [[ "${1:-}" == "--print-architecture" ]]; then
        echo amd64
    else
        return 97
    fi
}

systemctl() {
    if [[ "${1:-}" == "list-unit-files" ]]; then
        return 0
    fi
    return 97
}


apt-get() {
    local action="${1:-}"
    local option="${2:-}"
    printf 'MOCK_APT: failure=%s action=%s caller_line=%s\n' \
        "$FAILURE" "$action" "${BASH_LINENO[0]:-unknown}" >&2
    case "$FAILURE:$action:$option" in
        metadata:update:*)
            echo "SIMULATED_INITIAL_METADATA_FAILURE" >&2
            return 21
            ;;
        prerequisites:install:-y)
            echo "SIMULATED_PREREQUISITE_FAILURE" >&2
            return 22
            ;;
    esac

    case "$action:$option" in
        update:*)
            return 0
            ;;
        install:-y)
            if [[ " $* " == *" docker-ce "* ]]; then
                echo "UNEXPECTED_DOCKER_INSTALL" >&2
                return 97
            fi
            return 0
            ;;
    esac

    echo "UNEXPECTED_APT_COMMAND" >&2
    return 97
}


install() {
    local last="${!#}"
    [[ "$last" == "$TEST_ROOT"/* ]] || {
        echo "UNSAFE_INSTALL_DESTINATION" >&2
        return 97
    }
    command install "$@"
}

curl() {
    if [[ "$FAILURE" == "download" ]]; then
        echo "SIMULATED_DOWNLOAD_FAILURE" >&2
        return 23
    fi

    local previous="" output=""
    for arg in "$@"; do
        if [[ "$previous" == "-o" ]]; then
            output="$arg"
        fi
        previous="$arg"
    done

    [[ -n "$output" ]] || return 97
    printf 'FAKE-KEY\n' > "$output"
}

gpg() {
    if [[ "$FAILURE" == "fingerprint" ]]; then
        echo "fpr:::::::::INVALIDFINGERPRINT:"
    else
        echo "fpr:::::::::9DC858229FC7DD38854AE2D88D81803C0EBFCD88:"
    fi
}

''' + source + r'''

install_docker apt
'''

            result = subprocess.run(
                ["bash", "-c", script, "docker-test", failure, tmp],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            # No test may create the actual vendor repository file.
            self.assertFalse(
                (root / "etc/apt/sources.list.d/docker.sources").exists(),
            )
            self.assertNotIn("UNSAFE_INSTALL_DESTINATION", result.stderr)
            self.assertNotIn("UNEXPECTED_DOCKER_INSTALL", result.stderr)

            return result

    def test_metadata_refresh_failure(self):
        result = self.run_failure("metadata")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APT metadata refresh failed", result.stderr)

    def test_prerequisite_installation_failure(self):
        result = self.run_failure("prerequisites")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Repository prerequisite installation failed", result.stderr)

    def test_signing_key_download_failure(self):
        result = self.run_failure("download")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker signing key download failed", result.stderr)

    def test_signing_key_fingerprint_mismatch(self):
        result = self.run_failure("fingerprint")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fingerprint mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
