"""Tests for the portrait: what everything read so far adds up to, the trait scores, the brain lines, and the free charts.

No test contacts Anthropic. The stand-in answers each kind of request in the shape it expects.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_histories import Base, Fake  # noqa: E402
from test_reading import GOOD_DIGEST  # noqa: E402
from imx import fullread, histories, jobs, portrait, reading, server  # noqa: E402
from imx.query import QueryError  # noqa: E402

TEN = [{"trait": key, "score": 6, "confidence": "medium", "why": "Because of " + key} for key in portrait.TRAIT_KEYS]


class PortraitFake(Fake):
    @staticmethod
    def answer(params):
        fields = set(params["output_config"]["format"]["schema"]["properties"])
        text = params["messages"][0]["content"]
        if "about_me" in fields:
            return {"about_me": [{"kind": "fact", "text": "I plan things in detail."}, {"kind": "preference", "text": "I like short answers."},
                                 {"kind": "workflow", "text": "I confirm plans the night before."}, {"kind": "fact", "text": "   "}],
                    "people": [{"name": "Avery Stone", "lines": ["Avery is my oldest friend.", "We plan dinners."]},
                               {"name": "Jordan Pike", "lines": ["Jordan and I talk about work."]}]}
        if "in_brief" in fields:
            return {"headline": "A planner who shows care through logistics.", "in_brief": "You organise.",
                    "who_you_are": [{"title": "The organiser", "text": "You make the plans.", "people": ["Avery Stone", "Nobody Real"], "years": ["2025"]}],
                    "roles": [{"role": "friend", "text": "Dependable.", "people": ["Jordan Pike"]}],
                    "values": [{"value": "showing up", "text": "You turn up.", "people": []}],
                    "how_you_communicate": "Short.", "under_pressure": "Shorter.",
                    "strengths": [{"what": "Reliable", "text": "You answer.", "people": ["Avery Stone"]}],
                    "blind_spots": [{"what": "Feelings go unsaid", "text": "Plans instead of feelings.", "people": ["Avery Stone"]}],
                    "contradictions": [], "how_you_have_changed": [{"period": "2024 to 2025", "text": "More messages."}],
                    "what_people_get_from_you": "Plans.", "what_you_ask_of_people": "Replies.",
                    "quotes": [{"text": "message 1", "to": "Avery Stone", "date": "2025-01-01", "why": "Typical."},
                               {"text": "message 29", "to": "Avery Stone", "date": "1999-01-01", "why": "Right words, wrong date."},
                               {"text": "I never wrote this sentence", "to": "Avery Stone", "date": "2025-01-01", "why": "Invented."}],
                    "traits": TEN[:3] + [{"trait": "humour", "score": 14, "confidence": "high", "why": "Too high"},
                                         {"trait": "openness", "score": -2, "confidence": "certain", "why": "Too low"},
                                         {"trait": "charisma", "score": 9, "confidence": "high", "why": "Not one of the ten"}],
                    "what_messages_cannot_show": "Time in person.", "questions_worth_asking": ["What do you leave unsaid?"]}
        if "voice" in fields:
            mine = re.findall(r"\] to [^:]+: (.+)", text)
            return {"voice": "Brief.", "values": [{"value": "plans", "evidence": "many plans"}], "humour": "Dry.", "feelings": "Rarely stated.",
                    "with_different_people": [{"who": "Avery Stone", "how": "Warm."}], "under_pressure": "Terse.",
                    "kindnesses": [], "rough_edges": [],
                    "quotes": [{"text": mine[0], "to": "Avery Stone", "date": re.search(r"\[(\d{4}-\d{2}-\d{2})", text).group(1), "why": "First."},
                               {"text": "Something I never said", "to": "Avery Stone", "date": "2025-01-01", "why": "Invented."}]}
        if "life_events" in fields:
            period = re.search(r"Period: (.+?)\.\n", text).group(1)
            return {"in_a_sentence": "Year note for " + period, "life_events": [{"date": period[:4] + "-06-01", "what": "A move", "who": "Avery Stone"}],
                    "closest_people": [{"name": "Avery Stone", "how": "Daily."}], "how_you_showed_up": "Present.", "themes": ["plans"],
                    "strains": [], "differences": "Warmer with Avery.", "doubts": ""}
        if fields == {"in_a_sentence", "traits"}:
            return {"in_a_sentence": "Steady with " + re.search(r"=== (.+?) ===", text).group(1), "traits": list(reversed(TEN))}
        if "summary" in fields and "message 69" in text:
            return dict(GOOD_DIGEST, closeness="tense", topics=["Dinner", "work"], people=["Sam"], places=["Lake house"],
                        tensions=[{"what": "A late reply", "resolution": "unresolved"}, {"what": "A cancelled plan", "resolution": "resolved"}],
                        promises=[{"who": "Me", "what": "to call"}, {"who": "Avery", "what": "to book"}, {"who": "me", "what": "to pay"}])
        return Fake.answer(params)


class PortraitBase(Base):
    fake_class = PortraitFake

    def write_histories(self):
        histories.set_people(["Avery Stone", "Jordan Pike"])
        histories.start()
        self.finish(histories.runner)
        self.before_portrait = len(self.fake.requests_seen)

    def portrait_requests(self):
        return self.fake.requests_seen[self.before_portrait:]


class PortraitTests(PortraitBase):
    def test_planning_counts_the_work_and_sends_nothing(self):
        self.write_histories()
        planned = portrait.plan(self.conn)
        # Digests exist for 2024 and 2025; you sent messages in both years; two people have histories.
        self.assertEqual((planned["year_notes"], planned["voice_notes"], planned["trait_scores"]),
                         ({"done": 0, "to_do": 2}, {"done": 0, "to_do": 2}, {"done": 0, "to_do": 2}))
        self.assertEqual((planned["histories"], planned["digests"], planned["requests"]), (2, 5, 8))     # 6 now, then the portrait and the brain lines
        self.assertEqual(planned["sent_messages"], 15 + 35 + 10 + 7 + 6)                # the odd-numbered messages of each conversation
        self.assertEqual((planned["cap_usd"], planned["project_cap_usd"]), (80.0, 300.0))
        self.assertEqual(self.portrait_requests(), [])

    def test_round_one_then_round_two(self):
        self.write_histories()
        portrait.start(self.conn)
        self.assertTrue(portrait.runner.step())
        first = self.portrait_requests()
        self.assertEqual(sorted(r["custom_id"][:3] for r in first), ["pt-", "pt-", "pv-", "pv-", "py-", "py-"])
        voice = next(r for r in first if r["custom_id"] == "pv-2025")["params"]["messages"][0]["content"]
        self.assertIn("to Avery Stone: message 1", voice)                               # what you sent
        self.assertNotIn(": message 0", voice)                                          # never what they sent
        self.assertIn('to group "Lake Weekend": message 1', voice)
        year = next(r for r in first if r["custom_id"] == "py-2025")["params"]["messages"][0]["content"]
        self.assertIn("[Avery Stone | 2025-01-01 | 70 messages | feel: tense]", year)
        self.assertIn("[Jordan Pike | 2025-01-10 | 14 messages | feel: warm]", year)
        self.assertIn('[group "Lake Weekend" | 2025-04-01 | 12 messages', year)
        traits = next(r for r in first if r["custom_id"].startswith("pt-"))["params"]["messages"][0]["content"]
        self.assertIn("Measured from the messages with", traits)
        self.assertIn('"conversations_started_by_me_percent"', traits)
        self.finish(portrait.runner)
        second = self.portrait_requests()[6:]
        self.assertEqual(sorted(r["custom_id"] for r in second), ["pb", "pp"])
        material = second[0]["params"]["messages"][0]["content"]
        for expected in ("RELATIONSHIP HISTORIES", "=== Avery Stone ===", "History of Avery Stone", "--- 2024 ---", "Year note for 2025",
                         "NOTES ON THE OWNER'S OWN MESSAGES", "TRAIT SCORES PER PERSON", "MEASURED FROM THE MESSAGES"):
            self.assertIn(expected, material)
        self.assertEqual(second[0]["params"]["output_config"]["effort"], "high")
        now = portrait.status()
        self.assertEqual((now["year_notes"], now["voice_notes"], now["trait_scores"], now["has_portrait"], now["has_brain_lines"], now["ended"]),
                         (2, 2, 2, True, True, "finished"))
        # Eight replies at 1,000 in and 200 out, Opus, half price.
        self.assertAlmostEqual(jobs.spent("portrait"), 8 * 0.004)
        with self.assertRaises(QueryError):
            portrait.start(self.conn)                                                   # written: never paid for twice

    def test_every_quote_is_checked_against_what_you_actually_sent(self):
        self.write_histories()
        portrait.start(self.conn)
        self.finish(portrait.runner)
        found = portrait.read()
        quotes = {q["text"]: q["found"] for q in found["portrait"]["quotes"]}
        self.assertEqual(quotes, {"message 1": True,                    # real, on the day given
                                  "message 29": True,                   # real, though the date given was wrong
                                  "I never wrote this sentence": False})
        voice = {q["text"]: q["found"] for q in found["voices"][0]["note"]["quotes"]}
        self.assertEqual(voice, {"message 1": True, "Something I never said": False})
        # Something the other person said does not count as a quote of yours.
        self.assertFalse(portrait.verify_quotes(self.conn, [{"text": "message 0", "date": "2025-01-01"}])[0]["found"])
        self.assertTrue(portrait.verify_quotes(self.conn, [{"text": "“Message   1”", "date": ""}])[0]["found"])

    def test_trait_scores_are_kept_to_the_ten_traits_and_between_0_and_10(self):
        self.write_histories()
        portrait.start(self.conn)
        self.finish(portrait.runner)
        found = portrait.read()
        overall = {t["trait"]: (t["score"], t["confidence"]) for t in found["portrait"]["traits"]}
        self.assertEqual(overall, {"warmth": (6, "medium"), "reliability": (6, "medium"), "initiative": (6, "medium"),
                                   "humour": (10, "high"),          # 14 brought down to 10
                                   "openness": (0, "low")})         # -2 brought up to 0; an unknown confidence becomes "low"
        self.assertNotIn("charisma", overall)
        self.assertEqual([t["key"] for t in found["trait_names"]], portrait.TRAIT_KEYS)
        self.assertEqual([p["name"] for p in found["per_person"]], ["Avery Stone", "Jordan Pike"])     # in the order of the histories
        self.assertEqual([t["trait"] for t in found["per_person"][0]["traits"]], portrait.TRAIT_KEYS)  # always in the standard order
        self.assertEqual(found["per_person"][0]["in_a_sentence"], "Steady with Avery Stone")

    def test_measured_numbers_come_from_the_messages(self):
        numbers = portrait.measured(self.conn)["Avery Stone"]
        # Avery opened all three conversations; the messages alternate, so each of us wrote half the words.
        self.assertEqual(numbers["conversations_started_by_me_percent"], 0)
        self.assertEqual((numbers["messages"], numbers["words_written_by_me_percent"]), (120, 50))
        self.assertEqual(numbers["my_typical_reply_minutes"], 1.0)

    def test_brain_lines_are_yours_to_edit_and_nothing_is_exported_unticked(self):
        self.write_histories()
        portrait.start(self.conn)
        self.finish(portrait.runner)
        found = portrait.brain_lines()
        self.assertEqual([(s["name"], len(s["lines"])) for s in found["sections"]], [("About me", 3), ("Avery Stone", 2), ("Jordan Pike", 1)])
        self.assertEqual((found["total"], found["confirmed"]), (6, 0))                  # the blank line was dropped; nothing is ticked for you
        self.assertEqual(portrait.brain_export()["lines"], 0)
        first, second, third = (line["id"] for line in found["sections"][0]["lines"])
        avery = found["sections"][1]["lines"][0]["id"]
        portrait.save_brain_line(first, confirmed=True)
        portrait.save_brain_line(second, confirmed=True)
        portrait.save_brain_line(second, text="I like short answers, with the reason.")   # an edit unticks: you confirm what it says now
        portrait.save_brain_line(avery, text="  Avery is my oldest friend,   from school. ", confirmed=True)
        exported = portrait.brain_export()
        self.assertEqual((exported["lines"], exported["total"]), (2, 6))
        self.assertIn("== About me ==\n[fact] I plan things in detail.\n", exported["text"])
        self.assertIn("== Avery Stone ==\n[fact] Avery is my oldest friend, from school.\n", exported["text"])
        self.assertNotIn("short answers", exported["text"])
        self.assertNotIn("Jordan", exported["text"])
        self.assertIn("confirmed by me on", exported["text"])
        again = {line["id"]: line for section in portrait.brain_lines()["sections"] for line in section["lines"]}
        self.assertEqual((again[second]["confirmed"], again[second]["edited"], again[third]["edited"]), (False, True, False))
        with self.assertRaises(QueryError):
            portrait.save_brain_line(first, text="   ")
        with self.assertRaises(QueryError):
            portrait.save_brain_line(999999, confirmed=True)

    def test_the_whole_project_has_a_cap_of_its_own(self):
        self.write_histories()
        saved = portrait.PROJECT_CAP_USD
        portrait.PROJECT_CAP_USD = portrait.project_spent() + 0.0000001               # nothing left for the project
        try:
            self.assertLess(portrait._cap(), 0.001)
            fullread._set("portrait_active", "1")
            self.finish(portrait.runner)
            self.assertEqual((portrait.status()["ended"], portrait.status()["year_notes"]), ("stopped at the cap", 0))
            self.assertEqual(self.portrait_requests(), [])
        finally:
            portrait.PROJECT_CAP_USD = saved

    def test_the_portrait_can_be_thrown_away_and_rewritten_without_redoing_the_notes(self):
        self.write_histories()
        portrait.start(self.conn)
        self.finish(portrait.runner)
        self.assertEqual(len(self.portrait_requests()), 8)
        portrait.forget()
        self.assertIsNone(portrait.read()["portrait"])
        self.assertEqual(portrait.status()["year_notes"], 2)                            # the notes are kept
        portrait.start(self.conn)
        self.finish(portrait.runner)
        self.assertEqual(sorted(r["custom_id"] for r in self.portrait_requests()[8:]), ["pb", "pp"])
        self.assertEqual(portrait.read()["portrait"]["headline"], "A planner who shows care through logistics.")

    def test_without_histories_the_portrait_is_still_drawn_from_the_notes(self):
        self.before_portrait = len(self.fake.requests_seen)
        portrait.start(self.conn)
        self.finish(portrait.runner)
        self.assertEqual(sorted(r["custom_id"][:2] for r in self.portrait_requests()), ["pb", "pp", "pv", "pv", "py", "py"])
        self.assertTrue(portrait.status()["has_portrait"])
        self.assertEqual(portrait.read()["per_person"], [])


class ChartTests(PortraitBase):
    def test_charts_are_counted_straight_from_the_digests(self):
        found = portrait.charts()
        self.assertEqual((found["digests"], found["years"]), (5, ["2024", "2025"]))
        by_year = {row["year"]: row for row in found["feel_by_year"]}
        # 2024: Avery's 30 messages, warm. 2025: the 70-message stretch is tense; 20 + 14 + 12 are warm.
        self.assertEqual((by_year["2024"]["warm"], by_year["2024"]["tense"]), (30, 0))
        self.assertEqual((by_year["2025"]["warm"], by_year["2025"]["tense"]), (46, 70))
        friction = {row["year"]: row for row in found["friction_by_year"]}
        self.assertEqual((friction["2025"]["resolved"], friction["2025"]["unresolved"], friction["2024"]["resolved"]), (1, 1, 0))
        promises = {row["year"]: row for row in found["promises_by_year"]}
        self.assertEqual((promises["2025"]["me"], promises["2025"]["them"]), (2, 1))    # "Me" and "me" are both you
        self.assertEqual(found["topics"][0], {"name": "dinner", "count": 5})            # "Dinner" and "dinner" are one topic
        self.assertEqual(found["people"], [{"name": "Sam", "count": 1}])
        self.assertEqual(found["places"], [{"name": "Lake house", "count": 1}])
        people = {row["name"]: row for row in found["feel_by_person"]}
        self.assertEqual((people["Avery Stone"]["warm"], people["Avery Stone"]["tense"], people["Lake Weekend"]["is_group"]), (50, 70, True))
        self.assertEqual(found["feel_by_person"][0]["name"], "Avery Stone")             # busiest first

    def test_charts_for_one_person(self):
        found = portrait.charts("Jordan Pike")
        self.assertEqual((found["digests"], found["years"]), (1, ["2025"]))
        self.assertEqual(found["feel_by_year"][0]["warm"], 14)
        self.assertEqual(portrait.charts("Lake Weekend", is_group=True)["digests"], 1)
        self.assertEqual(portrait.charts("Lake Weekend", is_group=False)["digests"], 0)


class RouteTests(PortraitBase):
    def setUp(self):
        super().setUp()
        self.httpd, self.url = server.make_server(0)
        self.token = server.Handler.token
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None):
        import urllib.error
        import urllib.request
        request = urllib.request.Request(self.url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-IMX-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def test_the_portrait_over_the_web_interface(self):
        self.write_histories()
        for body in ({}, {"confirm": 1}):
            status, data = self.call("/api/me/start", body)
            self.assertEqual(status, 400)
        self.assertEqual(self.portrait_requests(), [])
        status, planned = self.call("/api/me/plan")
        self.assertEqual((status, planned["requests"]), (200, 8))
        self.assertNotIn("_ready", planned)
        self.assertNotIn("message 3", json.dumps(planned))                              # no message text reaches the page in a plan
        status, data = self.call("/api/me/start", {"confirm": True})
        self.assertEqual((status, data["started"]), (200, True))
        self.finish(portrait.runner)
        status, me = self.call("/api/me")
        self.assertEqual((me["portrait"]["in_brief"], me["status"]["has_portrait"], len(me["per_person"])), ("You organise.", True, 2))
        status, charts = self.call("/api/me/charts?name=Avery%20Stone")
        self.assertEqual(charts["digests"], 3)
        status, lines = self.call("/api/me/brain")
        line = lines["sections"][0]["lines"][0]
        self.assertEqual(self.call("/api/me/brain/save", {"id": line["id"], "confirmed": True}), (200, {"saved": True}))
        status, exported = self.call("/api/me/brain/export")
        self.assertEqual(exported["lines"], 1)
        self.assertIn("[fact] I plan things in detail.", exported["text"])
        status, _ = self.call("/api/me/forget", {})
        status, me = self.call("/api/me")
        self.assertIsNone(me["portrait"])
        from imx import paths
        log = paths.activity_log().read_text(encoding="utf-8")
        self.assertIn('"portrait"', log)
        self.assertNotIn("message 3", log)


if __name__ == "__main__":
    unittest.main()
