"""Build a small, entirely fictional Messages database and Contacts database for testing.

No real person, number or message appears here. The layout copies the parts of
Apple's real databases that the tool reads, including the awkward cases:
text stored only in the binary `attributedBody`, old rows dated in seconds,
tapbacks, attachment-only messages, group chats and group system events.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

APPLE_EPOCH_OFFSET = 978307200

# The columns every fictional message supplies. Optional fields are filled in afterwards.
MESSAGE_COLUMNS = ("(ROWID, guid, text, attributedBody, date, is_from_me, handle_id, service, "
                   "cache_has_attachments, associated_message_type, item_type)")

# Fictional contacts. The 555-01xx range is reserved for fiction.
AVERY = "+12815550101"      # in Contacts as "Avery Stone"
JORDAN = "jordan.pike@example.com"   # in Contacts as "Jordan Pike"
UNKNOWN = "+12815550199"    # not in Contacts: shows as the raw number
RILEY = "(281) 555-0102"    # same person stored with different punctuation in Contacts


def typedstream(text: str) -> bytes:
    """Wrap text the way macOS stores it in `attributedBody`."""
    raw = text.encode("utf-8")
    if len(raw) < 128:
        length = bytes([len(raw)])
    elif len(raw) < 65536:
        length = b"\x81" + len(raw).to_bytes(2, "little")
    else:
        length = b"\x82" + len(raw).to_bytes(4, "little")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw +
        b"\x86\x84\x02iI\x01" + bytes([min(len(raw), 127)]) +
        b"\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x84\x96\x96\x1d__kIMMessagePartAttributeName"
        b"\x86\x92\x84\x84\x84\x08NSNumber\x00\x84\x84\x07NSValue\x00\x94\x84\x01*\x84\x99\x99\x00\x86\x86\x86"
    )


def apple_ns(when: str) -> int:
    """'2025-03-01 09:00' in this machine's local time, as Apple nanoseconds."""
    unix = datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
    return int((unix - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def build_chat_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, style INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, date INTEGER,
            is_from_me INTEGER, handle_id INTEGER, service TEXT, cache_has_attachments INTEGER,
            associated_message_type INTEGER, item_type INTEGER,
            associated_message_guid TEXT, date_read INTEGER DEFAULT 0, thread_originator_guid TEXT,
            date_edited INTEGER DEFAULT 0, date_retracted INTEGER DEFAULT 0, is_audio_message INTEGER DEFAULT 0
        );
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, mime_type TEXT, is_sticker INTEGER DEFAULT 0);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
    """)
    db.executemany("INSERT INTO handle VALUES (?, ?, ?)", [
        (1, AVERY, "iMessage"), (2, JORDAN, "iMessage"), (3, UNKNOWN, "SMS"), (4, "+12815550102", "iMessage"),
    ])
    db.executemany("INSERT INTO chat VALUES (?, ?, ?, ?, ?)", [
        (1, "iMessage;-;" + AVERY, AVERY, "", 45),
        (2, "iMessage;-;" + JORDAN, JORDAN, "", 45),
        (3, "iMessage;+;chat100", "chat100", "Lake Weekend", 43),
        (4, "SMS;-;" + UNKNOWN, UNKNOWN, "", 45),
        (5, "iMessage;-;+12815550102", "+12815550102", "", 45),
    ])
    db.executemany("INSERT INTO chat_handle_join VALUES (?, ?)", [
        (1, 1), (2, 2), (3, 1), (3, 2), (4, 3), (5, 4),
    ])

    long_text = "This is a long message about the mountain cabin. " * 6  # over 127 bytes: tests the 2-byte length
    # (chat, when, from_me, handle, text, body_only, extras)
    rows = [
        # --- Avery: a morning conversation, then a new one that evening ---
        (1, "2025-03-03 09:00", 0, 1, "Are we still on for dinner friday?", False, {}),
        (1, "2025-03-03 09:01", 0, 1, "I can book the italian place", False, {}),
        (1, "2025-03-03 09:10", 1, 1, "Yes! Dinner friday works \U0001F60A", True, {}),          # my reply: 10 min after 09:00
        (1, "2025-03-03 09:12", 0, 1, "Perfect, booking it now", True, {}),                     # their reply: 2 min
        (1, "2025-03-03 09:13", 0, 1, 'Loved "Yes! Dinner friday works"', False, {"reaction": 2000}),
        (1, "2025-03-03 19:30", 1, 1, "Did you see the flight prices for June?", False, {}),    # new conversation, started by me
        (1, "2025-03-03 19:50", 0, 1, "The flight to Denver is cheap right now ✈️", True, {}),  # their reply: 20 min
        (1, "2025-03-04 08:00", 1, 1, long_text, True, {}),                                      # new conversation (12h gap)
        (1, "2025-03-04 08:05", 0, 1, "", False, {"attachment": True}),                          # photo, no words
        (1, "2025-03-04 08:06", 0, 1, "￼Cabin photo attached", True, {"attachment": True}),
        # --- Jordan (email handle) ---
        (2, "2025-04-10 14:00", 0, 2, "Can you review the budget before the meeting?", False, {}),
        (2, "2025-04-10 14:30", 1, 2, "Reviewing the budget now", False, {}),                    # my reply: 30 min
        (2, "2025-04-10 14:31", 0, 2, "Thank you \U0001F64F\U0001F64F", True, {}),               # their reply: 1 min
        (2, "2025-04-12 10:00", 0, 2, "Flight delayed, landing at noon", False, {}),             # new conversation, by them
        # --- Group chat ---
        (3, "2025-05-01 12:00", 0, 1, "Who is bringing the kayak to the lake?", False, {}),
        (3, "2025-05-01 12:02", 0, 2, "I will bring the kayak", False, {}),
        (3, "2025-05-01 12:05", 1, 0, "I'll handle dinner friday night at the lake", True, {}),
        (3, "2025-05-01 12:06", 0, 0, None, False, {"item_type": 2}),                            # group renamed: system event
        # --- Unknown SMS sender ---
        (4, "2025-05-02 07:00", 0, 3, "Your verification code is 000000", False, {"service": "SMS"}),
        # --- Riley (number formatted differently in Contacts) ---
        (5, "2025-06-01 18:00", 1, 4, "Happy birthday Riley! \U0001F382", False, {}),
        (5, "2025-06-01 18:20", 0, 4, "Thank you so much!!", False, {}),
        # --- Undecodable body: must be counted, not guessed ---
        (5, "2025-06-02 09:00", 0, 4, None, False, {"bad_body": True}),
    ]
    for index, (chat, when, from_me, handle, text, body_only, extra) in enumerate(rows, start=1):
        body = None
        text_col = text
        if extra.get("bad_body"):
            body, text_col = b"\x04\x0bstreamtyped-but-not-a-string", None
        elif body_only and text is not None:
            body, text_col = typedstream(text), None
        db.execute(
            "INSERT INTO message %s VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)" % MESSAGE_COLUMNS,
            (index, "guid-%03d" % index, text_col, body, apple_ns(when), from_me, handle,
             extra.get("service", "iMessage"), 1 if extra.get("attachment") else 0,
             extra.get("reaction", 0), extra.get("item_type", 0)),
        )
        db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, index))

    # Optional fields that newer versions of macOS record.
    extras = [
        # Avery's "Loved" tapback (message 5) points at my reply (message 3).
        ("UPDATE message SET associated_message_guid = 'p:0/guid-003' WHERE ROWID = 5", ()),
        # I read Avery's 09:00 message at 09:05, and Jordan's 14:00 message at 14:20.
        ("UPDATE message SET date_read = ? WHERE ROWID = 1", (apple_ns("2025-03-03 09:05"),)),
        ("UPDATE message SET date_read = ? WHERE ROWID = 11", (apple_ns("2025-04-10 14:20"),)),
        # Avery shares read receipts: they read my 09:10 reply at 09:11.
        ("UPDATE message SET date_read = ? WHERE ROWID = 3", (apple_ns("2025-03-03 09:11"),)),
        # Avery's "Perfect, booking it now" (message 4) is a direct reply to my message 3.
        ("UPDATE message SET thread_originator_guid = 'guid-003' WHERE ROWID = 4", ()),
        # I edited message 6. The unreadable message 22 was unsent by Riley.
        ("UPDATE message SET date_edited = ? WHERE ROWID = 6", (apple_ns("2025-03-03 19:31"),)),
        ("UPDATE message SET date_retracted = ? WHERE ROWID = 22", (apple_ns("2025-06-02 09:01"),)),
        # Message 9 is a photo, message 10 carries a video, and a sticker on message 2 is ignored.
        ("INSERT INTO attachment VALUES (1, 'image/jpeg', 0)", ()),
        ("INSERT INTO attachment VALUES (2, 'video/mp4', 0)", ()),
        ("INSERT INTO attachment VALUES (3, 'image/png', 1)", ()),
        ("INSERT INTO message_attachment_join VALUES (9, 1)", ()),
        ("INSERT INTO message_attachment_join VALUES (10, 2)", ()),
        ("INSERT INTO message_attachment_join VALUES (2, 3)", ()),
    ]
    for sql, values in extras:
        db.execute(sql, values)

    # An old row dated in whole seconds (pre-2017 macOS format), and one with no date at all.
    old_seconds = int(datetime(2015, 7, 4, 12, 0).timestamp() - APPLE_EPOCH_OFFSET)
    db.execute("INSERT INTO message %s VALUES (900, 'guid-old', 'Old message from 2015', NULL, ?, 0, 1, 'iMessage', 0, 0, 0)" % MESSAGE_COLUMNS,
               (old_seconds,))
    db.execute("INSERT INTO chat_message_join VALUES (1, 900)")
    db.execute("INSERT INTO message %s VALUES (901, 'guid-nodate', 'No date here', NULL, 0, 0, 1, 'iMessage', 0, 0, 0)" % MESSAGE_COLUMNS)
    db.execute("INSERT INTO chat_message_join VALUES (1, 901)")
    db.commit()
    db.close()


def build_addressbook(folder: Path) -> None:
    source = folder / "Sources" / "FICTIONAL-SOURCE"
    source.mkdir(parents=True, exist_ok=True)
    path = source / "AddressBook-v22.abcddb"
    if path.exists():
        path.unlink()
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
    """)
    db.executemany("INSERT INTO ZABCDRECORD VALUES (?, ?, ?, ?, ?)", [
        (1, "Avery", "Stone", None, None), (2, "Jordan", "Pike", None, None), (3, "Riley", "Marsh", None, None),
    ])
    db.executemany("INSERT INTO ZABCDPHONENUMBER VALUES (?, ?, ?)", [(1, 1, "(281) 555-0101"), (2, 3, RILEY)])
    db.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (1, 2, 'Jordan.Pike@Example.com')")
    db.commit()
    db.close()


def build(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    chat_db = folder / "chat.db"
    build_chat_db(chat_db)
    build_addressbook(folder / "AddressBook")
    return chat_db


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixture")
    print("Fictional test data written to", build(target))
