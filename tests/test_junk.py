"""Tests for setting aside automated senders: one-time codes, bank alerts, marketing.

The fictional history below has real people and several kinds of automated
sender. Each test says which rule it is checking.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
from imx import index, insights, junk, labels, query, reading, server, stats  # noqa: E402
from imx.query import Filters  # noqa: E402

# sender id -> messages as (when, from_me, text). Avery (+12815550101) is "Avery Stone" in the fictional Contacts.
HISTORY = {
    make_fixture.AVERY: [                                   # a real person: written to many times
        ("2025-01-01 09:00", 0, "Are we still on for dinner?"),
        ("2025-01-01 09:05", 1, "Yes, see you at seven"),
        ("2025-01-02 09:00", 0, "Here is the link https://example.com/menu"),
        ("2025-01-02 09:01", 1, "Looks great"),
        ("2025-01-03 09:00", 1, "Running late"),
        ("2025-01-03 09:02", 1, "Be there in ten"),
        ("2025-01-03 09:03", 1, "Parking now"),
        ("2025-01-03 09:04", 1, "Walking in"),
    ],
    "24273": [                                              # a bank's short code
        ("2025-02-01 10:00", 0, "Chase: You made a $42.17 debit card transaction with COFFEE SHOP on Feb 1."),
        ("2025-02-02 10:00", 0, "Chase: A $1,250.00 payment posted to your account ending 1234."),
        ("2025-02-03 10:00", 0, "Chase Fraud Alert: did you attempt a $900.00 purchase? Reply YES or NO."),
        ("2025-02-03 10:01", 1, "YES"),
    ],
    "+18005550144": [                                       # a long number that only ever sends codes
        ("2025-03-01 08:00", 0, "Your verification code is 482913. Do not share it."),
        ("2025-03-05 08:00", 0, "123456 is your one-time passcode."),
    ],
    "+18005550155": [                                       # marketing you replied STOP to
        ("2025-03-02 12:00", 0, "FLASH SALE! 40% off everything today only. Reply STOP to opt out."),
        ("2025-03-09 12:00", 0, "Last chance: free shipping ends tonight. Msg & data rates may apply."),
        ("2025-03-09 12:05", 1, "STOP"),
    ],
    "+18005550166": [                                       # a stranger, nothing automated-looking, never answered
        ("2025-04-01 15:00", 0, "Hey is this still your number?"),
    ],
    "+18005550177": [                                       # a new friend not yet in contacts: you wrote back
        ("2025-04-02 15:00", 0, "Hi, it's Sam from the gym"),
        ("2025-04-02 15:01", 1, "Hey Sam! Good to hear from you"),
    ],
    "deals@shop.example.com": [                             # an address that sends only links
        ("2025-04-03 09:00", 0, "Your order has shipped: https://shop.example.com/track/991"),
    ],
    "+18005550188": [                                       # a business you really talk to, mostly automated
        ("2025-05-01 09:00", 0, "Appointment reminder for May 3 at 2pm. Reply C to confirm. Reply STOP to opt out."),
        ("2025-05-01 09:01", 1, "C"),
        ("2025-05-10 09:00", 0, "Your prescription is ready for pickup. Reply STOP to opt out."),
        ("2025-05-20 09:00", 0, "Your appointment is confirmed. Text STOP to cancel reminders."),
        ("2025-06-01 09:00", 0, "Your refill is due. Reply STOP to opt out."),
        ("2025-06-10 09:00", 0, "Reminder: your balance of $20.00 is due. Reply STOP to end."),
        ("2025-06-10 09:05", 1, "Can I pay when I come in on Friday?"),
    ],
}
PERSON, BANK, CODES, MARKETING, STRANGER, FRIEND, SHOP, PHARMACY = (
    "Avery Stone", "24273", "+18005550144", "+18005550155", "+18005550166", "+18005550177", "deals@shop.example.com", "+18005550188")


def build(folder: Path, history=HISTORY) -> None:
    """Create a fictional Messages database holding `history`, and import it."""
    chat_db = make_fixture.build(folder / "fixture")
    db = sqlite3.connect(str(chat_db))
    for table in ("message", "chat_message_join", "chat", "handle", "chat_handle_join", "message_attachment_join", "attachment"):
        db.execute("DELETE FROM %s" % table)
    number = 0
    for sender_number, (sender, messages) in enumerate(history.items(), start=1):
        db.execute("INSERT INTO handle VALUES (?, ?, 'SMS')", (sender_number, sender))
        db.execute("INSERT INTO chat VALUES (?, ?, ?, '', 45)", (sender_number, "SMS;-;" + sender, sender))
        db.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (sender_number, sender_number))
        for when, from_me, text in messages:
            number += 1
            db.execute("INSERT INTO message %s VALUES (?, ?, ?, NULL, ?, ?, ?, 'SMS', 0, 0, 0)" % make_fixture.MESSAGE_COLUMNS,
                       (number, "guid-%04d" % number, text, make_fixture.apple_ns(when), from_me, sender_number))
            db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (sender_number, number))
    db.commit()
    db.close()
    os.environ["IMX_DATA_DIR"] = str(folder / "data")
    os.environ["IMX_CHAT_DB"] = str(chat_db)
    os.environ["IMX_ADDRESSBOOK_DIR"] = str(folder / "fixture" / "AddressBook")
    index.run_import(progress=lambda _line: None)


class SignTests(unittest.TestCase):
    def test_messages_that_read_as_automated(self):
        automated = [
            "Your verification code is 482913",
            "482913 is your Acme security code",
            "Use 0042 to log in to your account",
            "Chase: You made a $42.17 debit card transaction with COFFEE SHOP",
            "Your statement balance of $1,204.55 is due on the 5th",
            "Reply STOP to unsubscribe",
            "Msg&data rates may apply. Text HELP for help.",
            "This is an automated message, do not reply.",
        ]
        for text in automated:
            self.assertTrue(junk.looks_automated(text, strict=True), text)

    def test_ordinary_messages_do_not(self):
        ordinary = [
            "Are we still on for dinner?",
            "Stop by around 6 if you can",                 # "stop" as an ordinary word
            "I owe you $20 for lunch",                      # money, but no banking words
            "The gate code is on the fridge",               # "code" with no digits
            "Call me at 2815550101",
            "Happy 2025!",
        ]
        for text in ordinary:
            self.assertFalse(junk.looks_automated(text, strict=True), text)
            self.assertFalse(junk.looks_automated(text, strict=False), text)

    def test_links_and_delivery_notices_count_only_for_senders_not_in_contacts(self):
        for text in ("Look at this https://example.com/x", "Your order has shipped"):
            self.assertFalse(junk.looks_automated(text, strict=True), text)     # from a contact: not enough
            self.assertTrue(junk.looks_automated(text, strict=False), text)     # from an unknown number: counts


class JunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-junk-test-"))
        build(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        people = self.tmp / "data" / "people.json"
        if people.exists():
            people.unlink()
        junk.refresh()
        stats._cache.clear()
        self.conn = index.connect(readonly=True)

    def tearDown(self):
        self.conn.close()

    def hidden(self):
        return {row[0] for row in self.conn.execute("SELECT DISTINCT counterpart FROM messages WHERE junk = 1")}

    def test_each_kind_of_sender_is_judged_by_the_stated_rules(self):
        found = junk.classify(self.conn)
        tiers = {name: entry["tier"] for name, entry in found.items()}
        self.assertEqual(tiers, {
            BANK: "auto",            # a short code
            CODES: "auto",           # never written to; 2 of 2 are codes
            MARKETING: "auto",       # only reply was STOP; 2 of 2 carry opt-out wording
            SHOP: "auto",            # never written to, not a contact, 1 of 1 is a shipping link
            STRANGER: "suggested",   # never written to, not a contact, but nothing automated-looking
            PHARMACY: "suggested",   # really written to once, but 5 of 5 received look automated
        })
        self.assertNotIn(PERSON, found)      # a real person, even though they once sent a link
        self.assertNotIn(FRIEND, found)      # not in contacts, but you wrote back
        self.assertIn("short code", found[BANK]["reason"])
        self.assertEqual(found[CODES]["reason"], "You never wrote to them, and 2 of their 2 messages look automated.")
        self.assertEqual(found[PHARMACY]["reason"], "You wrote to them once, but 5 of their 5 messages look automated.")
        self.assertIn("Chase", found[BANK]["sample"])

    def test_only_the_automatic_ones_are_hidden_and_nothing_is_deleted(self):
        self.assertEqual(self.hidden(), {BANK, CODES, MARKETING, SHOP})
        total = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(total, sum(len(messages) for messages in HISTORY.values()))      # all 28 are still stored

    def test_hidden_senders_are_left_out_of_every_view_by_default(self):
        people = stats.people(self.conn, Filters())
        self.assertEqual({row["name"] for row in people["rows"]}, {PERSON, STRANGER, FRIEND, PHARMACY})
        # 8 + 1 + 2 + 7 messages; the 10 from the four automated senders are left out.
        self.assertEqual(people["totals"]["messages"], 18)
        self.assertEqual(query.search(self.conn, "code", Filters())["total"], 0)
        self.assertNotIn(BANK, {c["name"] for c in query.contacts(self.conn)})
        ranked = {row["name"] for row in insights.importance(self.conn)["rows"]}
        self.assertFalse(ranked & {BANK, CODES, MARKETING, SHOP})
        self.assertEqual(reading.default_people(self.conn, "2025")[0], PERSON)
        self.assertNotIn(BANK, reading.default_people(self.conn, "2025"))

    def test_a_switch_brings_them_back_and_naming_a_sender_always_shows_it(self):
        everything = stats.people(self.conn, Filters(include_junk=True))
        self.assertEqual(everything["totals"]["messages"], 28)
        self.assertEqual(query.search(self.conn, "code", Filters(include_junk=True))["total"], 1)     # "passcode" is a different word
        self.assertEqual(Filters.from_dict({"include_junk": "1"}).include_junk, True)
        self.assertEqual(Filters.from_dict({}).include_junk, False)
        self.assertEqual(query.search(self.conn, "", Filters(contact=BANK))["total"], 4)      # asked for by name

    def test_your_choices_always_win(self):
        labels.keep(CODES)                       # restore an automatically hidden sender
        labels.hide(FRIEND)                      # hide someone the rules would never touch
        labels.save(MARKETING, "friend", False)  # label one as a person
        labels.save(SHOP, "", True)              # pin one
        junk.refresh()
        self.assertEqual(self.hidden(), {BANK, FRIEND})
        labels.keep(FRIEND)                      # change your mind
        junk.refresh()
        self.assertEqual(self.hidden(), {BANK})
        self.assertEqual(labels.load()[FRIEND], {"label": "", "pinned": False, "keep": True})

    def test_report_lists_hidden_suggested_and_restored_senders(self):
        labels.keep(CODES)
        junk.refresh()
        found = junk.report(self.conn)
        self.assertEqual([row["name"] for row in found["hidden"]], [BANK, MARKETING, SHOP])        # busiest first
        self.assertEqual(found["hidden_messages"], 4 + 3 + 1)
        self.assertEqual([row["name"] for row in found["suggested"]], [PHARMACY, STRANGER])
        self.assertEqual([row["name"] for row in found["kept"]], [CODES])
        bank = found["hidden"][0]
        self.assertEqual((bank["messages"], bank["last_day"], bank["by_you"]), (4, "2025-02-03", False))
        self.assertIn("Your order has shipped", found["hidden"][2]["sample"])

    def test_a_database_from_an_earlier_version_is_brought_up_to_date_on_opening(self):
        # An earlier version had no "junk" column and had never judged any sender.
        self.conn.close()
        path = self.tmp / "data" / "messages.db"
        raw = sqlite3.connect(str(path))
        raw.execute("ALTER TABLE messages DROP COLUMN junk")
        raw.execute("DELETE FROM meta WHERE key IN ('junk_rules', 'junk_report')")
        raw.commit()
        raw.close()
        index._prepared.clear()
        self.conn = index.connect(readonly=True)
        self.assertEqual(self.hidden(), {BANK, CODES, MARKETING, SHOP})
        self.assertTrue(index.is_current())          # and no long re-read of Messages was needed

    def test_group_chats_are_never_hidden(self):
        tmp = Path(tempfile.mkdtemp(prefix="imx-junk-group-test-"))
        saved = {name: os.environ[name] for name in ("IMX_DATA_DIR", "IMX_CHAT_DB", "IMX_ADDRESSBOOK_DIR")}
        try:
            os.environ["IMX_DATA_DIR"] = str(tmp / "data")
            os.environ["IMX_CHAT_DB"] = str(make_fixture.build(tmp / "fixture"))
            os.environ["IMX_ADDRESSBOOK_DIR"] = str(tmp / "fixture" / "AddressBook")
            index.run_import(progress=lambda _line: None)
            conn = index.connect(readonly=True)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages WHERE is_group = 1 AND junk = 1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages WHERE is_group = 1").fetchone()[0], 3)
            conn.close()
        finally:
            os.environ.update(saved)
            shutil.rmtree(str(tmp), ignore_errors=True)


class JunkRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-junk-route-test-"))
        build(cls.tmp)
        cls.httpd, cls.url = server.make_server(0)
        cls.token = server.Handler.token
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def call(self, path, body=None):
        request = urllib.request.Request(self.url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-IMX-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())

    def test_hide_and_restore_from_the_screen_take_effect_at_once(self):
        self.assertEqual(self.call("/api/stats/people")["totals"]["messages"], 18)
        self.assertEqual(self.call("/api/home")["hidden"], {"senders": 4, "messages": 10, "suggested": 2})
        # Restore the bank: its 4 messages come back.
        self.assertEqual(self.call("/api/junk/change", {"action": "keep", "names": [BANK]}), {"changed": 1})
        self.assertEqual(self.call("/api/stats/people")["totals"]["messages"], 22)
        # Hide both suggestions in one go: 1 + 7 messages go.
        self.call("/api/junk/change", {"action": "hide", "names": [STRANGER, PHARMACY]})
        self.assertEqual(self.call("/api/stats/people")["totals"]["messages"], 14)
        found = self.call("/api/junk")
        self.assertEqual({row["name"] for row in found["hidden"]}, {CODES, MARKETING, SHOP, STRANGER, PHARMACY})
        self.assertEqual([row["name"] for row in found["kept"]], [BANK])
        self.assertEqual(found["suggested"], [])
        # The switch on the filter bar shows everything again without changing what is hidden.
        self.assertEqual(self.call("/api/stats/people?include_junk=1")["totals"]["messages"], 28)


if __name__ == "__main__":
    unittest.main()
