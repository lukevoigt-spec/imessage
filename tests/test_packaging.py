"""Checks that everything the tool needs is actually included when it is packaged.

The first version of this check did not exist, and a release went out missing
the sentiment word list because an ignore rule hid it. These tests fail if any
file the code reads at run time is missing or would be left out of the zip.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imx import update  # noqa: E402

# Every file the tool must have to run. The same list is what the app checks before it installs an update.
RUNTIME_FILES = update.REQUIRED_FILES
# Files that must be packaged and runnable for installing to work.
INSTALL_FILES = ["install.sh", "uninstall.sh", "Double-click to install.command"]


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True, text=True)


class PackagingTests(unittest.TestCase):
    def test_runtime_files_exist(self):
        for name in RUNTIME_FILES:
            self.assertTrue((ROOT / name).is_file(), "missing: %s" % name)

    def test_word_list_loads(self):
        from imx import features
        self.assertGreater(len(features.lexicon()), 7000)

    def test_nothing_needed_is_left_out_of_the_package(self):
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout (for example, an unzipped copy)")
        tracked = set(_git("ls-files").stdout.splitlines())
        for name in RUNTIME_FILES:
            self.assertIn(name, tracked, "%s exists but would be left out of the zip" % name)
        # Every Python file and every file in imx/ must be packaged too, not just the ones listed above.
        for path in list((ROOT / "imx").rglob("*")) + list((ROOT / "tests").glob("*.py")):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(ROOT).as_posix()
                self.assertIn(relative, tracked, "%s exists but would be left out of the zip" % relative)

    def test_installer_files_are_packaged_and_runnable(self):
        for name in INSTALL_FILES:
            self.assertTrue((ROOT / name).is_file(), "missing: %s" % name)
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")
        listing = _git("ls-files", "-s", "--", *INSTALL_FILES).stdout
        for name in INSTALL_FILES:
            lines = [line for line in listing.splitlines() if line.endswith("\t" + name)]
            self.assertTrue(lines, "%s would be left out of the zip" % name)
            # Mode 100755 means the file stays runnable when downloaded, so a double-click works.
            self.assertTrue(lines[0].startswith("100755"), "%s would not be runnable after download" % name)

    def test_nothing_personal_is_published(self):
        """The code is published openly. Planning notes are kept elsewhere and must not come back in.
        docs/ holds only the public sign-in relay pages that GitHub Pages serves for Brain Explorer."""
        self.assertFalse((ROOT / "DONE.md").exists(), "DONE.md is private and must not be in the published project")
        docs = ROOT / "docs"
        if docs.exists():
            found = sorted(p.name for p in docs.iterdir())
            self.assertEqual(found, sorted(set(found) & {"withings.html", "whoop.html"}),
                             "docs/ may hold only the sign-in relay pages; planning notes are private")

    def test_ignore_rules_do_not_hide_project_files(self):
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")
        for name in RUNTIME_FILES:
            self.assertNotEqual(_git("check-ignore", "-q", name).returncode, 0, "%s is hidden by .gitignore" % name)


if __name__ == "__main__":
    unittest.main()
