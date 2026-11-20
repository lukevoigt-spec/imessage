"""Tests for importing, searching and statistics, run against fictional data.

Run with:  python3 -m unittest discover -s tests -v
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
from imx import extract, index, query, stats, topics  # noqa: E402
from imx.query import Filters, QueryError  # noqa: E402


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-test-"))
        fixture = cls.tmp / "fixture"
        cls.chat_db = make_fixture.build(fixture)
        os.environ["IMX_DATA_DIR"] = str(cls.tmp / "data")
        os.environ["IMX_CHAT_DB"] = str(cls.chat_db)
        os.environ["IMX_ADDRESSBOOK_DIR"] = str(fixture / "AddressBook")
        cls.report = index.run_import(progress=lambda _line: None)
        cls.conn = index.connect(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    # ---- reading Apple's formats -------------------------------------------------

    def test_dates_in_nanoseconds_and_seconds(self):
        self.assertEqual(extract.apple_time_to_unix(0), None)
        self.assertEqual(extract.apple_time_to_unix(1), 978307201)                       # old format: seconds
        self.assertEqual(extract.apple_time_to_unix(1_000_000_000_000_000_000), 1978307200)  # nanoseconds
        row = self.conn.execute("SELECT day FROM messages WHERE guid = 'guid-old'").fetchone()
        self.assertEqual(row["day"], "2015-07-04")

    def test_attributed_body_short_long_and_emoji(self):
        for text in ("Hi", "Yes! Dinner friday works \U0001F60A", "x" * 127, "y" * 128, "long " * 400):
            self.assertEqual(extract.decode_attributed_body(make_fixture.typedstream(text)), text)
        self.assertIsNone(extract.decode_attributed_body(b"not a typedstream"))
        # The 'mutable' variant macOS also writes: a different class chain before the same string.
        mutable = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x19NSMutableAttributedString\x00\x84\x84\x12"
                   b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x0fNSMutableString\x01\x84\x84\x08"
                   b"NSString\x01\x95\x84\x01+\x0bHello there\x86\x84\x02iI\x01\x0b")
        self.assertEqual(extract.decode_attributed_body(mutable), "Hello there")
        # A length that runs past the end of the data must not return a partial message.
        self.assertIsNone(extract.decode_attributed_body(b"NSString\x01\x94\x84\x01+\x7fshort"))
        self.assertIsNone(extract.decode_attributed_body(None))

    def test_text_only_in_body_is_searchable(self):
        result = query.search(self.conn, "booking", Filters())
        self.assertEqual([r["text"] for r in result["results"]], ["Perfect, booking it now"])

    def test_undecodable_body_is_counted_not_hidden(self):
        self.assertEqual(self.report["undecoded_bodies"], 1)
        self.assertIn("WARNING", index.describe_report(self.report))

    def test_handle_normalization(self):
        self.assertEqual(extract.normalize_handle("+1 (281) 555-0101"), "2815550101")
        self.assertEqual(extract.normalize_handle("2815550101"), "2815550101")
        self.assertEqual(extract.normalize_handle("Jordan.Pike@Example.com"), "jordan.pike@example.com")
        self.assertEqual(extract.normalize_handle("72645"), "72645")

    # ---- what gets imported --------------------------------------------------------

    def test_import_counts(self):
        r = self.report
        self.assertEqual(r["source_messages"], 24)
        self.assertEqual(r["skipped_system_events"], 1)
        self.assertEqual(r["skipped_no_date"], 1)
        self.assertEqual(r["imported"], 22)
        self.assertEqual(r["reactions"], 1)
        self.assertEqual(r["handles_with_contact_name"], 3)
        self.assertEqual(r["handles_total"], 4)

    def test_contact_names_and_unknown_numbers(self):
        names = {c["name"] for c in query.contacts(self.conn)}
        # The unknown number only ever sent a verification code, so it is set aside as automated.
        self.assertEqual(names, {"Avery Stone", "Jordan Pike", "Riley Marsh", "Lake Weekend"})

    def test_attachment_placeholder_removed(self):
        row = self.conn.execute("SELECT text, kind FROM messages WHERE text LIKE '%Cabin photo%'").fetchone()
        self.assertEqual(row["text"], "Cabin photo attached")
        self.assertEqual(row["kind"], "text")

    def test_importing_twice_adds_nothing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        index.run_import(progress=lambda _line: None)
        fresh = index.connect(readonly=True)
        self.assertEqual(fresh.execute("SELECT COUNT(*) FROM messages").fetchone()[0], before)
        fresh.close()

    def test_source_database_is_never_modified(self):
        before = self.chat_db.read_bytes()
        index.run_import(progress=lambda _line: None)
        self.assertEqual(self.chat_db.read_bytes(), before)

    # ---- search ----------------------------------------------------------------------

    def test_two_words_must_both_match(self):
        result = query.search(self.conn, "dinner friday", Filters())
        self.assertEqual(result["total"], 3)   # Avery's question, my reply, my group message

    def test_tapbacks_do_not_appear_in_search(self):
        texts = [r["text"] for r in query.search(self.conn, "dinner", Filters())["results"]]
        self.assertFalse(any(t.startswith("Loved") for t in texts))

    def test_phrase_or_exclude_prefix(self):
        self.assertEqual(query.search(self.conn, '"flight prices"', Filters())["total"], 1)
        self.assertEqual(query.search(self.conn, "kayak OR budget", Filters())["total"], 4)
        self.assertEqual(query.search(self.conn, "flight -denver", Filters())["total"], 2)
        self.assertEqual(query.search(self.conn, "book*", Filters())["total"], 2)   # book, booking

    def test_hostile_search_text_is_harmless(self):
        for text in ('"; DROP TABLE messages; --', "a\" OR \"b", "((((", "*", "NEAR(", "-"):
            try:
                query.search(self.conn, text, Filters())
            except QueryError:
                pass
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        with self.assertRaises(QueryError):
            query.search(self.conn, "-onlyexclude", Filters())

    def test_filters(self):
        self.assertEqual(query.search(self.conn, "flight", Filters(contact="Jordan Pike"))["total"], 1)
        self.assertEqual(query.search(self.conn, "flight", Filters(direction="sent"))["total"], 1)
        self.assertEqual(query.search(self.conn, "dinner", Filters(scope="group"))["total"], 1)
        self.assertEqual(query.search(self.conn, "", Filters(date_from="2025-04-01", date_to="2025-04-30"))["total"], 4)
        with self.assertRaises(QueryError):
            Filters(date_from="April")

    def test_context_shows_surrounding_messages(self):
        hit = query.search(self.conn, "booking", Filters())["results"][0]
        around = query.context(self.conn, hit["id"], before=2, after=1)
        texts = [m["text"] for m in around["messages"]]
        self.assertEqual(texts, ["I can book the italian place", "Yes! Dinner friday works \U0001F60A",
                                 "Perfect, booking it now", "Did you see the flight prices for June?"])

    # ---- statistics ------------------------------------------------------------------

    def test_people_and_volume(self):
        data = stats.people(self.conn, Filters())
        self.assertEqual(data["totals"]["messages"], 20)        # 22 imported minus 1 tapback and 1 automated code
        self.assertEqual(data["totals"]["sent"], 6)
        self.assertEqual(data["totals"]["received"], 14)
        self.assertEqual(data["totals"]["people"], 3)          # Avery, Jordan and Riley; the code sender is not a person
        self.assertEqual(data["totals"]["groups"], 1)
        top = data["rows"][0]
        self.assertEqual((top["name"], top["total"], top["sent"], top["received"]), ("Avery Stone", 10, 3, 7))

    def test_trends(self):
        data = stats.trends(self.conn, Filters(), "month")
        march = [b for b in data["buckets"] if b["period"] == "2025-03"][0]
        self.assertEqual((march["sent"], march["received"]), (3, 6))
        # Monday 9am hour: four on 3 March (tapback excluded) plus one on Monday 2 June.
        self.assertEqual(data["heatmap"][0][9], 5)
        self.assertEqual(data["busiest_days"][0]["day"], "2025-03-03")
        self.assertEqual(sum(sum(row) for row in data["heatmap"]), 20)

    def test_response_patterns(self):
        data = stats.response(self.conn, Filters())
        avery = [r for r in data["rows"] if r["name"] == "Avery Stone"][0]
        # 2015 message, 3 Mar morning (them), 3 Mar evening (me), 4 Mar morning (me)
        self.assertEqual((avery["started_by_them"], avery["started_by_me"]), (2, 2))
        self.assertEqual((avery["my_replies"], avery["their_replies"]), (1, 3))
        self.assertIsNone(avery["my_median_seconds"])           # too few replies to report a speed
        overall = data["overall"]
        self.assertEqual(overall["my_replies"], 2)              # 10 min (Avery), 30 min (Jordan)
        self.assertEqual(overall["my_median_seconds"], 20 * 60)
        self.assertEqual(overall["their_replies"], 5)           # 2, 20, 5 min (Avery); 1 min (Jordan); 20 min (Riley)
        self.assertEqual(overall["their_median_seconds"], 5 * 60)
        self.assertNotIn("Lake Weekend", [r["name"] for r in data["rows"]])   # groups are excluded

    def test_words_phrases_emoji(self):
        data = stats.words(self.conn, Filters())
        words = {w["term"]: w["count"] for w in data["words"]}
        self.assertEqual(words["dinner"], 3)
        self.assertEqual(words["flight"], 3)
        self.assertNotIn("the", words)
        phrases = {p["term"]: p["count"] for p in data["phrases"]}
        self.assertEqual(phrases["dinner friday"], 3)
        emoji = {e["term"]: e["count"] for e in data["emoji"]}
        self.assertEqual(emoji["\U0001F64F"], 2)
        self.assertEqual(emoji["\U0001F60A"], 1)

    def test_keyword_trend(self):
        data = stats.keyword_trend(self.conn, "flight", Filters(), "month")
        self.assertEqual(data["total"], 3)
        self.assertEqual({b["period"]: b["sent"] + b["received"] for b in data["buckets"]},
                         {"2025-03": 2, "2025-04": 1})
        self.assertEqual(data["by_person"][0]["name"], "Avery Stone")

    # ---- topics ----------------------------------------------------------------------

    def test_topics(self):
        topics.save("Outdoors", ["kayak", "mountain cabin", "Lake"])
        overview = {t["name"]: t for t in topics.overview(self.conn, Filters())}
        self.assertEqual(overview["Outdoors"]["query"], 'kayak OR "mountain cabin" OR lake')
        self.assertEqual(overview["Outdoors"]["total"], 4)
        self.assertIn("Travel", overview)                       # starter topics are present
        topics.delete("Outdoors")
        self.assertNotIn("Outdoors", topics.load())


if __name__ == "__main__":
    unittest.main()
