"""Updating the app itself to a newer version, from inside the app.

How it works, in plain terms:

- Published versions are marked in the public code repository with a version
  tag such as v0.3.1. Only tagged versions are ever installed; work in
  progress is not.
- "Check for updates" asks GitHub for the list of tags and compares the newest
  with the version running here. This sends nothing about you or your messages.
- "Update the app" downloads that version, checks it is complete, swaps it in,
  and keeps the previous version alongside in case the new one is broken.

Only a copy put in place by the installer updates itself. A folder you
unzipped by hand, or a developer's working copy, is left alone.

Downloads use the Mac's own `curl`, which trusts the same certificates as
Safari. That avoids certificate problems some Python installs have.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__, paths

REPOSITORY = "lukevoigt-spec/imessage"
TAGS_URL = "https://api.github.com/repos/%s/tags?per_page=100" % REPOSITORY
ZIP_URL = "https://github.com/%s/archive/refs/tags/v%%s.zip" % REPOSITORY
INSTALL_COMMAND = "curl -fsSL https://raw.githubusercontent.com/%s/main/install.sh | bash" % REPOSITORY

# Written by the installer into the app's folder. Its presence means "this copy may update itself".
MARKER = ".installed-by-installer"
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024

# Every file the tool must have to run. A download missing any of these is refused,
# and the packaging tests use the same list.
REQUIRED_FILES = [
    "imessage_explorer.py",
    "imx/__init__.py",
    "imx/cli.py",
    "imx/server.py",
    "imx/index.py",
    "imx/data/vader_lexicon.txt",
    "imx/data/VADER_LICENSE.txt",
    "imx/static/index.html",
    "imx/static/app.js",
    "imx/static/style.css",
]

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateError(Exception):
    """An update could not be checked or applied. The message says why, plainly."""


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    match = _VERSION_RE.match((text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def app_dir() -> Path:
    """The folder holding the running code."""
    return paths.launcher().parent


def is_managed(folder: Optional[Path] = None) -> bool:
    """True when this copy was put in place by the installer and may replace itself."""
    return ((folder or app_dir()) / MARKER).exists()


def _curl(url: str, destination: Optional[Path] = None) -> bytes:
    """Fetch a web address with the system's curl. Returns the content, or writes it to a file."""
    command = ["curl", "-fsSL", "--max-time", "90", "--max-filesize", str(MAX_DOWNLOAD_BYTES), url]
    if destination is not None:
        command += ["-o", str(destination)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError("Could not reach GitHub (%s). Check the internet connection." % exc)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no detail"]
        raise UpdateError("Could not reach GitHub: %s" % detail[0])
    return result.stdout


def newest_version(fetch: Callable[[str], bytes] = _curl) -> Optional[str]:
    """The highest published version, such as '0.3.1', or None if nothing is published yet."""
    try:
        tags = json.loads(fetch(TAGS_URL).decode("utf-8"))
    except ValueError:
        raise UpdateError("GitHub sent back something unreadable. Try again later.")
    versions = sorted(v for v in (parse_version(tag.get("name", "")) for tag in tags if isinstance(tag, dict)) if v)
    return "%d.%d.%d" % versions[-1] if versions else None


def check(fetch: Callable[[str], bytes] = _curl) -> Dict[str, object]:
    """Compare the running version with the newest published one."""
    latest = newest_version(fetch)
    current = parse_version(__version__)
    return {
        "current": __version__,
        "latest": latest,
        "update_available": bool(latest and current and parse_version(latest) > current),
        "can_update_itself": is_managed(),
        "install_command": INSTALL_COMMAND,
    }


def _extract(archive: Path, into: Path) -> Path:
    """Unpack a downloaded version and return the folder holding the code. Refuses anything unsafe."""
    try:
        with zipfile.ZipFile(str(archive)) as bundle:
            for name in bundle.namelist():
                # A well-formed download never has absolute paths or ".." in it.
                if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                    raise UpdateError("The download contained an unsafe file path and was refused.")
            bundle.extractall(str(into))
    except zipfile.BadZipFile:
        raise UpdateError("The download was not a valid archive. Try again.")
    folders = [entry for entry in into.iterdir() if entry.is_dir()]
    if len(folders) != 1:
        raise UpdateError("The download was not laid out as expected and was refused.")
    return folders[0]


def missing_files(folder: Path) -> List[str]:
    return [name for name in REQUIRED_FILES if not (folder / name).is_file()]


def version_in(folder: Path) -> Optional[str]:
    """The version a folder of code says it is, read without running it."""
    try:
        text = (folder / "imx" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def apply(version: str, target: Optional[Path] = None, fetch_to: Callable[[str, Path], bytes] = _curl) -> Dict[str, object]:
    """Download one published version and put it in place of the running code.

    The previous version is kept next to it as "<folder>.previous". If anything
    fails before the swap, nothing is changed. If the swap itself fails, the
    previous version is put back.
    """
    target = target or app_dir()
    if not is_managed(target):
        raise UpdateError("This copy was not put in place by the installer, so it will not replace itself. "
                          "To install the app properly, paste this into Terminal:  %s" % INSTALL_COMMAND)
    if parse_version(version) is None:
        raise UpdateError("'%s' is not a version number." % version)
    workspace = Path(tempfile.mkdtemp(prefix="imessage-explorer-update-"))
    try:
        archive = workspace / "download.zip"
        fetch_to(ZIP_URL % version, archive)
        if not archive.exists() or archive.stat().st_size == 0:
            raise UpdateError("The download came back empty. Try again.")
        fresh = _extract(archive, workspace / "unpacked")
        missing = missing_files(fresh)
        if missing:
            raise UpdateError("The download is incomplete (missing %s) and was refused." % ", ".join(missing[:3]))
        if version_in(fresh) != version:
            raise UpdateError("The download says it is version %s, not %s, and was refused." % (version_in(fresh), version))
        (fresh / MARKER).write_text("Installed by the in-app updater.\n", encoding="utf-8")

        previous = target.with_name(target.name + ".previous")
        staged = target.with_name(target.name + ".new")
        for leftover in (previous, staged):
            if leftover.exists():
                shutil.rmtree(str(leftover))
        shutil.copytree(str(fresh), str(staged))
        target.rename(previous)
        try:
            staged.rename(target)
        except OSError:
            previous.rename(target)              # put the working version back
            raise UpdateError("The new version could not be put in place. Nothing was changed.")
        return {"updated_to": version, "previous_kept_at": str(previous)}
    finally:
        shutil.rmtree(str(workspace), ignore_errors=True)
