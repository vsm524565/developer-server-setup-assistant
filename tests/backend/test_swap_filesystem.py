import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/linux/discovery/swap_filesystem.sh"


def discover(path):
    """Run filesystem discovery in an isolated Bash process."""
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; discover_swap_filesystem "$2"',
            "bash",
            str(SCRIPT),
            str(path),
        ],
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )


class SwapFilesystemTests(unittest.TestCase):

    def test_existing_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing"
            target.write_text("test")

            result = discover(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

            self.assertEqual(payload["target_path"], str(target))
            self.assertGreater(
                payload["filesystem"]["available_bytes"], 0
            )

    def test_proposed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "swapfile"

            result = discover(target)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["target_path"],
                str(target),
            )

    def test_missing_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing" / "swapfile"

            result = discover(target)

            self.assertEqual(result.returncode, 3)

    def test_relative_path_rejected(self):
        result = discover("relative-swapfile")
        self.assertEqual(result.returncode, 2)

    def test_symlinked_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()

            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)

            result = discover(alias / "swapfile")

            self.assertEqual(result.returncode, 3)

    def test_dangling_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "dangling"
            target.symlink_to(root / "missing")

            result = discover(target)

            self.assertEqual(result.returncode, 3)

    def test_directory_target_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = discover(directory)

            self.assertEqual(result.returncode, 3)

    def test_noncanonical_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = f"{directory}/./swapfile"

            result = discover(target)

            self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()
