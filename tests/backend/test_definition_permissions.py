"""Definition file and folder permission rules (Milestone 4.35).

The same rule in Bash (plan.sh) and Python (loader.py): a real file or
folder, owned by the current user or root, never writable by others,
and group-writable only for the owner's private group with no other
members. Group lookups are mocked so the tests do not depend on the
machine's real groups.
"""

import grp
import json
import os
import pwd
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.components import loader
from bridge.components.contracts import DefinitionError


ROOT = Path(__file__).resolve().parents[2]
PLAN_SH = ROOT / "backend/linux/engine/plan.sh"
DEFINITIONS_SH = ROOT / "backend/linux/engine/definitions.sh"
GIT = ROOT / "definitions/components/git.json"

ME = pwd.getpwuid(os.getuid())


class PermissionCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "definitions"
        self.dir.mkdir()
        self.file = self.dir / "git.json"
        shutil.copyfile(GIT, self.file)
        self.dir.chmod(0o755)
        self.file.chmod(0o644)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()

    def fake_getent(self, members=""):
        """getent reporting the owner's group with these members."""
        script = self.bin / "getent"
        script.write_text(f"""#!/bin/bash
case "$1" in
  group)  echo "{ME.pw_name}:x:{os.getgid()}:{members}" ;;
  passwd) if [ -n "$2" ]; then echo "{ME.pw_name}:x:{os.getuid()}:{os.getgid()}::/home/x:/bin/bash"
          else echo "{ME.pw_name}:x:{os.getuid()}:{os.getgid()}::/home/x:/bin/bash"; fi ;;
esac
""")
        script.chmod(0o755)

    def bash_check(self, path):
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin")
        return subprocess.run(
            ["bash", "-c", f'source "{PLAN_SH}"; dssa_check_definition_path "$1"', "_", str(path)],
            capture_output=True, text=True, env=env, check=False, stdin=subprocess.DEVNULL)

    def python_check(self, path, members=()):
        group = grp.struct_group((ME.pw_name, "x", os.getgid(), list(members)))
        with mock.patch.object(loader.grp, "getgrgid", return_value=group), \
             mock.patch.object(loader.pwd, "getpwuid", return_value=ME), \
             mock.patch.object(loader.pwd, "getpwall", return_value=[ME]):
            loader.check_definition_path(path)


class BashRuleTests(PermissionCase):

    def test_private_file_and_folder_pass(self):
        self.fake_getent()
        for path in (self.dir, self.file):
            self.assertEqual(self.bash_check(path).returncode, 0)

    def test_other_writable_is_refused(self):
        self.fake_getent()
        self.file.chmod(0o646)
        result = self.bash_check(self.file)
        self.assertEqual(result.returncode, 1)
        self.assertIn("writable by other users", result.stderr)
        self.dir.chmod(0o757)
        self.assertEqual(self.bash_check(self.dir).returncode, 1)

    def test_group_writable_private_group_passes(self):
        self.fake_getent(members="")
        self.file.chmod(0o664)
        self.dir.chmod(0o775)
        self.assertEqual(self.bash_check(self.file).returncode, 0)
        self.assertEqual(self.bash_check(self.dir).returncode, 0)

    def test_group_writable_shared_group_is_refused(self):
        self.fake_getent(members="alice,bob")
        self.file.chmod(0o664)
        result = self.bash_check(self.file)
        self.assertEqual(result.returncode, 1)
        self.assertIn("shared group", result.stderr)

    def test_symlink_is_refused(self):
        self.fake_getent()
        link = self.dir / "link.json"
        link.symlink_to(self.file)
        self.assertEqual(self.bash_check(link).returncode, 1)

    def test_plan_request_refuses_an_unsafe_folder(self):
        self.fake_getent()
        self.dir.chmod(0o757)
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin")
        result = subprocess.run(
            ["bash", "-c", f'source "{DEFINITIONS_SH}"; source "{PLAN_SH}"; '
             f'build_plan_request lab1 SHA256:{"A" * 43} "$1" git', "_", str(self.dir)],
            capture_output=True, text=True, env=env, check=False, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


class PythonRuleTests(PermissionCase):

    def test_private_file_and_folder_pass(self):
        self.python_check(self.dir)
        self.python_check(self.file)

    def test_other_writable_is_refused(self):
        self.file.chmod(0o646)
        with self.assertRaisesRegex(DefinitionError, "other users"):
            self.python_check(self.file)

    def test_group_writable_private_group_passes(self):
        self.file.chmod(0o664)
        self.python_check(self.file)

    def test_group_writable_shared_group_is_refused(self):
        self.file.chmod(0o664)
        with self.assertRaisesRegex(DefinitionError, "shared group"):
            self.python_check(self.file, members=["alice"])

    def test_another_user_with_the_same_primary_group_is_shared(self):
        self.file.chmod(0o664)
        other = pwd.struct_passwd(("other", "x", os.getuid() + 1, os.getgid(), "", "/", "/bin/sh"))
        group = grp.struct_group((ME.pw_name, "x", os.getgid(), []))
        with mock.patch.object(loader.grp, "getgrgid", return_value=group), \
             mock.patch.object(loader.pwd, "getpwuid", return_value=ME), \
             mock.patch.object(loader.pwd, "getpwall", return_value=[ME, other]):
            with self.assertRaisesRegex(DefinitionError, "shared group"):
                loader.check_definition_path(self.file)

    def test_loader_refuses_an_unsafe_folder(self):
        self.dir.chmod(0o757)
        with self.assertRaisesRegex(DefinitionError, "other users"):
            loader.load_component_definitions(self.dir)

    def test_repository_definitions_load(self):
        # The real repository must pass on this machine as it is.
        self.assertIn("git", loader.load_component_definitions())


if __name__ == "__main__":
    unittest.main()
