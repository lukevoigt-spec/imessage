"""Tests for installing the app and for the app updating itself.

Nothing here contacts GitHub. Downloads are replaced with local files, and the
installer runs inside a pretend home folder so it cannot touch the real one.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imx import __version__, update  # noqa: E402

INSTALLER = ROOT / "install.sh"
HAVE_SHELL_TOOLS = all(shutil.which(tool) for tool in ("bash", "curl", "unzip"))


def make_release(destination: Path, version: str, leave_out=(), extra=None) -> Path:
    """Build a zip laid out the way GitHub lays out a published version: one top folder holding the code."""
    top = "imessage-%s" % version
    with zipfile.ZipFile(str(destination), "w") as bundle:
        for name in ["imessage_explorer.py", "install.sh", "README.md"]:
            bundle.write(str(ROOT / name), "%s/%s" % (top, name))
        for path in sorted((ROOT / "imx").rglob("*")):
            relative = path.relative_to(ROOT).as_posix()
            if not path.is_file() or "__pycache__" in path.parts or relative in leave_out:
                continue
            if relative == "imx/__init__.py":
                text = path.read_text(encoding="utf-8").replace('"%s"' % __version__, '"%s"' % version)
                bundle.writestr("%s/%s" % (top, relative), text)
            else:
                bundle.write(str(path), "%s/%s" % (top, relative))
        for name, content in (extra or {}).items():
            bundle.writestr(name, content)
    return destination


class VersionTests(unittest.TestCase):
    def test_version_numbers_compare_as_numbers_not_text(self):
        self.assertEqual(update.parse_version("v0.3.1"), (0, 3, 1))
        self.assertEqual(update.parse_version("0.10.0"), (0, 10, 0))
        self.assertGreater(update.parse_version("0.10.0"), update.parse_version("0.9.9"))
        self.assertIsNone(update.parse_version("main"))
        self.assertIsNone(update.parse_version("v1.2"))

    def test_newest_version_ignores_tags_that_are_not_versions(self):
        tags = [{"name": "v0.3.1"}, {"name": "v0.10.0"}, {"name": "experiment"}, {"name": "v0.9.2"}]
        self.assertEqual(update.newest_version(lambda _url: json.dumps(tags).encode()), "0.10.0")
        self.assertIsNone(update.newest_version(lambda _url: b"[]"))

    def test_unreadable_reply_is_a_plain_error(self):
        with self.assertRaises(update.UpdateError):
            update.newest_version(lambda _url: b"<html>not json</html>")

    def test_check_reports_whether_an_update_exists(self):
        newer = "%d.%d.%d" % (update.parse_version(__version__)[0], update.parse_version(__version__)[1] + 1, 0)
        found = update.check(lambda _url: json.dumps([{"name": "v" + newer}]).encode())
        self.assertTrue(found["update_available"])
        self.assertEqual(found["latest"], newer)
        same = update.check(lambda _url: json.dumps([{"name": "v" + __version__}]).encode())
        self.assertFalse(same["update_available"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imx-update-test-"))
        self.target = self.tmp / "app"
        self.target.mkdir()
        (self.target / "old-file.txt").write_text("the version before")
        (self.target / update.MARKER).write_text("installed")

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def fetch(self, archive: Path):
        def fetch_to(_url, destination):
            shutil.copyfile(str(archive), str(destination))
            return b""
        return fetch_to

    def assert_untouched(self):
        self.assertEqual((self.target / "old-file.txt").read_text(), "the version before")
        self.assertFalse((self.tmp / "app.previous").exists())

    def test_a_good_download_replaces_the_code_and_keeps_the_previous_version(self):
        archive = make_release(self.tmp / "good.zip", "9.9.9")
        result = update.apply("9.9.9", target=self.target, fetch_to=self.fetch(archive))
        self.assertEqual(result["updated_to"], "9.9.9")
        self.assertEqual(update.version_in(self.target), "9.9.9")
        self.assertEqual(update.missing_files(self.target), [])
        self.assertTrue(update.is_managed(self.target), "the new copy must still be able to update itself")
        self.assertEqual((self.tmp / "app.previous" / "old-file.txt").read_text(), "the version before")
        self.assertFalse((self.tmp / "app.new").exists())

    def test_a_copy_not_put_in_place_by_the_installer_never_replaces_itself(self):
        (self.target / update.MARKER).unlink()
        archive = make_release(self.tmp / "good.zip", "9.9.9")
        with self.assertRaises(update.UpdateError):
            update.apply("9.9.9", target=self.target, fetch_to=self.fetch(archive))
        self.assert_untouched()

    def test_an_incomplete_download_is_refused(self):
        archive = make_release(self.tmp / "bad.zip", "9.9.9", leave_out=("imx/data/vader_lexicon.txt",))
        with self.assertRaises(update.UpdateError) as caught:
            update.apply("9.9.9", target=self.target, fetch_to=self.fetch(archive))
        self.assertIn("vader_lexicon", str(caught.exception))
        self.assert_untouched()

    def test_a_download_that_is_a_different_version_is_refused(self):
        archive = make_release(self.tmp / "other.zip", "9.9.8")
        with self.assertRaises(update.UpdateError):
            update.apply("9.9.9", target=self.target, fetch_to=self.fetch(archive))
        self.assert_untouched()

    def test_a_download_with_an_unsafe_path_is_refused(self):
        archive = make_release(self.tmp / "evil.zip", "9.9.9", extra={"../escaped.txt": "should never be written"})
        with self.assertRaises(update.UpdateError):
            update.apply("9.9.9", target=self.target, fetch_to=self.fetch(archive))
        self.assert_untouched()
        self.assertFalse((self.tmp / "escaped.txt").exists())

    def test_a_download_that_is_not_an_archive_is_refused(self):
        junk = self.tmp / "junk.zip"
        junk.write_text("<html>Not Found</html>")
        with self.assertRaises(update.UpdateError):
            update.apply("9.9.9", target=self.target, fetch_to=self.fetch(junk))
        self.assert_untouched()


@unittest.skipUnless(HAVE_SHELL_TOOLS, "needs bash, curl and unzip")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imx-install-test-"))
        self.home = self.tmp / "home"
        self.apps = self.tmp / "Applications"
        self.home.mkdir()
        self.support = self.home / "Library" / "Application Support" / "iMessage Explorer"

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def install(self, **extra):
        env = dict(os.environ, HOME=str(self.home), IMX_INSTALL_TEST="1", IMX_INSTALL_APPS_DIR=str(self.apps))
        env.update(extra)
        return subprocess.run(["bash", str(INSTALLER)], env=env, capture_output=True, text=True, timeout=120)

    def assert_installed(self, version):
        code = self.support / "app"
        self.assertEqual(update.missing_files(code), [])
        self.assertEqual(update.version_in(code), version)
        self.assertTrue(update.is_managed(code))
        self.assertFalse((code / ".git").exists())
        bundle = self.apps / "iMessage Explorer.app"
        self.assertTrue(bundle.is_dir())
        scripts = [self.support / "run.command"]
        if (bundle / "Contents" / "MacOS" / "launcher").exists():
            # The plain icon, used where Apple's osacompile is absent (as on the machine running these tests).
            with open(str(bundle / "Contents" / "Info.plist"), "rb") as handle:
                info = plistlib.load(handle)
            self.assertEqual(info["CFBundleExecutable"], "launcher")
            self.assertEqual(info["CFBundleShortVersionString"], version)
            scripts.append(bundle / "Contents" / "MacOS" / "launcher")
        for script in scripts:
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, "%s must be runnable" % script)
            self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        self.assertIn('cd "$(dirname "$0")/app"', (self.support / "run.command").read_text())
        self.assertIn("serve", (self.support / "run.command").read_text())

    def test_installs_the_copy_it_sits_in(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed(__version__)
        # The copy that was installed can actually run.
        ran = subprocess.run([sys.executable, str(self.support / "app" / "imessage_explorer.py"), "--version"],
                             capture_output=True, text=True)
        self.assertEqual(ran.stdout.strip(), __version__)

    def test_installing_again_keeps_the_previous_copy_and_never_touches_message_data(self):
        data = self.home / ".imessage-explorer"
        data.mkdir()
        (data / "people.json").write_text("{}")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        self.assert_installed(__version__)
        self.assertTrue((self.support / "app.previous").is_dir())
        self.assertEqual((data / "people.json").read_text(), "{}")

    def test_installs_from_a_download(self):
        archive = make_release(self.tmp / "release.zip", "9.9.9")
        result = self.install(IMX_INSTALL_FROM_WEB="1", IMX_INSTALL_ZIP_URL="file://%s" % archive)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed("9.9.9")

    def test_an_incomplete_download_changes_nothing(self):
        archive = make_release(self.tmp / "bad.zip", "9.9.9", leave_out=("imx/data/vader_lexicon.txt",))
        result = self.install(IMX_INSTALL_FROM_WEB="1", IMX_INSTALL_ZIP_URL="file://%s" % archive)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete", result.stderr)
        self.assertFalse(self.support.exists())

    def test_uninstall_removes_the_app_and_keeps_message_data(self):
        data = self.home / ".imessage-explorer"
        data.mkdir()
        self.assertEqual(self.install().returncode, 0)
        env = dict(os.environ, HOME=str(self.home), IMX_INSTALL_APPS_DIR=str(self.apps))
        done = subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.support.exists())
        self.assertFalse((self.apps / "iMessage Explorer.app").exists())
        self.assertTrue(data.is_dir())


FAKE_CURL = r'''#!/bin/bash
# Stands in for curl during the test: answers GitHub's addresses from local files.
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    --max-time|--max-filesize) shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
case "$url" in
  *api.github.com*tags*) cat "$IMX_FAKE_GITHUB/tags.json";;
  *archive/refs/tags/*) cp "$IMX_FAKE_GITHUB/release.zip" "$out";;
  *) echo "unexpected address: $url" >&2; exit 22;;
esac
'''


@unittest.skipUnless(HAVE_SHELL_TOOLS, "needs bash, curl and unzip")
class EndToEndUpdateTests(unittest.TestCase):
    """Install, start the app, press "Update the app", and confirm the new version is the one answering."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imx-e2e-test-"))
        self.process = None

    def tearDown(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def page(self, port):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5) as response:
            return response.read().decode("utf-8")

    def call(self, port, token, path, post=False):
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=b"{}" if post else None,
                                         headers={"X-IMX-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_for(self, port, wanted_version=None, seconds=20):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                token = self.page(port).split('name="imx-token" content="')[1].split('"')[0]
                status = self.call(port, token, "/api/status")
                if wanted_version in (None, status["version"]):
                    return token, status
            except Exception:
                pass
            time.sleep(0.25)
        self.fail("the app did not come up%s" % (" on version %s" % wanted_version if wanted_version else ""))

    def test_update_button_installs_the_new_version_and_restarts_on_it(self):
        home, apps, fake = self.tmp / "home", self.tmp / "Applications", self.tmp / "github"
        for folder in (home, fake, self.tmp / "bin"):
            folder.mkdir()
        env = dict(os.environ, HOME=str(home), IMX_INSTALL_TEST="1", IMX_INSTALL_APPS_DIR=str(apps),
                   IMX_DATA_DIR=str(self.tmp / "data"), IMX_FAKE_GITHUB=str(fake))
        self.assertEqual(subprocess.run(["bash", str(INSTALLER)], env=env, capture_output=True).returncode, 0)
        code = home / "Library" / "Application Support" / "iMessage Explorer" / "app"

        make_release(fake / "release.zip", "9.9.9")
        (fake / "tags.json").write_text(json.dumps([{"name": "v" + __version__}, {"name": "v9.9.9"}]))
        curl = self.tmp / "bin" / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        env["PATH"] = "%s%s%s" % (self.tmp / "bin", os.pathsep, env["PATH"])

        with __import__("socket").socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.process = subprocess.Popen([sys.executable, str(code / "imessage_explorer.py"), "serve", "--port", str(port), "--no-browser"],
                                        env=env, cwd=str(code), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        token, status = self.wait_for(port)
        self.assertEqual(status["version"], __version__)
        self.assertTrue(status["can_update_itself"])

        found = self.call(port, token, "/api/app/check")
        self.assertTrue(found["update_available"])
        self.assertEqual(found["latest"], "9.9.9")
        self.assertEqual(self.call(port, token, "/api/app/update", post=True)["updated_to"], "9.9.9")

        # Same process, same address, new version.
        new_token, status = self.wait_for(port, wanted_version="9.9.9")
        self.assertNotEqual(new_token, token)
        self.assertIsNone(self.process.poll(), "the app should still be running after updating itself")
        self.assertEqual(update.version_in(code.with_name("app.previous")), __version__)

        # Quit from the interface ends the process cleanly.
        self.call(port, new_token, "/api/quit", post=True)
        self.assertEqual(self.process.wait(timeout=15), 0)


if __name__ == "__main__":
    unittest.main()
