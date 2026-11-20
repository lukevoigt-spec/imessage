"""Tests for the extra signals, people labels, and the three research measures.

Expected values are worked out by hand from the fictional data in
tests/make_fixture.py and from the short conversations in this file.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
import test_insights  # noqa: E402
from imx import extract, features, index, insights, labels, science, signals  # noqa: E402
from imx.query import Filters, QueryError  # noqa: E402


class SignalTests(unittest.TestCase):
    """Uses the standard fictional database, which has one of each optional field."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-signal-test-"))
        fixture = cls.tmp / "fixture"
        os.environ["IMX_DATA_DIR"] = str(cls.tmp / "data")
        os.environ["IMX_CHAT_DB"] = str(make_fixture.build(fixture))
        os.environ["IMX_ADDRESSBOOK_DIR"] = str(fixture / "AddressBook")
        cls.report = index.run_import(progress=lambda _line: None)
        cls.conn = index.connect(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_reaction_target_ids(self):
        self.assertEqual(extract.reaction_target("p:0/ABCD-1234"), "ABCD-1234")
        self.assertEqual(extract.reaction_target("bp:ABCD-1234"), "ABCD-1234")
        self.assertEqual(extract.reaction_target("ABCD-1234"), "ABCD-1234")
        self.assertIsNone(extract.reaction_target(None))
        self.assertIsNone(extract.reaction_target(""))

    def test_probe_reports_what_this_database_records(self):
        found = {entry["signal"]: entry for entry in self.report["signals"]}
        self.assertEqual(found["Reactions linked to their message"]["messages"], 1)
        self.assertEqual(found["When you read a message you received"]["messages"], 2)
        self.assertEqual(found["When they read a message you sent"]["messages"], 1)
        self.assertEqual(found["Direct replies to a specific message"]["messages"], 1)
        self.assertEqual(found["Edited messages"]["messages"], 1)
        self.assertEqual(found["Unsent messages"]["messages"], 1)
        self.assertEqual(found["Voice messages"]["messages"], 0)
        self.assertEqual(found["Attachments by kind (photo, video, audio, file)"]["messages"], 2)   # sticker ignored
        self.assertEqual(found["Edited messages"]["first_seen"], "2025-03-03")
        self.assertTrue(all(entry["recorded"] for entry in found.values()))

    def test_probe_on_an_older_database_says_not_recorded(self):
        # The first version of the fictional database had none of the optional fields.
        import sqlite3
        old = self.tmp / "old.db"
        db = sqlite3.connect(str(old))
        db.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, style INTEGER);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, date INTEGER, is_from_me INTEGER, handle_id INTEGER);
            INSERT INTO message VALUES (1, 'g1', 'hello', 700000000000000000, 0, 0);
        """)
        db.commit()
        db.close()
        source = extract.Source(old)
        report = source.probe()
        self.assertFalse(any(entry["recorded"] for entry in report))
        self.assertEqual(len(list(source.messages())), 1)        # and it still imports
        self.assertEqual(source.attachment_kinds(), {})
        source.close()

    def test_reactions_are_matched_to_their_message(self):
        data = signals.reactions(self.conn, Filters())
        self.assertEqual((data["total"], data["matched"], data["unmatched"]), (1, 1, 0))
        self.assertEqual((data["given"], data["received"]), (0, 1))        # Avery loved my message
        self.assertEqual(data["by_type"], [{"kind": "love", "given": 0, "received": 1}])
        avery = data["by_person"][0]
        self.assertEqual((avery["name"], avery["received"], avery["given"]), ("Avery Stone", 1, 0))
        self.assertEqual(avery["received_per_100"], 33.3)                   # 1 reaction across my 3 messages to Avery
        self.assertEqual(data["most_reacted_to"][0]["text"], "Yes! Dinner friday works \U0001F60A")

    def test_read_times(self):
        data = signals.read_times(self.conn, Filters())
        coverage = data["coverage"]
        self.assertEqual((coverage["received"], coverage["received_with_read_time"]), (15, 2))
        self.assertEqual((coverage["sent"], coverage["sent_with_read_time"]), (6, 1))
        self.assertEqual((coverage["received_share"], coverage["sent_share"]), (13.3, 16.7))
        mine = data["me_reading_them"]                                       # 5 minutes and 20 minutes
        self.assertEqual((mine["messages"], mine["median_seconds"], mine["within_5_minutes"], mine["over_a_day"]), (2, 750, 50.0, 0.0))
        self.assertEqual(data["them_reading_me"]["median_seconds"], 60)
        self.assertEqual(data["rows"], [])                                   # nobody has 5 read times

    def test_direct_replies_edits_and_unsends(self):
        totals = signals.replies(self.conn, Filters())["totals"]
        self.assertEqual(totals["direct_replies"], {"me": 0, "them": 1})
        self.assertEqual(totals["edited"], {"me": 1, "them": 0})
        self.assertEqual(totals["unsent"], {"me": 0, "them": 1})

    def test_attachments_by_kind(self):
        data = signals.sharing(self.conn, Filters())
        self.assertEqual(data["totals"]["photos"], {"me": 0, "them": 1})
        self.assertEqual(data["totals"]["videos"], {"me": 0, "them": 1})
        self.assertEqual(data["totals"]["audio"], {"me": 0, "them": 0})
        self.assertEqual(data["by_person"][0]["name"], "Avery Stone")
        self.assertEqual(data["websites"], [])

    def test_people_never_written_to_are_left_out_of_community(self):
        # The unknown number only ever texted me a verification code; I never replied.
        self.assertEqual(insights.community(self.conn)["now"]["people"], 3)


class LinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-link-test-"))
        test_insights.build(cls.tmp, [
            (1, "2025-01-01 09:00", 1, "Read this https://www.example.com/story and https://example.com/other"),
            (1, "2025-01-01 09:01", 0, "Nice. Also www.Recipes.example.org/pie"),
            (1, "2025-01-01 09:02", 1, "and http://news.example.net"),
        ])
        cls.conn = index.connect(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_links_grouped_by_website(self):
        data = signals.sharing(self.conn, Filters())
        self.assertEqual(data["totals"]["links"], {"me": 2, "them": 1})
        sites = {entry["site"]: (entry["me"], entry["them"]) for entry in data["websites"]}
        # Two links to the same site in one message count once; "www." is dropped.
        self.assertEqual(sites, {"example.com": (1, 0), "recipes.example.org": (0, 1), "news.example.net": (1, 0)})


class LabelAndCircleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-label-test-"))
        test_insights.build(cls.tmp, test_insights.CONVERSATIONS)
        cls.conn = index.connect(readonly=True)
        cls.saved = insights.MIN_IMPORTANCE_MESSAGES
        insights.MIN_IMPORTANCE_MESSAGES = 1

    @classmethod
    def tearDownClass(cls):
        insights.MIN_IMPORTANCE_MESSAGES = cls.saved
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def tearDown(self):
        for name in list(labels.load()):
            labels.save(name)                    # no label, no pin: forgets the person

    def ranking(self):
        return [(r["name"], r["pinned"], r["label"]) for r in insights.importance(self.conn)["rows"]]

    def test_unlabelled_ranking_is_by_score(self):
        self.assertEqual(self.ranking(), [("Avery Stone", False, ""), ("Jordan Pike", False, "")])

    def test_a_pinned_person_is_listed_first_whatever_their_score(self):
        labels.save("Jordan Pike", "family", True)
        self.assertEqual(self.ranking(), [("Jordan Pike", True, "family"), ("Avery Stone", False, "")])
        scores = {r["name"]: r["score"] for r in insights.importance(self.conn)["rows"]}
        self.assertEqual(scores, {"Avery Stone": 72, "Jordan Pike": 71})      # the pin moves the order, not the score

    def test_a_service_is_left_out(self):
        labels.save("Avery Stone", "service")
        self.assertEqual([name for name, _pin, _label in self.ranking()], ["Jordan Pike"])
        self.assertEqual(insights.community(self.conn)["now"]["people"], 1)

    def test_labels_are_checked_and_can_be_cleared(self):
        with self.assertRaises(QueryError):
            labels.save("Avery Stone", "enemy")
        with self.assertRaises(QueryError):
            labels.save("", "family")
        labels.save("Avery Stone", "friend")
        self.assertEqual(labels.load()["Avery Stone"], {"label": "friend", "pinned": False})
        labels.save("Avery Stone")
        self.assertEqual(labels.load(), {})

    def test_labels_survive_a_new_import(self):
        labels.save("Jordan Pike", "family", True)
        index.run_import(progress=lambda _line: None)
        self.assertEqual(labels.load()["Jordan Pike"], {"label": "family", "pinned": True})

    def test_circles_fill_from_the_inside_with_pins_first(self):
        labels.save("Jordan Pike", "family", True)
        data = science.circles(self.conn)
        self.assertEqual([ring["name"] for ring in data["rings"]], ["Closest", "Close", "Friends", "Wider circle"])
        self.assertEqual([ring["holds"] for ring in data["rings"]], [5, 10, 35, 100])
        self.assertEqual([p["name"] for p in data["rings"][0]["people"]], ["Jordan Pike", "Avery Stone"])
        self.assertEqual(data["rings"][1]["people"], [])
        self.assertEqual((data["people_ranked"], data["beyond"]), (2, 0))


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-language-test-"))
        test_insights.build(cls.tmp, [
            (1, "2025-01-01 09:00", 1, "We love our cabin"),          # mine: we, our = 2
            (1, "2025-01-01 09:01", 1, "I think we should go"),       # mine: I = 1, we = 1
            (1, "2025-01-01 09:02", 0, "I want my coffee"),           # theirs: I, my = 2
            (1, "2025-01-01 09:03", 0, "us too"),                     # theirs: us = 1
        ])
        cls.conn = index.connect(readonly=True)
        cls.saved = (science.MIN_WORDS_WE_TALK, science.MIN_WORDS_EACH_SIDE)
        science.MIN_WORDS_WE_TALK = science.MIN_WORDS_EACH_SIDE = 1

    @classmethod
    def tearDownClass(cls):
        science.MIN_WORDS_WE_TALK, science.MIN_WORDS_EACH_SIDE = cls.saved
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_we_against_i(self):
        row = science.language(self.conn, Filters())["rows"][0]
        self.assertEqual((row["my_words"], row["their_words"]), (9, 6))
        self.assertEqual(row["my_we_share"], 75.0)            # 3 "we" words of 4 first-person words
        self.assertEqual(row["their_we_share"], 33.3)         # 1 of 3
        overall = science.language(self.conn, Filters())["overall"]
        self.assertEqual((overall["my_we_share"], overall["their_we_share"]), (75.0, 33.3))

    def test_style_matching_arithmetic(self):
        groups = len(science.GROUP_NAMES)
        same = {"words": 100, "groups": [10] + [0] * (groups - 1)}
        self.assertEqual(science._matching(same, dict(same)), 1.0)
        # One of the eight groups differs completely (10% against 0%); the other seven match: 7/8.
        different = {"words": 100, "groups": [0] * groups}
        self.assertEqual(science._matching(same, different), 0.88)
        self.assertEqual(groups, 8)

    def test_style_matching_needs_enough_words(self):
        science.MIN_WORDS_EACH_SIDE = 500
        try:
            from imx import stats
            stats._cache.clear()
            self.assertIsNone(science.language(self.conn, Filters())["rows"][0]["style_matching"])
        finally:
            science.MIN_WORDS_EACH_SIDE = 1


if __name__ == "__main__":
    unittest.main()
