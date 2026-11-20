"""Read Apple's Messages database (chat.db) and Contacts. Read-only, always.

Three things make chat.db awkward, and each is handled here in one place:

1. Dates are counted from 1 January 2001, in nanoseconds on current macOS and
   in whole seconds on old rows.
2. On recent macOS the visible text of many messages is not in the `text`
   column. It is packed inside a binary blob called `attributedBody`.
3. People are stored as phone numbers and email addresses, not names. Names
   come from the separate Contacts database.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Optional

# Seconds between the Unix epoch (1970) and Apple's epoch (2001-01-01 UTC).
APPLE_EPOCH_OFFSET = 978307200

# Any Apple timestamp above this is in nanoseconds. A seconds value will not
# reach it for thousands of years; a nanoseconds value passed it in 2001.
NANOSECOND_THRESHOLD = 100_000_000_000

# Tapbacks ("Loved", "Liked", ...) are stored as their own message rows.
# 2000-2999 adds a reaction, 3000-3999 removes one, 1000 is a sticker reaction.
REACTION_TYPE_MIN = 1000
REACTION_TYPE_MAX = 3999

# Placeholder character Apple puts in the text where an attachment sits.
OBJECT_REPLACEMENT = "￼"

GROUP_CHAT_STYLE = 43  # chat.style: 43 = group, 45 = one-to-one


class SourceError(Exception):
    """The Messages database could not be read. The message says why, plainly."""


def apple_time_to_unix(value: Optional[int]) -> Optional[int]:
    """Convert an Apple timestamp to Unix seconds. Returns None for 'no date'."""
    if not value:
        return None
    if value > NANOSECOND_THRESHOLD:
        value = value / 1_000_000_000
    return int(value + APPLE_EPOCH_OFFSET)


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Pull the plain message text out of an `attributedBody` blob.

    The blob is Apple's "typedstream" archive of a styled string. We do not
    need the styling, only the text, which sits right after the first
    "NSString" marker as: a few header bytes, a "+" byte, a length, then the
    UTF-8 text. The length is one byte when under 128; otherwise 0x81 followed
    by a 2-byte length, or 0x82 followed by a 4-byte length (little-endian).

    Returns None when the blob does not match, so the importer can count and
    report how many messages it could not read instead of guessing.
    """
    if not blob:
        return None
    marker = blob.find(b"NSString")
    if marker == -1:
        return None
    # The "+" type tag appears within a few bytes after the class name.
    search_from = marker + len(b"NSString")
    plus = blob.find(b"+", search_from, search_from + 16)
    if plus == -1:
        return None
    pos = plus + 1
    if pos >= len(blob):
        return None
    first = blob[pos]
    if first == 0x81:
        length = int.from_bytes(blob[pos + 1:pos + 3], "little")
        pos += 3
    elif first == 0x82:
        length = int.from_bytes(blob[pos + 1:pos + 5], "little")
        pos += 5
    else:
        length = first
        pos += 1
    raw = blob[pos:pos + length]
    if len(raw) != length:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def reaction_target(value: Optional[str]) -> Optional[str]:
    """The id of the message a reaction points at.

    Apple stores it with a prefix naming the part of the message, such as
    "p:0/ABCD-1234" or "bp:ABCD-1234". Only the id after the prefix is kept.
    """
    if not value:
        return None
    if "/" in value:
        return value.rsplit("/", 1)[1] or None
    if value.startswith("bp:"):
        return value[3:] or None
    return value


