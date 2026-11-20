"""Build and update the local searchable database from Apple's Messages database.

Importing is additive: messages already imported are kept even if they are
later deleted from the Mac, and running the import again only adds what is new
or updates what changed. Each message is identified by Apple's own unique id
(guid), so importing twice never creates duplicates.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

from . import extract, features, paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT NOT NULL UNIQUE,      -- Apple's unique id for the message
    ts INTEGER NOT NULL,            -- when it was sent, Unix seconds
    day TEXT NOT NULL,              -- YYYY-MM-DD in this Mac's time zone
    week TEXT NOT NULL,             -- the Monday of that week, YYYY-MM-DD
    month TEXT NOT NULL,            -- YYYY-MM
    year TEXT NOT NULL,             -- YYYY
    hour INTEGER NOT NULL,          -- 0-23, local time
    dow INTEGER NOT NULL,           -- 0 = Monday ... 6 = Sunday
    from_me INTEGER NOT NULL,       -- 1 if you sent it
    sender TEXT NOT NULL,           -- 'Me', a contact name, or a number/email
    counterpart TEXT NOT NULL,      -- who the conversation is with: a person, or a group name
    is_group INTEGER NOT NULL,      -- 1 for group chats
    chat_id INTEGER,                -- Apple's id for the conversation
    service TEXT,                   -- iMessage, SMS or RCS
    kind TEXT NOT NULL,             -- 'text', 'attachment' (no words) or 'reaction' (tapback)
    has_attachment INTEGER NOT NULL,
    text TEXT NOT NULL,
    tone REAL NOT NULL DEFAULT 0,       -- -1 (very negative) to +1 (very positive); see features.py
    words INTEGER NOT NULL DEFAULT 0,   -- number of words
    flags INTEGER NOT NULL DEFAULT 0,   -- expression and style markers; see features.py
    react_to TEXT,                      -- for a reaction: Apple's id of the message it reacts to
    react_type INTEGER NOT NULL DEFAULT 0,  -- for a reaction: 2000 love, 2001 like, 2002 dislike, 2003 laugh, 2004 emphasis, 2005 question
    read_ts INTEGER,                    -- when the message was read, if this Mac recorded it
    reply_to TEXT,                      -- Apple's id of the message this one replies to directly
    att INTEGER NOT NULL DEFAULT 0      -- kinds of attachment carried; see features.py
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (ts);
CREATE INDEX IF NOT EXISTS idx_messages_counterpart ON messages (counterpart, ts);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_day ON messages (day);

-- Full-text search index over the message text.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
"""

UPSERT = """
INSERT INTO messages (guid, ts, day, week, month, year, hour, dow, from_me, sender, counterpart,
                      is_group, chat_id, service, kind, has_attachment, text, tone, words, flags,
                      react_to, react_type, read_ts, reply_to, att)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(guid) DO UPDATE SET
    ts=excluded.ts, day=excluded.day, week=excluded.week, month=excluded.month, year=excluded.year,
    hour=excluded.hour, dow=excluded.dow, from_me=excluded.from_me, sender=excluded.sender,
    counterpart=excluded.counterpart, is_group=excluded.is_group, chat_id=excluded.chat_id,
    service=excluded.service, kind=excluded.kind, has_attachment=excluded.has_attachment,
    text=excluded.text, tone=excluded.tone, words=excluded.words, flags=excluded.flags,
    react_to=excluded.react_to, react_type=excluded.react_type, read_ts=excluded.read_ts,
    reply_to=excluded.reply_to, att=excluded.att
"""

ME = "Me"


def connect(path: Optional[Path] = None, readonly: bool = False) -> sqlite3.Connection:
    """Open the local database. Searching and statistics always open it read-only."""
    path = path or paths.index_db()
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    else:
        conn = sqlite3.connect(str(path), timeout=60)
        conn.executescript(SCHEMA)
        _upgrade(conn)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    return conn


