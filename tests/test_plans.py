"""Tests for the plans: the partner plan, the monthly check, a month's reading and the reality check of the decade.

No test contacts Anthropic. The stand-in answers each kind of request in the shape it expects.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_portrait import PortraitBase, PortraitFake  # noqa: E402
from imx import fullread, jobs, plans, portrait, reading, server  # noqa: E402
from imx.query import QueryError  # noqa: E402

GOALS = "My goals for the decade:\n- A weekly date night.\n- Bench press 100 kg.\n- Stop work at 5pm."


class PlanFake(PortraitFake):
    @staticmethod
    def answer(params):
        fields = set(params["output_config"]["format"]["schema"]["properties"])
        text = params["messages"][0]["content"]
        numbers = [int(n) for n in re.findall(r"\[#(\d+)\]", text)]
        if "what_they_have_asked_for" in fields:
            return {"in_brief": "You fix when she wants to be heard.",
                    "what_is_working": [{"what": "Quick repair.", "refs": numbers[:1] + [99999, 0]}],
                    "what_they_have_asked_for": [{"ask": "Tell me early about changes.", "how_often": "Several times a year.", "refs": numbers[-1:]}],
                    "patterns": [{"name": "Fixing", "what_happens": "A plan arrives.", "cost": "She feels unheard.", "idea": "Responsiveness",
                                  "strength": "well-established", "refs": numbers[:2]},
                                 {"name": "Made up", "what_happens": "x", "cost": "y", "idea": "Astrology", "strength": "certain", "refs": []}],
                    "first_month": [{"action": "Action %d" % i, "when": "Sunday", "why": "Because.", "how_you_will_know": "Counts.",
                                     "measure": "Thanks and praise"} for i in range(1, 5)],
                    "moments": [{"when": "She shares a problem", "you_tend_to": "Fix it.", "try_instead": "Ask what she needs.",
                                 "words_that_work": "That sounds hard.", "refs": numbers[:1]}],
                    "what_to_stop": [], "to_talk_about": ["What would make this week easier?"],
                    "get_help_if": "The same fight repeats for months.", "what_messages_cannot_show": "Time together.",
                    "brain_lines": [{"kind": "fact", "text": "I tend to fix when Avery wants to be heard."}, {"kind": "workflow", "text": "  "}]}
        if "first_90_days" in fields:
            return {"in_brief": "Work wins most weeks.",
                    "goals": [{"goal": "A weekly date night", "what_messages_show": "About monthly.", "area": "marriage", "evidence": "contradicts", "at_risk": True},
                              {"goal": "Bench press 100 kg", "what_messages_show": "Invisible in texts.", "area": "health", "evidence": "cannot see", "at_risk": "yes"}],
                    "most_at_risk": [{"goal": "A weekly date night", "why": "Travel."}], "tensions": [],
                    "patterns_to_watch": [{"pattern": "Late work", "how_it_shows": "Evening replies.", "early_warning": "Texts after 9pm."}],
                    "first_90_days": [{"move": "Move %d" % i, "when": "Mondays", "why": "Because."} for i in range(1, 6)],
                    "milestones": [{"when": "41", "what": "Run a 10k."}], "what_messages_cannot_show": "Health.",
                    "brain_lines": [{"kind": "fact", "text": "My date-night goal is most at risk."}]}
        if "bids" in fields:
            mine = re.findall(r"\] Me: (.+)", text)
            return {"in_brief": "A quiet month.",
                    "bids": [{"date": "2025-03-01", "what_they_said": "Coffee?", "my_reply": mine[0] if mine else "", "response": "turned toward"},
                             {"date": "2025-03-01", "what_they_said": "Call me", "my_reply": "Words I never sent", "response": "missed"}],
                    "listening": [{"date": "2025-03-01", "what_was_shared": "A hard day", "better": "Ask first.", "what_i_did": "fixed"}],
                    "actions": [{"action": "Action 1", "what_shows_it": "Two thank-yous.", "seen": "partly"}], "one_thing": "Ask before fixing."}
        return PortraitFake.answer(params)


class PlanBase(PortraitBase):
    fake_class = PlanFake

    def setUp(self):
        super().setUp()
        self.saved_plans = plans.runner.ensure_running
        plans.runner.ensure_running = lambda: None

    def tearDown(self):
        plans.runner.ensure_running = self.saved_plans
        super().tearDown()

    def requests_since(self, count):
        return self.fake.requests_seen[count:]

    def write_portrait(self):
        portrait.start(self.conn)
        self.finish(portrait.runner)


class PartnerPlanTests(PlanBase):
    def test_the_partner_defaults_to_the_person_read_with_the_stronger_model(self):
        self.assertEqual(plans.partner(), "")                          # no history yet: nothing to stand on
        self.write_histories()
        self.assertEqual(plans.partner(), "Avery Stone")
        self.assertEqual(plans.set_partner("Jordan Pike"), {"partner": "Jordan Pike"})
        with self.assertRaises(QueryError):
            plans.set_partner("Nobody Real")

    def test_planning_shows_what_would_be_sent_and_sends_nothing(self):
        self.write_histories()
        plans.save_goals(GOALS)
        before = len(self.fake.requests_seen)
        planned = plans.plan(self.conn, "partner")
        self.assertEqual(planned["blockers"], [])
        self.assertEqual((planned["digests"], planned["included"]), (3, 3))
        self.assertEqual((planned["cap_usd"], planned["project_cap_usd"]), (25.0, 300.0))
        self.assertGreater(planned["estimated_cost_usd"], 0)
        material = planned["_item"]["params"]["messages"][0]["content"]
        self.assertIn(GOALS, material)
        self.assertIn("HISTORY OF THE RELATIONSHIP", material)
        self.assertIn("[#1]", material)
        self.assertIn("Starting point (", material)
        self.assertEqual(len(self.fake.requests_seen), before)

    def test_friction_comes_first_when_not_everything_fits(self):
        self.write_histories()
        old = plans.MATERIAL_CHARACTERS
        try:
            plans.MATERIAL_CHARACTERS = 1          # the floor still leaves room for a few digests
            planned = plans.plan(self.conn, "partner")
        finally:
            plans.MATERIAL_CHARACTERS = old
        self.assertLessEqual(planned["included"], planned["digests"])
        self.assertEqual(planned["friction_included"], min(planned["friction"], planned["included"]))

    def test_the_plan_is_written_tidied_and_kept(self):
        self.write_histories()
        plans.save_goals(GOALS)
        with self.assertRaises(QueryError):
            server.POST_ROUTES["/api/plans/start"](self.conn, {"what": "partner"})      # not confirmed: refused
        before = len(self.fake.requests_seen)
        plans.start(self.conn, "partner")
        self.finish(plans.runner)
        sent = self.requests_since(before)
        self.assertEqual([r["custom_id"] for r in sent], ["pa-" + reading_pid("Avery Stone")])
        self.assertEqual(sent[0]["params"]["model"], "claude-opus-5-5")
        found = plans.read("partner")
        g = found["body"]
        self.assertEqual(len(g["first_month"]), 3)                                    # four offered, three kept
        self.assertEqual(g["what_is_working"][0]["refs"], [1])                         # 0 and 99999 are not digests
        self.assertEqual(g["patterns"][1]["idea"], "")                                 # an idea not on the list is dropped
        self.assertEqual(g["patterns"][1]["strength"], "")
        self.assertEqual(len(found["refs"]), 3)
        self.assertTrue(all(found["refs"]))
        self.assertEqual(plans.status()["spent_usd"], round(jobs.spent("plan"), 2))
        self.assertGreater(jobs.spent("plan"), 0)
        with self.assertRaises(QueryError):
            plans.start(self.conn, "partner")                                          # written: not sent twice

    def test_plan_lines_for_brain_are_private_and_kept_apart_from_the_portrait(self):
        self.write_histories()
        self.write_portrait()
        portrait_lines = portrait.brain_lines()["total"]
        plans.start(self.conn, "partner")
        self.finish(plans.runner)
        found = portrait.brain_lines()
        section = next(s for s in found["sections"] if s["name"] == "Plan: you and Avery Stone")
        self.assertTrue(section["private"])
        self.assertEqual([line["text"] for line in section["lines"]], ["I tend to fix when Avery wants to be heard."])
        self.assertEqual(found["total"], portrait_lines + 1)
        for s in found["sections"]:
            for line in s["lines"]:
                portrait.save_brain_line(line["id"], confirmed=True)
        exported = portrait.brain_export()["text"]
        self.assertIn("== Plan: you and Avery Stone [private] ==", exported)
        self.assertIn("Sections marked [private] belong only in a private memory", exported)
        self.assertIn("== About me ==", exported)
        portrait.forget()                                                              # the portrait's lines go, the plan's stay
        self.assertEqual(portrait.brain_lines()["total"], 1)
        plans.forget("partner")
        self.assertEqual(portrait.brain_lines()["total"], 0)
        self.assertIsNone(plans.read("partner")["body"])

    def test_the_cap_on_the_whole_project_holds(self):
        self.write_histories()
        old = portrait.PROJECT_CAP_USD
        try:
            portrait.PROJECT_CAP_USD = portrait.project_spent()          # nothing left anywhere
            with self.assertRaises(QueryError):
                plans.start(self.conn, "partner")
        finally:
            portrait.PROJECT_CAP_USD = old


class DecadeAndMonthTests(PlanBase):
    def test_the_decade_check_needs_goals_and_a_portrait(self):
        self.write_histories()
        blockers = plans.plan(self.conn, "decade")["blockers"]
        self.assertEqual(len(blockers), 2)
        plans.save_goals(GOALS)
        self.write_portrait()
        planned = plans.plan(self.conn, "decade")
        self.assertEqual(planned["blockers"], [])
        material = planned["_item"]["params"]["messages"][0]["content"]
        for part in (GOALS, "PORTRAIT OF THE OWNER", "NOTES ON EACH YEAR", "RELATIONSHIP HISTORIES, CONDENSED", "=== Avery Stone ==="):
            self.assertIn(part, material)
        plans.start(self.conn, "decade")
        self.finish(plans.runner)
        g = plans.read("decade")["body"]
        self.assertEqual(len(g["first_90_days"]), 3)
        self.assertIs(g["goals"][0]["at_risk"], True)
        self.assertIs(g["goals"][1]["at_risk"], False)                               # "yes" is not a yes/no answer
        self.assertEqual(g["goals"][1]["evidence"], "cannot see")

    def test_a_month_is_read_only_after_the_plan_and_only_that_month(self):
        self.write_histories()
        self.assertIn("Write the plan first", " ".join(plans.plan(self.conn, "month", "2025-03")["blockers"]))
        plans.start(self.conn, "partner")
        self.finish(plans.runner)
        self.assertIn("finished month", " ".join(plans.plan(self.conn, "month", datetime.now().strftime("%Y-%m"))["blockers"]))
        self.assertIn("no messages", " ".join(plans.plan(self.conn, "month", "2023-05")["blockers"]))
        self.assertIn("Choose a month", " ".join(plans.plan(self.conn, "month", "2025-13")["blockers"]))
        self.assertEqual(plans.finished_months(self.conn), ["2025-03", "2025-01", "2024-06"])
        before = len(self.fake.requests_seen)
        plans.start(self.conn, "month", "2025-03")
        self.finish(plans.runner)
        sent = self.requests_since(before)
        self.assertEqual([r["custom_id"] for r in sent], ["pm-2025-03"])
        text = sent[0]["params"]["messages"][0]["content"]
        self.assertIn("MESSAGES WITH Avery Stone IN 2025-03 (20)", text)
        self.assertIn("] Me: message 1", text)                                        # both sides, word for word
        self.assertIn("] Avery Stone: message 0", text)
        self.assertNotIn("2025-01-01", text.split("MESSAGES WITH")[1])                 # nothing from another month
        read = plans.months_read()
        self.assertEqual(read[0]["month"], "2025-03")
        self.assertEqual([b["found"] for b in read[0]["body"]["bids"]], [True, False])   # quotes are checked
        plans.forget("month", "2025-03")
        self.assertEqual(plans.months_read(), [])

    def test_goals_are_kept_and_bounded(self):
        self.assertEqual(plans.save_goals("  Run a 10k.  ")["text"], "Run a 10k.")
        with self.assertRaises(QueryError):
            plans.save_goals("x" * (plans.GOALS_CHARACTERS + 1))
        self.assertEqual(plans.goals()["text"], "Run a 10k.")


def reading_pid(name):
    from imx import histories
    return histories._pid(name, False)


class CountTests(unittest.TestCase):
    """The monthly counts, from messages written by hand into an empty index."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, ts INTEGER, from_me INTEGER, kind TEXT, text TEXT, words INTEGER, "
                          "react_type INTEGER, counterpart TEXT, is_group INTEGER, junk INTEGER)")

    def tearDown(self):
        self.conn.close()

    def add(self, when, from_me, text="", kind="text", react_type=0, who="Sam"):
        ts = int(time.mktime(time.strptime(when, "%Y-%m-%d %H:%M")))
        self.conn.execute("INSERT INTO messages (ts, from_me, kind, text, words, react_type, counterpart, is_group, junk) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
                          (ts, from_me, kind, text, len(text.split()), react_type, who))

    def test_each_measure_counts_what_it_says(self):
        self.add("2025-05-01 08:00", 0, "Can you grab milk?")                 # she starts
        self.add("2025-05-01 08:04", 1, "Sure")                                # reply after 4 minutes
        self.add("2025-05-01 08:05", 1, "", kind="reaction", react_type=2000)  # a tapback
        self.add("2025-05-01 20:00", 1, "Thank you for dinner, love you babe")  # 12 hours later: I start; thanks and affection
        self.add("2025-05-01 20:10", 0, "Of course")
        self.add("2025-05-01 20:16", 1, " ".join(["word"] * 45))                # a long message, reply after 6 minutes
        self.add("2025-05-02 09:00", 0, "", kind="reaction", react_type=2001)  # a conversation opened by a tapback has no starter
        self.add("2025-05-02 09:01", 1, "Morning ❤")                      # affection by emoji
        self.add("2025-05-02 09:02", 1, "", kind="reaction", react_type=3000)  # a removed tapback is ignored
        self.add("2025-05-02 09:03", 0, "Hi", who="Someone Else")             # another conversation is ignored
        m = plans.counts(self.conn, "Sam")["2025-05"]
        self.assertEqual((m["started_by_me"], m["started_by_them"]), (1, 1))
        self.assertEqual((m["mine"], m["my_tapbacks"], m["thanks_praise"], m["affection"], m["long"]), (5, 1, 1, 2, 1))
        values = plans._values(m)
        self.assertEqual(values["started_by_me"], 50.0)
        self.assertEqual(values["tapback_share"], 20.0)
        self.assertEqual(values["thanks_praise"], 25.0)                       # per 100 of my four worded messages
        self.assertEqual(values["affection"], 50.0)
        self.assertEqual(values["reply_minutes"], 5.0)                        # the median of 4 and 6

    def test_phrases_that_do_not_count(self):
        self.add("2025-05-01 08:00", 1, "thanksgiving plans? the baby shower is at noon")   # "baby" counts as affection, by design
        self.add("2025-05-01 08:01", 1, "I love your mom's cooking")
        self.add("2025-05-01 08:02", 1, "missed the bus")
        m = plans.counts(self.conn, "Sam")["2025-05"]
        self.assertEqual(m["thanks_praise"], 0)
        self.assertEqual(m["affection"], 1)

    def test_the_starting_point_is_the_twelve_months_before_the_plan(self):
        for month in range(1, 13):
            self.add("2024-%02d-10 09:00" % month, 1, "thank you for everything")
        self.add("2025-01-10 09:00", 1, "ok")
        found = plans.counts(self.conn, "Sam")
        months = plans._months_between("2023-06", "2025-06")
        base, recent = plans.windows(months, "2025-01-15 10:00:00")
        self.assertEqual((base[0], base[-1], len(base)), ("2024-01", "2024-12", 12))
        self.assertEqual(recent, ["2025-04", "2025-05", "2025-06"])                  # the latest finished months since the plan
        self.assertEqual(plans.windows(months, "2025-06-02 10:00:00")[1], ["2025-06"])
        self.assertEqual(plans.windows(months[:-1], "2025-06-02 10:00:00")[1], [])    # nothing has finished since the plan yet
        base, recent = plans.windows(months, "")                                       # no plan: the latest three against the twelve before
        self.assertEqual((recent, base[0], base[-1]), (["2025-04", "2025-05", "2025-06"], "2024-04", "2025-03"))
        pooled = plans._values(plans._pooled([found[m] for m in plans._months_between("2024-01", "2024-12")]))
        self.assertEqual(pooled["thanks_praise"], 100.0)
        self.assertEqual(plans._shift("2025-01", -1), "2024-12")
        self.assertEqual(plans._months_between("2024-11", "2025-02"), ["2024-11", "2024-12", "2025-01", "2025-02"])