def normalize_handle(handle: Optional[str]) -> str:
    """Reduce a phone number or email to a form that matches across sources.

    "+1 (281) 555-0142" and "2815550142" are the same person, so phone numbers
    are reduced to their last 10 digits. Emails are lower-cased. Short codes
    (5 or 6 digit senders) are kept as their digits.
    """
    if not handle:
        return ""
    handle = handle.strip()
    if "@" in handle:
        return handle.lower()
    digits = re.sub(r"\D", "", handle)
    if not digits:
        return handle.lower()
    return digits[-10:] if len(digits) >= 10 else digits


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open a database so that writing to it is impossible."""
    if not path.exists():
        raise SourceError(
            "Could not find the Messages database at %s. This tool must run on a Mac "
            "that is signed into Messages." % path
        )
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        raise SourceError(
            "macOS blocked access to the Messages database (%s). Open System Settings, "
            "go to Privacy & Security, then Full Disk Access, and switch on the app you "
            "are running this from (usually Terminal). Then quit that app fully and try again."
            % exc
        )
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def load_contacts(addressbook_dir: Path, override_csv: Optional[Path] = None) -> Dict[str, str]:
    """Build a lookup from normalized phone/email to a person's name.

    Reads every Contacts database it finds (one per synced account). Any
    failure here is non-fatal: people simply show as their number or email.
    """
    names: Dict[str, str] = {}
    databases: List[Path] = []
    if addressbook_dir.exists():
        databases = sorted(addressbook_dir.glob("AddressBook-v22.abcddb"))
        databases += sorted(addressbook_dir.glob("Sources/*/AddressBook-v22.abcddb"))
    for db_path in databases:
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
            people: Dict[int, str] = {}
            for pk, first, last, nick, org in conn.execute(
                "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION FROM ZABCDRECORD"
            ):
                full = " ".join(part for part in (first, last) if part).strip()
                people[pk] = full or nick or org or ""
            for owner, number in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                key = normalize_handle(number)
                if key and people.get(owner):
                    names.setdefault(key, people[owner])
            for owner, address in conn.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                key = normalize_handle(address)
                if key and people.get(owner):
                    names.setdefault(key, people[owner])
            conn.close()
        except sqlite3.Error:
            continue
    if override_csv and override_csv.exists():
        with open(str(override_csv), newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if len(row) >= 2 and row[0].strip() and not row[0].lstrip().startswith("#"):
                    names[normalize_handle(row[0])] = row[1].strip()
    return names


class Source:
    """A read-only view of chat.db that yields clean, plain records."""

    def __init__(self, chat_db: Path):
        self.conn = _open_readonly(chat_db)
        try:
            self.message_cols = _columns(self.conn, "message")
            self.chat_cols = _columns(self.conn, "chat")
        except sqlite3.Error as exc:
            raise SourceError("The Messages database could not be read: %s" % exc)
        if "guid" not in self.message_cols:
            raise SourceError("This does not look like a Messages database (no message table).")

    def close(self) -> None:
        self.conn.close()

    def handles(self) -> Dict[int, str]:
        """Map each handle row to its phone number or email."""
        return {row["ROWID"]: row["id"] for row in self.conn.execute("SELECT ROWID, id FROM handle")}

    def chats(self) -> Dict[int, dict]:
        """Every conversation, with its participants and whether it is a group."""
        style = "style" if "style" in self.chat_cols else "NULL"
        display = "display_name" if "display_name" in self.chat_cols else "NULL"
        chats: Dict[int, dict] = {}
        for row in self.conn.execute(
            "SELECT ROWID, chat_identifier, %s AS style, %s AS display_name FROM chat" % (style, display)
        ):
            chats[row["ROWID"]] = {
                "identifier": row["chat_identifier"] or "",
                "display_name": row["display_name"] or "",
                "style": row["style"],
                "participants": [],
            }
        for chat_id, handle_id in self.conn.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
            if chat_id in chats:
                chats[chat_id]["participants"].append(handle_id)
        for chat in chats.values():
            if chat["style"] is not None:
                chat["is_group"] = chat["style"] == GROUP_CHAT_STYLE
            else:
                chat["is_group"] = len(chat["participants"]) > 1
        return chats

    def message_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def message_chats(self) -> Dict[int, int]:
        """Which conversation each message belongs to.

        Loaded into memory in one pass. Looking it up per message inside the
        database is very slow when the database has no index for it.
        """
        mapping: Dict[int, int] = {}
        for chat_id, message_id in self.conn.execute("SELECT chat_id, message_id FROM chat_message_join"):
            # A message can rarely be listed under two conversations; keep the first one seen.
            mapping.setdefault(message_id, chat_id)
        return mapping

    def messages(self) -> Iterator[sqlite3.Row]:
        """Every message row. Columns missing on older macOS read as NULL."""

        def col(name: str) -> str:
            return "m.%s" % name if name in self.message_cols else "NULL AS %s" % name

        sql = (
            "SELECT m.ROWID AS rowid, m.guid, m.text, %s, m.date, m.is_from_me, m.handle_id, "
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM message m"
            % (
                col("attributedBody"),
                col("service"),
                col("cache_has_attachments"),
                col("associated_message_type"),
                col("item_type"),
                # Optional fields. Which of these exist depends on the macOS version.
                col("associated_message_guid"),
                col("date_read"),
                col("thread_originator_guid"),
                col("date_edited"),
                col("date_retracted"),
                col("is_audio_message"),
            )
        )
        return self.conn.execute(sql)

    def _has_table(self, name: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None

    def attachment_kinds(self) -> Dict[int, int]:
        """For each message with attachments, which kinds it carries (photo, video, audio, other).

        Returns an empty mapping when this Mac's database has no attachment tables.
        """
        from . import features
        if not (self._has_table("attachment") and self._has_table("message_attachment_join")):
            return {}
        columns = _columns(self.conn, "attachment")
        sticker = "a.is_sticker" if "is_sticker" in columns else "0"
        kinds: Dict[int, int] = {}
        try:
            rows = self.conn.execute(
                "SELECT j.message_id, a.mime_type, %s FROM message_attachment_join j "
                "JOIN attachment a ON a.ROWID = j.attachment_id" % sticker)
            for message_id, mime, is_sticker in rows:
                if is_sticker:
                    continue                      # stickers are decoration, not something shared
                mime = (mime or "").lower()
                if mime.startswith("image/"):
                    kind = features.PHOTO
                elif mime.startswith("video/"):
                    kind = features.VIDEO
                elif mime.startswith("audio/"):
                    kind = features.AUDIO
                else:
                    kind = features.OTHER_FILE
                kinds[message_id] = kinds.get(message_id, 0) | kind
        except sqlite3.Error:
            return {}
        return kinds

    def probe(self) -> List[dict]:
        """Which optional fields this Mac records, and how much they are filled in. Reads no message text."""
        total = self.message_count()
        checks = [
            ("Reactions linked to their message", "associated_message_guid",
             "associated_message_guid IS NOT NULL AND associated_message_guid != ''"),
            ("When you read a message you received", "date_read", "date_read > 0 AND is_from_me = 0"),
            ("When they read a message you sent", "date_read", "date_read > 0 AND is_from_me = 1"),
            ("Direct replies to a specific message", "thread_originator_guid",
             "thread_originator_guid IS NOT NULL AND thread_originator_guid != ''"),
            ("Edited messages", "date_edited", "date_edited > 0"),
            ("Unsent messages", "date_retracted", "date_retracted > 0"),
            ("Voice messages", "is_audio_message", "is_audio_message = 1"),
        ]
        report = []
        for label, column, condition in checks:
            entry = {"signal": label, "recorded": column in self.message_cols, "messages": 0, "first_seen": None}
            if entry["recorded"]:
                count, first = self.conn.execute("SELECT COUNT(*), MIN(date) FROM message WHERE %s" % condition).fetchone()
                entry["messages"] = count
                first_unix = apple_time_to_unix(first) if count else None
                if first_unix:
                    from datetime import datetime
                    entry["first_seen"] = datetime.fromtimestamp(first_unix).strftime("%Y-%m-%d")
            report.append(entry)
        kinds = self.attachment_kinds()
        report.append({"signal": "Attachments by kind (photo, video, audio, file)",
                       "recorded": self._has_table("attachment") and self._has_table("message_attachment_join"),
                       "messages": len(kinds), "first_seen": None})
        for entry in report:
            entry["share"] = round(100.0 * entry["messages"] / total, 1) if total else 0.0
        return report
