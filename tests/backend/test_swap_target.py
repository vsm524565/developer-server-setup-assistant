import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/linux/discovery/swap_target.sh"


class SwapTargetTests(unittest.TestCase):

    def run_discovery(self, target):
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; discover_swap_target "$2"',
                "bash",
                str(SCRIPT),
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_absent_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing"
            result = self.run_discovery(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)

            self.assertEqual(data["target_type"], "ABSENT")
            self.assertFalse(data["exists"])
            self.assertFalse(data["execution_allowed"])

    def test_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "regular"
            target.touch()

            result = self.run_discovery(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)

            self.assertEqual(data["target_type"], "REGULAR_FILE")
            self.assertTrue(data["exists"])
            self.assertFalse(data["execution_allowed"])

    def test_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "subdirectory"
            target.mkdir()

            result = self.run_discovery(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)

            self.assertEqual(data["target_type"], "DIRECTORY")
            self.assertTrue(data["exists"])
            self.assertFalse(data["execution_allowed"])

    def test_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular"
            regular.touch()

            link = root / "link"
            link.symlink_to(regular)

            result = self.run_discovery(link)

            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)

            self.assertEqual(data["target_type"], "SYMLINK")
            self.assertTrue(data["is_symlink"])
            self.assertFalse(data["execution_allowed"])

    def test_relative_path_rejected(self):
        result = self.run_discovery("swap.img")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid swap target path", result.stderr)

    def test_root_path_rejected(self):
        result = self.run_discovery("/")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid swap target path", result.stderr)
    def test_parent_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_parent = root / "real"
            real_parent.mkdir()

            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            result = self.run_discovery(linked_parent / "swap.img")

            self.assertEqual(result.returncode, 3)
            self.assertIn("parent contains a symlink", result.stderr)

    def test_missing_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing-parent" / "swap.img"

            result = self.run_discovery(target)

            self.assertEqual(result.returncode, 3)
            self.assertIn(
                "parent is missing or inaccessible",
                result.stderr,
            )
    def test_dangling_symlink_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "dangling"
            target.symlink_to(root / "nonexistent")

            result = self.run_discovery(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)

            self.assertEqual(data["target_type"], "SYMLINK")
            self.assertTrue(data["exists"])
            self.assertTrue(data["is_symlink"])
            self.assertFalse(data["execution_allowed"])

    def test_inaccessible_parent_rejected(self):
        import os

        if os.geteuid() == 0:
            self.skipTest(
                "Permission test requires an unprivileged user"
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "restricted"
            parent.mkdir()

            try:
                parent.chmod(0o000)

                result = self.run_discovery(parent / "swap.img")

                self.assertEqual(result.returncode, 3)
                self.assertIn(
                    "parent cannot be inspected",
                    result.stderr,
                )
            finally:
                parent.chmod(0o700)

if __name__ == "__main__":
    unittest.main()
