import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "backend/linux/discovery/swap_identity.sh"


class SwapIdentityTests(unittest.TestCase):

    def resolve(self, source):
        return subprocess.run(
            [
                "bash", "-c",
                'source "$1"; resolve_swap_path "$2"',
                "bash", str(RESOLVER), source
            ],
            text=True,
            capture_output=True,
            timeout=10
        )

    def test_absolute_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "swap.img"
            path.touch()

            result = self.resolve(str(path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                str(path.resolve())
            )

    def test_symlink_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "swap.img"
            alias = Path(directory) / "swap-alias"

            path.touch()
            alias.symlink_to(path)

            result = self.resolve(str(alias))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                str(path.resolve())
            )

    def test_missing_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-swap.img"

            result = self.resolve(str(missing))

            self.assertNotEqual(result.returncode, 0)

    def test_relative_path_rejected(self):
        result = self.resolve("swap.img")
        self.assertNotEqual(result.returncode, 0)

    def test_unknown_identifier_rejected(self):
        result = self.resolve("INVALID=swap")
        self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_uuid_rejected(self):
        result = self.resolve(
            "UUID=00000000-0000-0000-0000-000000000000"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_partuuid_rejected(self):
        result = self.resolve(
            "PARTUUID=00000000-0000-0000-0000-000000000000"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_label_rejected(self):
        result = self.resolve(
            "LABEL=DSSA_NONEXISTENT_SWAP_TEST"
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