# Bumped whenever a new import is needed to fill in newly added measurements.
SCHEMA_VERSION = 3
NEW_COLUMNS = (("tone", "REAL NOT NULL DEFAULT 0"), ("words", "INTEGER NOT NULL DEFAULT 0"),
               ("flags", "INTEGER NOT NULL DEFAULT 0"), ("react_to", "TEXT"),
               ("react_type", "INTEGER NOT NULL DEFAULT 0"), ("read_ts", "INTEGER"), ("reply_to", "TEXT"),
               ("att", "INTEGER NOT NULL DEFAULT 0"))


def _upgrade(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created. Existing messages are kept."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    for name, definition in NEW_COLUMNS:
        if name not in existing:
            conn.execute("ALTER TABLE messages ADD COLUMN %s %s" % (name, definition))
    conn.commit()


def is_current(path: Optional[Path] = None) -> bool:
    """True when the database was last imported by this version, so every measurement is filled in."""
    path = path or paths.index_db()
    try:
        conn = connect(path, readonly=True)
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        conn.close()
        return row is not None and int(row[0]) >= SCHEMA_VERSION
    except (sqlite3.Error, ValueError):
        return False


def is_ready(path: Optional[Path] = None) -> bool:
    """True once at least one import has completed."""
    path = path or paths.index_db()
    if not path.exists():
        return False
    try:
        conn = connect(path, readonly=True)
        row = conn.execute("SELECT value FROM meta WHERE key = 'last_import'").fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error:
        return False


def _time_parts(ts: int) -> tuple:
    """Break a timestamp into the local-time pieces the statistics group by."""
    local = datetime.fromtimestamp(ts)
    monday = local.date() - timedelta(days=local.weekday())
    return (
        local.strftime("%Y-%m-%d"),
        monday.strftime("%Y-%m-%d"),
        local.strftime("%Y-%m"),
        local.strftime("%Y"),
        local.hour,
        local.weekday(),
    )


def run_import(
    chat_db: Optional[Path] = None,
    index_path: Optional[Path] = None,
    progress: Callable[[str], None] = print,
) -> Dict[str, object]:
    """Copy everything readable from Messages into the local database.

    Returns a report of what happened, including how many messages could not
    be read, so a problem is visible instead of silently missing data.
    """
    started = time.time()
    source = extract.Source(chat_db or paths.chat_db())
    contacts = extract.load_contacts(paths.addressbook_dir(), paths.contacts_override_csv())
    handles = source.handles()
    chats = source.chats()
    message_chats = source.message_chats()
    attachment_kinds = source.attachment_kinds()
    probe = source.probe()

    def person(handle_id: Optional[int]) -> str:
        """Best available name for a handle: contact name, else the raw number/email."""
        raw = handles.get(handle_id or 0, "")
        if not raw:
            return "Unknown"
        return contacts.get(extract.normalize_handle(raw), raw)

    # Decide once what each conversation is called.
    chat_names: Dict[int, str] = {}
    for chat_id, chat in chats.items():
        people = sorted({person(h) for h in chat["participants"]})
        if chat["is_group"]:
            chat_names[chat_id] = chat["display_name"] or ", ".join(people) or chat["identifier"] or "Group chat"
        else:
            chat_names[chat_id] = people[0] if people else (
                contacts.get(extract.normalize_handle(chat["identifier"]), chat["identifier"]) or "Unknown"
            )

    matched = sum(1 for raw in set(handles.values()) if extract.normalize_handle(raw) in contacts)
    report: Dict[str, object] = {
        "source_messages": source.message_count(),
        "imported": 0,
        "text_from_text_column": 0,
        "text_from_attributed_body": 0,
        "undecoded_bodies": 0,
        "reactions": 0,
        "attachments_without_text": 0,
        "skipped_system_events": 0,
        "skipped_no_date": 0,
        "handles_total": len(set(handles.values())),
        "handles_with_contact_name": matched,
    }

    index = connect(index_path)
    progress("Reading %s messages from Messages..." % format(report["source_messages"], ","))
    batch = []
    for row in source.messages():
        # item_type other than 0 is a group event (someone renamed the chat, joined, left).
        if row["item_type"]:
            report["skipped_system_events"] += 1
            continue
        ts = extract.apple_time_to_unix(row["date"])
        if ts is None:
            report["skipped_no_date"] += 1
            continue

        text = row["text"]
        if text:
            report["text_from_text_column"] += 1
        elif row["attributedBody"] is not None:
            text = extract.decode_attributed_body(row["attributedBody"])
            if text is None:
                report["undecoded_bodies"] += 1
            else:
                report["text_from_attributed_body"] += 1
        text = (text or "").replace(extract.OBJECT_REPLACEMENT, "").strip()

        reaction_type = row["associated_message_type"] or 0
        has_attachment = 1 if row["cache_has_attachments"] else 0
        if extract.REACTION_TYPE_MIN <= reaction_type <= extract.REACTION_TYPE_MAX:
            kind = "reaction"
            report["reactions"] += 1
        elif not text:
            kind = "attachment"
            report["attachments_without_text"] += 1
        else:
            kind = "text"

        chat_id = message_chats.get(row["rowid"])
        chat = chats.get(chat_id)
        from_me = 1 if row["is_from_me"] else 0
        sender = ME if from_me else person(row["handle_id"])
        if chat is not None:
            counterpart = chat_names[chat_id]
            is_group = 1 if chat["is_group"] else 0
        else:
            # A message not linked to any conversation: file it under the other person.
            counterpart = person(row["handle_id"])
            is_group = 0

        tone, word_count, flags = features.measure(text) if kind != "reaction" else (0.0, 0, 0)
        if row["date_edited"]:
            flags |= features.EDITED
        if row["date_retracted"]:
            flags |= features.UNSENT
        if row["is_audio_message"]:
            flags |= features.VOICE
        read_ts = extract.apple_time_to_unix(row["date_read"])
        if read_ts is not None and read_ts < ts:
            read_ts = None                        # a read time before the send time is not usable
        batch.append(
            (row["guid"], ts) + _time_parts(ts)
            + (from_me, sender, counterpart, is_group, chat_id, row["service"], kind, has_attachment, text,
               tone, word_count, flags,
               extract.reaction_target(row["associated_message_guid"]) if kind == "reaction" else None,
               reaction_type if kind == "reaction" else 0,
               read_ts, row["thread_originator_guid"] or None, attachment_kinds.get(row["rowid"], 0))
        )
        if len(batch) >= 5000:
            index.executemany(UPSERT, batch)
            report["imported"] += len(batch)
            batch = []
            progress("  %s imported..." % format(report["imported"], ","))
    if batch:
        index.executemany(UPSERT, batch)
        report["imported"] += len(batch)
    source.close()

    progress("Building the search index...")
    index.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    total, first_day, last_day = index.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM messages").fetchone()
    report.update({
        "total_in_local_database": total,
        "first_day": first_day,
        "last_day": last_day,
        "signals": probe,
        "seconds": round(time.time() - started, 1),
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    index.execute("INSERT OR REPLACE INTO meta VALUES ('last_import', ?)", (json.dumps(report),))
    index.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    index.commit()
    index.execute("ANALYZE")
    index.close()
    return report


def last_report(conn: sqlite3.Connection) -> Dict[str, object]:
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_import'").fetchone()
    return json.loads(row[0]) if row else {}


def describe_report(report: Dict[str, object]) -> str:
    """Plain-language summary of an import, with a warning if anything was unreadable."""
    lines = [
        "Import finished in %s seconds." % report.get("seconds"),
        "  Messages in your local database: %s (%s to %s)"
        % (format(report.get("total_in_local_database", 0), ","), report.get("first_day"), report.get("last_day")),
        "  Read this run: %s  (tapbacks: %s, attachments with no words: %s)"
        % (format(report.get("imported", 0), ","), format(report.get("reactions", 0), ","),
           format(report.get("attachments_without_text", 0), ",")),
        "  People matched to a contact name: %s of %s"
        % (report.get("handles_with_contact_name"), report.get("handles_total")),
    ]
    undecoded = report.get("undecoded_bodies", 0)
    if undecoded:
        lines.append(
            "  WARNING: %s messages had text this tool could not read. They are stored without "
            "their words. Tell whoever maintains this tool; the message format may have changed."
            % format(undecoded, ",")
        )
    return "\n".join(lines)
