"""Build a larger fictional Messages database, for trying the tool and for speed checks.

    python3 tests/make_demo.py demo 20000

Every person, number and message is invented. The same seed always produces
the same data.
"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixture  # noqa: E402

PEOPLE = [
    ("Avery", "Stone", "+12815550101"), ("Jordan", "Pike", "+12815550102"), ("Riley", "Marsh", "+12815550103"),
    ("Casey", "Holt", "+12815550104"), ("Morgan", "Reyes", "+12815550105"), ("Quinn", "Abbott", "+12815550106"),
    ("Devon", "Clarke", "+12815550107"), ("Harper", "Lin", "+12815550108"), ("Rowan", "Baptiste", "+12815550109"),
    ("Sage", "Okafor", "+12815550110"), ("Emerson", "Vale", "+12815550111"), ("Blake", "Nakamura", "+12815550112"),
]
GROUPS = [("Lake Weekend", [0, 1, 2]), ("Book Club", [3, 4, 5, 6]), ("Soccer Carpool", [7, 8])]

TOPICS = {
    "plans": ["Are we still on for dinner friday?", "What time is the reservation tonight?", "Lunch this weekend?",
              "Can we move dinner to saturday", "Party starts at seven", "Booked the italian place for friday"],
    "travel": ["The flight lands at noon", "Did you book the hotel yet?", "Airport pickup at 4", "Flight delayed again",
               "Found a great airbnb near the lake", "Passport renewal came in the mail", "Boarding now, see you soon"],
    "links": ["Look at this https://www.example.com/recipes/pie", "Reading https://news.example.org/story-of-the-day",
              "Directions https://maps.example.net/place/lake", "Listen to this https://sermons.example.org/sunday"],
    "money": ["Sent you the venmo for dinner", "Did the refund come through?", "The invoice is attached",
              "Can you zelle me for the tickets", "Budget review is on thursday"],
    "school": ["School pickup is at 3 today", "Homework folder is in the backpack", "Soccer practice moved to the upper field",
               "The teacher emailed about the field trip", "Tutor is running ten minutes late"],
    "health": ["Doctor appointment is at 9", "Feeling sick, staying home", "Picked up the prescription",
               "Dentist says no cavities", "Can you grab medicine on the way home"],
    "chatter": ["Sounds good", "On my way", "Running late", "Love it", "Call you later", "That is hilarious",
                "Thinking about the mountain cabin again", "Did you see the game last night?", "Good morning",
                "Be there in ten", "Thank you so much", "No worries at all", "Perfect", "Let me check and get back to you"],
    "warm": ["Love you", "Miss you already", "So proud of you", "Praying for you today", "You got this",
             "Thank you for everything, it really meant a lot to me and I will not forget it any time soon",
             "That was the best weekend we have had in a long time and I am so glad we finally made it happen"],
    "rough": ["Sorry I snapped earlier", "I am so frustrated with this", "That was a terrible day",
              "Ugh I hate being late", "I'm worried about tomorrow", "This is really annoying",
              "Honestly I am upset about how that went and I do not want to argue about it again tonight"],
}
# A catchphrase each of the first few people uses, so signature phrases have something to find.
CATCHPHRASES = {1: "Roger that captain", 2: "Sending the spreadsheet now", 3: "Classic move honestly",
                4: "Coffee first then decisions", 5: "Per my last text"}
EMOJI = ["\U0001F602", "\U0001F64F", "\U0001F60A", "❤️", "\U0001F44D", "\U0001F389", "✈️", "\U0001F382", ""]


def build(folder: Path, total: int, seed: int = 7) -> Path:
    rng = random.Random(seed)
    folder.mkdir(parents=True, exist_ok=True)
    chat_db = folder / "chat.db"
    make_fixture.build_chat_db(chat_db)          # creates the tables
    db = sqlite3.connect(str(chat_db))
    for table in ("message", "handle", "chat", "chat_handle_join", "chat_message_join", "attachment", "message_attachment_join"):
        db.execute("DELETE FROM %s" % table)
    for i, (_first, _last, number) in enumerate(PEOPLE, start=1):
        db.execute("INSERT INTO handle VALUES (?, ?, 'iMessage')", (i, number))
        db.execute("INSERT INTO chat VALUES (?, ?, ?, '', 45)", (i, "iMessage;-;" + number, number))
        db.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (i, i))
    for g, (name, members) in enumerate(GROUPS, start=len(PEOPLE) + 1):
        db.execute("INSERT INTO chat VALUES (?, ?, ?, ?, 43)", (g, "iMessage;+;chat%d" % g, "chat%d" % g, name))
        for member in members:
            db.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (g, member + 1))

    # Busier contacts first; a few people account for most messages, as in real life.
    weights = [1.0 / (rank + 1) for rank in range(len(PEOPLE) + len(GROUPS))]
    chats = list(range(1, len(PEOPLE) + len(GROUPS) + 1))
    start = datetime(2023, 1, 1, 8, 0)
    span_minutes = int((datetime(2025, 6, 30) - start).total_seconds() // 60)
    # Decide the length of each short back-and-forth first, so the messages cover the whole period.
    sizes = []
    while sum(sizes) < total:
        sizes.append(rng.randint(2, 6))
    moments = sorted(rng.randrange(span_minutes) for _ in sizes)
    rows, joins, rowid = [], [], 0
    for moment, size in zip(moments, sizes):
        when = start + timedelta(minutes=moment)
        if when.hour < 7:                       # few messages overnight
            when = when.replace(hour=rng.choice([8, 9, 12, 17, 19, 20, 21]))
        chat = rng.choices(chats, weights)[0]
        # Blake stops writing in mid-2024 (gone quiet); Sage mostly appears from 2025 (growing).
        if chat == 12 and when >= datetime(2024, 6, 1):
            chat = 1
        if chat == 10 and when < datetime(2025, 1, 1) and rng.random() < 0.85:
            chat = 2
        if chat == 11 and when >= datetime(2025, 1, 1) and rng.random() < 0.7:
            chat = 3                               # Emerson fades
        rough_weight = 2.5 if (when.year == 2024 and when.month in (2, 3)) else 0.6   # a tense stretch in early 2024
        topic = rng.choices(list(TOPICS), [3, 2 + (when.month in (5, 6, 7)) * 3, 0.6, 1, 2, 1, 6, 1.5, rough_weight])[0]
        from_me = rng.random() < 0.45
        for _ in range(size):
            rowid += 1
            when += timedelta(seconds=rng.choice([20, 45, 90, 180, 600, 1500]))
            text = rng.choice(TOPICS[topic if rng.random() < 0.6 else "chatter"])
            if not from_me and chat in CATCHPHRASES and rng.random() < 0.06:
                text = CATCHPHRASES[chat]
            emoji = rng.choice(EMOJI) if rng.random() < 0.35 and topic != "rough" else ""
            text = (text + " " + emoji).strip()
            if chat > len(PEOPLE):
                members = GROUPS[chat - len(PEOPLE) - 1][1]
                handle = 0 if from_me else rng.choice(members) + 1
            else:
                handle = chat
            body_only = rng.random() < 0.5      # half the text lives only in attributedBody, as on real Macs
            rows.append((rowid, "demo-%07d" % rowid, None if body_only else text,
                         make_fixture.typedstream(text) if body_only else None,
                         int((when.timestamp() - make_fixture.APPLE_EPOCH_OFFSET) * 1_000_000_000),
                         1 if from_me else 0, handle, "iMessage", 0, 0, 0))
            joins.append((chat, rowid))
            if rng.random() < 0.7:
                from_me = not from_me
            if rowid >= total:
                break
        if rowid >= total:
            break
    db.executemany("INSERT INTO message %s VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)" % make_fixture.MESSAGE_COLUMNS, rows)
    db.executemany("INSERT INTO chat_message_join VALUES (?, ?)", joins)

    # Extra details newer versions of macOS record: read times, reactions, direct replies,
    # edits, unsends, attachments and voice messages. All invented, like everything else here.
    second = 1_000_000_000
    chat_of = dict((rowid, chat) for chat, rowid in joins)
    reactions, updates, attachments = [], [], []
    next_id = rowid
    previous_in_chat = {}
    for (row_id, guid, _text, _body, date, from_me, _handle, _service, _att, _react, _item) in rows:
        chat = chat_of[row_id]
        # I read most of what I receive. People 1 to 4 share read receipts for what I send.
        if (not from_me and rng.random() < 0.7) or (from_me and chat <= 4 and rng.random() < 0.8):
            updates.append(("date_read", date + rng.choice([10, 40, 120, 600, 3600, 7200, 90000]) * second, row_id))
        if rng.random() < 0.06:                 # the other person reacts
            next_id += 1
            kind = rng.choices([2000, 2001, 2002, 2003, 2004, 2005], [5, 6, 1, 6, 2, 1])[0]
            sender = 0 if from_me else 1
            handle = chat if sender == 0 and chat <= len(PEOPLE) else 0
            reactions.append((next_id, "demo-react-%07d" % next_id, "Reacted to a message", None, date + 30 * second,
                              sender, handle, "iMessage", 0, kind, 0, "p:0/" + guid, chat))
        if chat in previous_in_chat and rng.random() < 0.02:
            updates.append(("thread_originator_guid", previous_in_chat[chat], row_id))
        if rng.random() < 0.005:
            updates.append(("date_edited", date + 60 * second, row_id))
        if rng.random() < 0.001:
            updates.append(("date_retracted", date + 90 * second, row_id))
        roll = rng.random()
        if roll < 0.04:
            attachments.append((row_id, "image/jpeg"))
        elif roll < 0.045:
            attachments.append((row_id, "video/mp4"))
        elif roll < 0.048:
            attachments.append((row_id, "audio/x-m4a"))
            updates.append(("is_audio_message", 1, row_id))
        previous_in_chat[chat] = guid
    db.executemany("INSERT INTO message %s VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)" % make_fixture.MESSAGE_COLUMNS,
                   [r[:11] for r in reactions])
    db.executemany("UPDATE message SET associated_message_guid = ? WHERE ROWID = ?", [(r[11], r[0]) for r in reactions])
    db.executemany("INSERT INTO chat_message_join VALUES (?, ?)", [(r[12], r[0]) for r in reactions])
    for column in ("date_read", "thread_originator_guid", "date_edited", "date_retracted", "is_audio_message"):
        db.executemany("UPDATE message SET %s = ? WHERE ROWID = ?" % column,
                       [(value, row_id) for name, value, row_id in updates if name == column])
    for number, (row_id, mime) in enumerate(attachments, start=1):
        db.execute("INSERT INTO attachment VALUES (?, ?, 0)", (number, mime))
        db.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (row_id, number))
    db.commit()
    db.close()

    book = folder / "AddressBook" / "Sources" / "FICTIONAL-SOURCE"
    book.mkdir(parents=True, exist_ok=True)
    ab_path = book / "AddressBook-v22.abcddb"
    if ab_path.exists():
        ab_path.unlink()
    ab = sqlite3.connect(str(ab_path))
    ab.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
    """)
    for i, (first, last, number) in enumerate(PEOPLE, start=1):
        ab.execute("INSERT INTO ZABCDRECORD VALUES (?, ?, ?, NULL, NULL)", (i, first, last))
        ab.execute("INSERT INTO ZABCDPHONENUMBER VALUES (?, ?, ?)", (i, i, number))
    ab.commit()
    ab.close()
    return chat_db


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo")
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    path = build(target, count)
    made = sqlite3.connect(str(path)).execute("SELECT COUNT(*) FROM message").fetchone()[0]
    print("Fictional demo data (%s messages) written to %s" % (format(made, ","), path))
    print("Try it with:")
    print("  IMX_CHAT_DB=%s IMX_ADDRESSBOOK_DIR=%s IMX_DATA_DIR=%s python3 imessage_explorer.py"
          % (path, target / "AddressBook", target / "data"))
