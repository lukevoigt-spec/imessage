"""Where things live on disk.

The searchable copy of your messages is kept OUTSIDE this code folder, in
~/.imessage-explorer, so message data can never be committed to GitHub by
accident. Each location can be overridden with an environment variable, which
is how the tests point the tool at fictional data.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Folder holding the local database, saved topics and activity logs."""
    path = Path(os.environ.get("IMX_DATA_DIR", str(Path.home() / ".imessage-explorer")))
    path.mkdir(parents=True, exist_ok=True)
    # Private to your user account: other accounts on the Mac cannot read it.
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def index_db() -> Path:
    """The local, searchable database this tool builds."""
    return data_dir() / "messages.db"


def chat_db() -> Path:
    """Apple's Messages database. This tool only ever reads it."""
    return Path(os.environ.get("IMX_CHAT_DB", str(Path.home() / "Library" / "Messages" / "chat.db")))


def addressbook_dir() -> Path:
    """Apple's Contacts storage, used to turn phone numbers into names."""
    default = Path.home() / "Library" / "Application Support" / "AddressBook"
    return Path(os.environ.get("IMX_ADDRESSBOOK_DIR", str(default)))


def contacts_override_csv() -> Path:
    """Optional file of 'phone or email,name' lines that wins over Contacts."""
    return data_dir() / "contacts.csv"


def topics_file() -> Path:
    return data_dir() / "topics.json"


def activity_log() -> Path:
    """Record of AI requests and connected-tool searches. Never stores message text."""
    return data_dir() / "activity.jsonl"


def static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def launcher() -> Path:
    return Path(__file__).resolve().parent.parent / "imessage_explorer.py"
