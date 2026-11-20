"""Tests for tone, importance, community, balance, expressions, style and phrases.

Each expected number below was worked out by hand from the small fictional
conversations in this file, and the working is written next to it.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
from imx import features, index, insights, mcp_server  # noqa: E402
from imx.query import Filters, QueryError  # noqa: E402

AVERY, JORDAN = 1, 2    # chat and handle numbers in the fictional database


def build(folder: Path, rows) -> None:
    """Create a fictional Messages database from (chat, when, from_me, text) rows and import it."""
    chat_db = make_fixture.build(folder / "fixture")
    db = sqlite3.connect(str(chat_db))
    db.execute("DELETE FROM message")
    db.execute("DELETE FROM chat_message_join")
    for number, (chat, when, from_me, text) in enumerate(rows, start=1):
        db.execute("INSERT INTO message %s VALUES (?, ?, ?, NULL, ?, ?, ?, 'iMessage', 0, 0, 0)" % make_fixture.MESSAGE_COLUMNS,
                   (number, "guid-%04d" % number, text, make_fixture.apple_ns(when), from_me, chat))
        db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, number))
    db.commit()
    db.close()
    os.environ["IMX_DATA_DIR"] = str(folder / "data")
    os.environ["IMX_CHAT_DB"] = str(chat_db)
    os.environ["IMX_ADDRESSBOOK_DIR"] = str(folder / "fixture" / "AddressBook")
    index.run_import(progress=lambda _line: None)


CONVERSATIONS = [
    # Avery, 1 January: they open, I reply, they ask a question nobody answers.
    (AVERY, "2025-01-01 09:00", 0, "I love you so much"),                 # 5 words, affection
    (AVERY, "2025-01-01 09:01", 1, "Thank you! You are the best"),        # 6 words, gratitude
    (AVERY, "2025-01-01 09:02", 0, "Are we still on for dinner?"),        # 6 words, question, left unanswered by me
    # Avery, 2 January: I open with three in a row, ending on a question they never answer.
    (AVERY, "2025-01-02 10:00", 1, "This is terrible, I hate it"),        # 6 words, negative
    (AVERY, "2025-01-02 10:01", 1, "so sorry"),                           # 2 words, apology
    (AVERY, "2025-01-02 10:02", 1, "can you call me?"),                   # 4 words, question
    # Avery, 3 January: they open.
    (AVERY, "2025-01-03 09:00", 0, "ok"),                                 # 1 word, neutral
    # Jordan: two conversations, both opened by them.
    (JORDAN, "2025-01-03 12:00", 0, "lol that is hilarious"),             # 4 words, laughter
    (JORDAN, "2025-01-03 12:05", 1, "Praying for you"),                   # 3 words, prayer
    (JORDAN, "2025-01-10 08:00", 0, "Great job, proud of you"),           # 5 words, encouragement
]


class ToneScoringTests(unittest.TestCase):
    def test_direction_of_scores(self):
        self.assertGreater(features.tone("I love you so much"), 0.5)
        self.assertLess(features.tone("This is terrible, I hate it"), -0.5)
        self.assertEqual(features.tone("on my way"), 0.0)
        self.assertEqual(features.tone(""), 0.0)

    def test_not_reverses_and_very_strengthens(self):
        self.assertGreater(features.tone("good"), 0)
        self.assertLess(features.tone("not good"), 0)
        self.assertGreater(features.tone("not bad"), 0)
        self.assertGreater(features.tone("very good"), features.tone("good"))
        self.assertLess(features.tone("kinda good"), features.tone("good"))

    def test_plain_acknowledgements_are_neutral(self):
        for word in ("ok", "okay", "yes", "yeah", "sure", "k"):
            self.assertEqual(features.tone(word), 0.0, word)

    def test_emoji_and_exclamation_count(self):
        self.assertGreater(features.tone("\U0001F389"), 0)
        self.assertLess(features.tone("\U0001F621"), 0)
        self.assertGreater(features.tone("great!!!"), features.tone("great"))

    def test_links_are_ignored(self):
        self.assertEqual(features.tone("https://example.com/great-amazing-love"), 0.0)
        self.assertEqual(features.measure("https://example.com/great")[1], 0)        # no words counted

    def test_scores_stay_in_range(self):
        for text in ("love " * 500, "hate " * 500, "!!!!", "a" * 10000):
            self.assertTrue(-1.0 <= features.tone(text) <= 1.0)

    def test_expression_markers(self):
        def marks(text):
            return {name for name, bit, _ in features.EXPRESSIONS if features.measure(text)[2] & bit}
        self.assertEqual(marks("Love you"), {"affection"})
        self.assertEqual(marks("I'd love a coffee"), set())                           # "love" alone is not affection
        self.assertEqual(marks("thanks so much"), {"gratitude"})
        self.assertEqual(marks("sorry, my bad"), {"apology"})
        self.assertEqual(marks("hahaha"), {"laughter"})
        self.assertEqual(marks("what a shame"), set())                                # "ha" inside a word is not laughter
        self.assertEqual(marks("are you coming?"), {"question"})
        self.assertEqual(marks("praying for you"), {"prayer"})
        self.assertEqual(marks("so proud of you"), {"encouragement"})

    def test_style_markers(self):
        flags = features.measure("WHERE ARE YOU!")[2]
        self.assertTrue(flags & features.SHOUT and flags & features.EXCLAIM)
        self.assertTrue(features.measure("on my way")[2] & features.LOWER_START)
        self.assertFalse(features.measure("On my way")[2] & features.LOWER_START)
        self.assertTrue(features.measure("see https://example.com")[2] & features.LINK)


class RelationshipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-insights-test-"))
        build(cls.tmp, CONVERSATIONS)
        cls.conn = index.connect(readonly=True)
        cls.saved = (insights.MIN_TONE_MESSAGES, insights.MIN_IMPORTANCE_MESSAGES)
        insights.MIN_TONE_MESSAGES = insights.MIN_IMPORTANCE_MESSAGES = 1    # tiny test data

    @classmethod
    def tearDownClass(cls):
        insights.MIN_TONE_MESSAGES, insights.MIN_IMPORTANCE_MESSAGES = cls.saved
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_measurements_are_stored_at_import(self):
        row = self.conn.execute("SELECT tone, words, flags FROM messages WHERE text = 'I love you so much'").fetchone()
        self.assertGreater(row["tone"], 0.5)
        self.assertEqual(row["words"], 5)
        self.assertTrue(row["flags"] & features.AFFECTION)
        self.assertTrue(index.is_current())

    def test_tone_by_person(self):
        data = insights.tone_by_person(self.conn, Filters())
        self.assertEqual(data["overall"]["messages"], 10)
        expected_negative = sum(1 for _c, _w, _m, text in CONVERSATIONS if features.tone(text) <= features.NEGATIVE_AT)
        self.assertEqual(expected_negative, 2)                                 # "terrible... hate" and "so sorry"
        self.assertEqual(data["overall"]["negative_share"], 20.0)
        names = [r["name"] for r in data["rows"]]
        self.assertEqual(names, ["Jordan Pike", "Avery Stone"])               # Jordan's messages are all warm
        avery = data["rows"][1]
        self.assertGreater(avery["their_score"], avery["my_score"])            # my side holds the two negative messages

    def test_tone_over_time_and_moments(self):
        days = {b["period"]: b for b in insights.tone_over_time(self.conn, Filters(), "day")["buckets"]}
        self.assertLess(days["2025-01-02"]["score"], 0)
        self.assertGreater(days["2025-01-01"]["score"], 0)
        self.assertEqual(days["2025-01-02"]["negative_share"], 66.7)           # 2 of 3 messages that day
        moments = insights.tone_moments(self.conn, Filters())
        self.assertEqual([m["text"] for m in moments["most_negative"]], ["This is terrible, I hate it"])   # only one negative message has 5+ words
        self.assertTrue(all(m["score"] > 0 for m in moments["most_positive"]))
        self.assertEqual(moments["worst_days"], [])                            # no day has 10 messages

    def test_importance_parts_and_score(self):
        rows = {r["name"]: r for r in insights.importance(self.conn)["rows"]}
        avery, jordan = rows["Avery Stone"], rows["Jordan Pike"]
        # Volume: Avery has the most (7) so 100. Jordan: ln(1+3)/ln(1+7) = 0.667.
        self.assertEqual((avery["parts"]["volume"], jordan["parts"]["volume"]), (100, 67))
        # Consistency: the whole history is one month and both were active in it.
        self.assertEqual((avery["parts"]["consistency"], jordan["parts"]["consistency"]), (100, 100))
        # Recency: Jordan wrote on the last day. Avery last wrote 7 days earlier: 100 x 0.5^(7/90) = 94.8.
        self.assertEqual((avery["parts"]["recency"], jordan["parts"]["recency"]), (95, 100))
        # Two-sided, Avery: messages 4 sent of 7 -> 1 - 1/7 = 0.857; starts 1 me, 2 them -> 1 - 1/3 = 0.667; mean 76.
        # Jordan: messages 1 sent of 3 -> 0.667; starts 0 me, 2 them -> 0; mean 33.
        self.assertEqual((avery["parts"]["two_sided"], jordan["parts"]["two_sided"]), (76, 33))
        # Warmth and depth are rankings between the two people.
        self.assertEqual((avery["parts"]["warmth"], jordan["parts"]["warmth"]), (0, 100))
        # Depth: Avery averages 30/7 = 4.3 words against Jordan's 4.0 (rank 100 v 0); neither has long messages (50 each).
        self.assertEqual((avery["parts"]["depth"], jordan["parts"]["depth"]), (75, 25))
        # Score: volume counts half, the other five parts count fully, divided by 5.5.
        # Avery: (50 + 100 + 94.75 + 76.19 + 0 + 75) / 5.5 = 71.99. Jordan: (33.33 + 100 + 100 + 33.33 + 100 + 25) / 5.5 = 71.21.
        self.assertEqual((avery["score"], jordan["score"]), (72, 71))
        self.assertEqual((avery["rank"], jordan["rank"]), (1, 2))
        self.assertEqual(avery["days_since_last"], 7)

    def test_community(self):
        now = insights.community(self.conn)["now"]
        self.assertEqual((now["people"], now["messages"], now["steady_contacts"]), (2, 10, 0))
        self.assertEqual(now["top1_share"], 70.0)                              # Avery has 7 of 10
        self.assertEqual(now["breadth"], 1.8)                                  # exp(-(0.7 ln 0.7 + 0.3 ln 0.3)) = 1.84
        self.assertEqual(now["active_last_30_days"], 2)

    def test_drift_needs_enough_history(self):
        data = insights.drift(self.conn)
        self.assertEqual((data["growing"], data["fading"], data["gone_quiet"]), ([], [], []))

    def test_balance(self):
        avery = [r for r in insights.balance(self.conn, Filters())["rows"] if r["name"] == "Avery Stone"][0]
        self.assertEqual((avery["my_messages"], avery["their_messages"]), (4, 3))
        self.assertEqual((avery["my_words"], avery["their_words"]), (18, 12))  # 6+6+2+4 and 5+6+1
        self.assertEqual(avery["my_word_share"], 60.0)
        self.assertEqual((avery["my_average_words"], avery["their_average_words"]), (4.5, 4.0))
        self.assertEqual((avery["my_runs"], avery["their_runs"]), (1, 0))      # my three in a row on 2 January
        self.assertEqual((avery["my_questions"], avery["their_questions"]), (1, 1))
        self.assertEqual((avery["unanswered_by_me"], avery["unanswered_by_them"]), (1, 1))

    def test_streaks_and_milestones(self):
        avery = insights.streaks(self.conn, Filters(contact="Avery Stone"))
        self.assertEqual(avery["longest_streak"], {"days": 3, "from": "2025-01-01", "to": "2025-01-03"})
        self.assertEqual(avery["current_streak_days"], 0)                      # history ends 10 January, without Avery
        self.assertEqual(avery["biggest_day"]["messages"], 3)
        self.assertEqual(avery["milestones"], [{"count": 1, "day": "2025-01-01"}])
        self.assertEqual(avery["first_message"]["text"], "I love you so much")
        jordan = insights.streaks(self.conn, Filters(contact="Jordan Pike"))
        self.assertEqual((jordan["longest_streak"]["days"], jordan["current_streak_days"]), (1, 1))

    def test_expressions(self):
        totals = insights.expressions(self.conn, Filters())["totals"]
        self.assertEqual(totals, {
            "affection": {"me": 0, "them": 1}, "gratitude": {"me": 1, "them": 0}, "apology": {"me": 1, "them": 0},
            "laughter": {"me": 0, "them": 1}, "question": {"me": 1, "them": 1}, "prayer": {"me": 1, "them": 0},
            "encouragement": {"me": 0, "them": 1}})
        only_jordan = insights.expressions(self.conn, Filters(contact="Jordan Pike"))["totals"]
        self.assertEqual(only_jordan["affection"], {"me": 0, "them": 0})

    def test_style(self):
        data = insights.style(self.conn, Filters())
        self.assertEqual(data["me"]["messages"], 5)
        self.assertEqual(data["me"]["average_words"], 4.2)                     # (6+6+2+4+3)/5
        self.assertEqual(data["me"]["question_rate"], 20.0)                    # 1 of 5
        self.assertEqual(data["me"]["lowercase_start_rate"], 40.0)             # "so sorry", "can you call me?"
        self.assertEqual(data["me"]["exclamation_rate"], 20.0)                 # "Thank you!"

    def test_profile_gathers_everything(self):
        page = insights.profile(self.conn, "Avery Stone")
        self.assertEqual(page["totals"]["messages"], 7)
        self.assertEqual(page["importance"]["score"], 72)
        self.assertEqual(page["balance"]["my_runs"], 1)
        self.assertEqual(page["streaks"]["longest_streak"]["days"], 3)
        self.assertIn("definition", page["style"])
        with self.assertRaises(QueryError):
            insights.profile(self.conn, "Nobody By This Name")
        with self.assertRaises(QueryError):
            insights.profile(self.conn, "")

    def test_filters_narrow_the_analysis(self):
        january_first = Filters(date_from="2025-01-01", date_to="2025-01-01")
        self.assertEqual(insights.tone_by_person(self.conn, january_first)["overall"]["messages"], 3)
        self.assertEqual(insights.expressions(self.conn, january_first)["totals"]["apology"], {"me": 0, "them": 0})

    def test_ai_tools_can_reach_the_new_analysis(self):
        for kind in ("tone", "tone_over_time", "importance", "community", "drift", "balance", "expressions", "style",
                     "long_phrases", "reactions", "read_times", "direct_replies", "sharing", "language", "circles"):
            result = mcp_server.call_tool("message_statistics", {"kind": kind})
            self.assertIsInstance(result, dict, kind)
        page = mcp_server.call_tool("person_profile", {"contact": "Avery Stone"})
        self.assertEqual(page["importance"]["score"], 72)
        self.assertNotIn("by_month", page)


PHRASES = (
    [(AVERY, "2025-02-01 09:%02d" % minute, 0, "Let me check and get back to you") for minute in range(4)]
    + [(AVERY, "2025-02-02 09:%02d" % minute, 0, "On my way home") for minute in range(3)]
    + [(AVERY, "2025-02-03 09:%02d" % minute, 1, "See you at the game tonight") for minute in range(3)]
    + [(JORDAN, "2025-02-04 09:%02d" % minute, 0, "Roger that captain") for minute in range(5)]
    + [(JORDAN, "2025-02-05 09:%02d" % minute, 1, "on my way home") for minute in range(3)]
)


class PhraseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-phrase-test-"))
        build(cls.tmp, PHRASES)
        cls.conn = index.connect(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_longer_phrases_keep_only_the_longest_form(self):
        found = insights.long_phrases(self.conn, Filters())["by_length"]
        listed = {key: [(item["term"], item["count"]) for item in items] for key, items in found.items()}
        # "on my way" and "my way home" are hidden because "on my way home" is just as common.
        self.assertEqual(listed["3"], [("roger that captain", 5)])
        self.assertEqual(listed["4"], [("on my way home", 6)])
        # Every five-word run here is a fragment of a longer repeated sentence.
        self.assertEqual(listed["5"], [])
        self.assertEqual(listed["6+"], [("let me check and get back to you", 4), ("see you at the game tonight", 3)])

    def test_phrases_never_join_two_messages(self):
        found = insights.long_phrases(self.conn, Filters())["by_length"]
        everything = " | ".join(item["term"] for items in found.values() for item in items)
        self.assertNotIn("to you let me", everything)          # the end of one message plus the start of the next
        self.assertNotIn("captain roger", everything)

    def test_phrases_respect_filters(self):
        mine = insights.long_phrases(self.conn, Filters(direction="sent"))["by_length"]
        self.assertEqual([item["term"] for item in mine["4"]], ["on my way home"])
        self.assertEqual(mine["3"], [])

    def test_signature_finds_what_is_distinctive(self):
        jordan = insights.signature(self.conn, "Jordan Pike")
        self.assertEqual([p["term"] for p in jordan["theirs"]["phrases"]], ["roger that captain"])
        self.assertEqual(sorted(w["term"] for w in jordan["theirs"]["words"]), ["captain", "roger"])
        # I say "on my way home" only to Jordan, so it is distinctive there, and shown in its longest form.
        self.assertEqual([p["term"] for p in jordan["mine"]["phrases"]], ["on my way home"])
        avery = [p["term"] for p in insights.signature(self.conn, "Avery Stone")["theirs"]["phrases"]]
        self.assertIn("get back to you", avery)
        self.assertNotIn("roger that captain", avery)
        # Avery also says "on my way home", but so do I to Jordan; it is still theirs among people writing to me.
        self.assertIn("on my way home", avery)
        with self.assertRaises(QueryError):
            insights.signature(self.conn, "")


class UpgradeTests(unittest.TestCase):
    """A database made by the first version of the tool gains the new measurements on the next import."""

    def test_old_database_is_upgraded_in_place(self):
        tmp = Path(tempfile.mkdtemp(prefix="imx-upgrade-test-"))
        try:
            build(tmp, CONVERSATIONS)
            path = tmp / "data" / "messages.db"
            db = sqlite3.connect(str(path))
            # Rewind to how the first version stored things: no measurement columns, no version marker.
            for column in ("tone", "words", "flags"):
                db.execute("ALTER TABLE messages DROP COLUMN %s" % column)
            db.execute("DELETE FROM meta WHERE key = 'schema_version'")
            db.commit()
            db.close()
            self.assertTrue(index.is_ready())
            self.assertFalse(index.is_current())
            index.run_import(progress=lambda _line: None)
            self.assertTrue(index.is_current())
            conn = index.connect(readonly=True)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 10)   # nothing lost or doubled
            self.assertEqual(conn.execute("SELECT words FROM messages WHERE text = 'so sorry'").fetchone()[0], 2)
            conn.close()
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