class ShapeTests(unittest.TestCase):
    def test_every_layout_is_strict(self):
        def check(schema, path):
            if schema.get("type") == "object":
                self.assertIs(schema.get("additionalProperties"), False, path)
                self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]), path)
                for key, value in schema["properties"].items():
                    check(value, path + "." + key)
            elif schema.get("type") == "array":
                check(schema["items"], path + "[]")
            self.assertFalse({"minimum", "maximum", "minItems", "maxItems"} & set(schema), path)
        for name in ("PARTNER_SCHEMA", "DECADE_SCHEMA", "MONTH_SCHEMA"):
            check(getattr(plans, name), name)

    def test_the_research_names_in_the_prompt_match_the_layout(self):
        for name, _what, strength in plans.RESEARCH:
            self.assertIn(name, plans.PARTNER_SYSTEM)
            self.assertIn(strength, plans.STRENGTHS)
        self.assertEqual(plans.PARTNER_SCHEMA["properties"]["patterns"]["items"]["properties"]["idea"]["enum"], [n for n, _w, _s in plans.RESEARCH])

    def test_nothing_that_takes_the_store_lock_is_called_while_holding_it(self):
        takers = {"spent", "in_flight", "items_out", "attempts", "fits", "submit", "charge", "collect", "cancel", "plan", "start", "stop",
                  "status", "read", "forget", "_digests", "_setting", "_set", "_write", "rates", "goals", "partner", "partner_choices",
                  "_read", "_doc", "measures", "_save_brain_lines", "project_spent", "_notes", "portrait_exists", "brain_lines"}
        for name in ("plans.py", "portrait.py"):
            tree = ast.parse((ROOT / "imx" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.With) and any("_store_lock" in ast.dump(item.context_expr) for item in node.items):
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Call):
                            called = inner.func.attr if isinstance(inner.func, ast.Attribute) else getattr(inner.func, "id", "")
                            self.assertNotIn(called, takers, "%s line %d calls %s() while holding the store lock" % (name, inner.lineno, called))


if __name__ == "__main__":
    unittest.main()
