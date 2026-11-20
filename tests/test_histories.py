"""Tests for the accuracy check and the relationship histories.

No test contacts Anthropic. A stand-in answers each batch request in the shape that
kind of request expects, so everything above it is the real code.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_fullread import AVERY, GROUP, JORDAN, FakeAnthropic, build  # noqa: E402
from test_reading import GOOD_DIGEST, conversation  # noqa: E402
from imx import check, claude_api, fullread, histories, index, jobs, labels, reading, server  # noqa: E402
from imx.query import QueryError  # noqa: E402


class Fake(FakeAnthropic):
    """Answers digests, checks, chapters and histories, each in its own shape."""

    def __init__(self):
        super().__init__()
        self.requests_seen = []

    def __call__(self, method, path, body, key, betas=()):
        if method == "GET" and path.endswith("/results"):
            batch = self.batches[path.split("/")[4]]
            lines = []
            for request in batch["requests"]:
                custom_id, params = request["custom_id"], request["params"]
                if custom_id in self.fail_ids:
                    result = {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error", "message": "too long"}}}
                else:
                    result = {"type": "succeeded", "message": {
                        "model": params["model"], "stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(self.answer(params))}],
                        "usage": {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}}}
                lines.append(json.dumps({"custom_id": custom_id, "result": result}))
            return 200, {}, "\n".join(lines).encode()
        if method == "POST" and path == "/v1/messages/batches":
            self.requests_seen.extend(body["requests"])
        return super().__call__(method, path, body, key, betas)

    @staticmethod
    def answer(params):
        fields = params["output_config"]["format"]["schema"]["properties"]
        text = params["messages"][0]["content"]
        numbers = [int(n) for n in re.findall(r"\[#(\d+)\]", text)]
        if "headline" in fields:
            name = re.search(r"Relationship with (.+?)\.", text).group(1)
            return {"headline": "History of " + name, "how_it_began": {"text": "It began simply.", "refs": numbers[:1]},
                    "eras": [{"title": "Early", "period": "start", "text": "The first stretch.", "refs": numbers[:2] + [99999]}],
                    "themes": [{"theme": "plans", "text": "They plan things.", "refs": numbers[-1:]}],
                    "friction_and_repair": [], "what_i_give": "Time.", "what_i_ask_for": "Patience.", "what_they_give": "Warmth.",
                    "what_they_ask_for": "Answers.", "turning_points": [{"date": "2025-01-01", "what": "A change.", "refs": numbers[:1]}],
                    "strengths": [{"what": "They show up.", "refs": numbers[:1]}], "hard_truths": [], "unresolved": [],
                    "what_messages_cannot_show": "Time spent in person."}
        if "account" in fields:
            period = re.search(r"Period: (.+?)\.\n", text).group(1)
            return {"account": "Account of " + period, "themes": ["plans"], "tone": "steady",
                    "events": [{"date": period[:4] + "-01-01", "what": "Something in " + period, "refs": numbers[:1] + [0, -3, 99999]}],
                    "friction": [], "me": "Shows up.", "them": "Asks.", "turning_points": [], "unresolved": [], "doubts": ""}
        if "unsupported" in fields:
            wrong = "message 69" in text                    # only the 70-message stretch contains this line
            return {"unsupported": [{"claim": "They planned dinner.", "actually": "Dinner is never mentioned."}] if wrong else [],
                    "missed": ["A promise to call."] if wrong else [], "tone": "partly" if wrong else "accurate", "grade": "adequate" if wrong else "good"}
        return GOOD_DIGEST


class Base(unittest.TestCase):
    """A fictional history that has already had its full read:

    Avery (read by Opus): 2024, a 30-message conversation; 2025, a 70-message one and a 20-message one.  -> 3 digests
    Jordan: 2025, 14 messages.                                                                              -> 1 digest
    Group "Lake Weekend": 12 messages.                                                                      -> 1 digest
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-history-test-"))
        build(cls.tmp, conversation(AVERY, "2024-06-01 09:00", 30) + conversation(AVERY, "2025-01-01 09:00", 70)
              + conversation(AVERY, "2025-03-01 09:00", 20) + conversation(JORDAN, "2025-01-10 09:00", 14)
              + conversation(GROUP, "2025-04-01 09:00", 12))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"
        cls.real_curl = claude_api._curl

    @classmethod
    def tearDownClass(cls):
        claude_api._curl = cls.real_curl
        os.environ.pop("ANTHROPIC_API_KEY", None)
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        for name in ("reading.db", "people.json"):
            if (self.tmp / "data" / name).exists():
                (self.tmp / "data" / name).unlink()
        self.fake = Fake()
        claude_api._curl = self.fake
        self.conn = index.connect(readonly=True)
        # Rounds are driven by hand here, so the background threads stay off.
        self.saved = [(job.runner, job.runner.ensure_running) for job in (fullread, check, histories)]
        for runner, _ in self.saved:
            runner.ensure_running = lambda: None
        # The full read comes first, as it does for the user.
        fullread.set_close_people(["Avery Stone"])
        fullread._set("started_epoch", "1")
        fullread._set("active", "1")
        self.finish(fullread.runner)
        self.read_cost = fullread.spent()
        self.sent_by_full_read = len(self.fake.requests_seen)

    def tearDown(self):
        for runner, real in self.saved:
            runner.ensure_running = real
        self.conn.close()

    def finish(self, runner, rounds=20):
        for _ in range(rounds):
            if not runner.step():
                return
        self.fail("did not finish")

    def new_requests(self):
        return self.fake.requests_seen[self.sent_by_full_read:]


class CheckTests(Base):
    def test_the_sample_draws_from_both_models_and_planning_sends_nothing(self):
        planned = check.plan(self.conn, 3)
        self.assertEqual(planned["available"], 5)
        self.assertEqual(planned["by_model"], {"Opus 5.5": 1, "Sonnet 5.5": 2})         # a third from the stronger model
        self.assertEqual((planned["sample"], planned["to_check"], planned["cap_usd"]), (3, 3, 10.0))
        self.assertEqual(self.new_requests(), [])
        everything = check.plan(self.conn, 150)
        self.assertEqual(everything["sample"], 5)                                       # never more than there are

    def test_a_run_checks_each_digest_against_its_messages_and_reports_what_it_found(self):
        check.start(self.conn, 150)
        self.finish(check.runner)
        sent = self.new_requests()
        self.assertEqual(len(sent), 5)
        seventy = next(r for r in sent if "message 69" in r["params"]["messages"][0]["content"])
        text = seventy["params"]["messages"][0]["content"]
        self.assertIn("<transcript>", text)
        self.assertIn("<digest>\n" + json.dumps(GOOD_DIGEST), text)
        self.assertEqual(seventy["params"]["model"], "claude-opus-5-5")
        self.assertRegex(seventy["custom_id"], r"^[a-zA-Z0-9_-]{1,64}$")
        found = check.summary()
        by_model = {row["model"]: row for row in found["models"]}
        opus, sonnet = by_model["claude-opus-5-5"], by_model["claude-sonnet-5-5"]
        # Avery's three digests were written by Opus; one of them (the 70-message stretch) is flagged.
        self.assertEqual((opus["checked"], opus["with_unsupported"], opus["unsupported_claims"], opus["with_missed"], opus["clean"]), (3, 1, 1, 1, 2))
        self.assertEqual((opus["tone"], opus["grades"]["adequate"], opus["grades"]["good"]), ({"accurate": 2, "partly": 1, "wrong": 0}, 1, 2))
        self.assertEqual((sonnet["checked"], sonnet["with_unsupported"], sonnet["clean"]), (2, 0, 2))
        self.assertEqual((found["checked"], found["flagged_total"]), (5, 1))
        flagged = found["flagged"][0]
        self.assertEqual((flagged["name"], flagged["first_day"], flagged["read_by"]), ("Avery Stone", "2025-01-01", "Opus 5.5"))
        self.assertEqual(flagged["unsupported"], [{"claim": "They planned dinner.", "actually": "Dinner is never mentioned."}])
        self.assertEqual(check.findings_for(flagged["unit_id"])["claude-opus-5-5"]["missed"], ["A promise to call."])
        # Five replies at 1,000 in and 200 out, Opus, half price: 5 x (0.004 + 0.004) / 2.
        self.assertAlmostEqual(jobs.spent("accuracy"), 0.02)
        self.assertAlmostEqual(fullread.spent(), self.read_cost)                        # the full read's total is untouched
        self.assertEqual(reading.spent(), 0.0)                                          # and so is the pilot's
        self.assertEqual((check.status()["ended"], check.status()["active"]), ("finished", False))
        with self.assertRaises(QueryError):
            check.start(self.conn, 150)                                                 # nothing left: never checked twice

    def test_the_check_stops_at_its_own_cap(self):
        saved = check.CAP_USD
        check.CAP_USD = 0.0000001
        try:
            fullread._set("check_active", "1")
            self.finish(check.runner)
            self.assertEqual((check.status()["ended"], check.status()["checked"]), ("stopped at the cap", 0))
            self.assertEqual(self.new_requests(), [])
        finally:
            check.CAP_USD = saved


class HistoryTests(Base):
    def test_the_default_list_is_pins_first_then_the_busiest_people_and_never_groups(self):
        self.assertEqual(histories.default_people(), ["Avery Stone", "Jordan Pike"])
        labels.save("Jordan Pike", "", True)
        self.assertEqual(histories.default_people(), ["Jordan Pike", "Avery Stone"])
        self.assertEqual(histories.set_people(["Avery Stone", " ", "Avery Stone", "Nobody"]), ["Avery Stone", "Nobody"])
        planned = histories.plan()
        self.assertEqual(planned["no_digests"], ["Nobody"])
        self.assertEqual(planned["to_write"], 1)

    def test_a_short_relationship_is_written_in_one_step_from_its_digests(self):
        histories.set_people(["Jordan Pike"])
        planned = histories.plan()
        self.assertEqual((planned["requests"], planned["to_write"], planned["cap_usd"]), (1, 1, 40.0))
        self.assertEqual(self.new_requests(), [])                                       # planning sends nothing
        histories.start()
        self.finish(histories.runner)
        sent = self.new_requests()
        self.assertEqual(len(sent), 1)
        text = sent[0]["params"]["messages"][0]["content"]
        self.assertTrue(text.startswith("Relationship with Jordan Pike. Digests 1 to 1, from 2025-01-10 to 2025-01-10."))
        self.assertIn("[#1] 2025-01-10, 14 messages, feel: warm\nSummary: They planned dinner.", text)
        self.assertNotIn("message 3", text)                                             # digests are sent, not the messages
        self.assertEqual((sent[0]["params"]["model"], sent[0]["params"]["output_config"]["effort"]), ("claude-opus-5-5", "high"))
        found = histories.read("Jordan Pike")
        self.assertEqual((found["history"]["headline"], found["digests"], found["first_day"], found["written_by"]),
                         ("History of Jordan Pike", 1, "2025-01-10", "Opus 5.5"))
        self.assertEqual(len(found["refs"]), 1)
        # The reference number opens the stretch it stands for.
        self.assertEqual(histories.digest_by_number(found["refs"][0])["messages"], 14)
        # A number the model made up (99999) is dropped; the real one is kept.
        self.assertEqual(found["history"]["eras"][0]["refs"], [1])

    def test_a_long_relationship_is_written_year_by_year_and_then_as_a_whole(self):
        histories.set_people(["Avery Stone"])
        saved = histories.SINGLE_CHARACTERS
        histories.SINGLE_CHARACTERS = 10                     # force the chapter route
        try:
            planned = histories.plan()
            self.assertEqual((planned["people"][0]["chapters"], planned["requests"]), (2, 3))     # 2024, 2025, then the history
            histories.start()
            self.assertTrue(histories.runner.step())         # sends the two chapters
            first = self.new_requests()
            self.assertEqual(sorted(r["custom_id"][:3] for r in first), ["hc-", "hc-"])
            self.assertEqual(sorted(re.search(r"Period: (\d+)", r["params"]["messages"][0]["content"]).group(1) for r in first), ["2024", "2025"])
            year_2025 = next(r for r in first if "Period: 2025" in r["params"]["messages"][0]["content"])["params"]["messages"][0]["content"]
            self.assertIn("[#2] 2025-01-01, 70 messages", year_2025)
            self.assertIn("[#3] 2025-03-01, 20 messages", year_2025)
            self.assertNotIn("[#1]", year_2025)              # 2024's digest belongs to the other chapter
            self.finish(histories.runner)
        finally:
            histories.SINGLE_CHARACTERS = saved
        sent = self.new_requests()
        self.assertEqual(len(sent), 3)
        whole = sent[2]["params"]["messages"][0]["content"]
        self.assertTrue(sent[2]["custom_id"].startswith("hh-"))
        self.assertIn("=== 2024 ===\nAccount of 2024", whole)
        self.assertIn("=== 2025 ===\nAccount of 2025", whole)
        self.assertIn("Events: 2025-01-01 - Something in 2025 [#2]", whole)             # invented numbers 0, -3 and 99999 were dropped
        self.assertLess(whole.index("=== 2024 ==="), whole.index("=== 2025 ==="))
        found = histories.read("Avery Stone")
        self.assertEqual((found["history"]["headline"], found["digests"], found["first_day"], found["last_day"]),
                         ("History of Avery Stone", 3, "2024-06-01", "2025-03-01"))
        self.assertEqual([histories.digest_by_number(unit)["messages"] for unit in found["refs"]], [30, 70, 20])
        now = histories.status()
        self.assertEqual((now["histories"], now["chapters"], now["ended"], now["being_written"]), (1, 2, "finished", 0))
        # Three replies at 1,000 in and 200 out, Opus, half price.
        self.assertAlmostEqual(jobs.spent("history"), 3 * 0.004)

    def test_a_busy_year_is_split_into_parts(self):
        digests = histories._digests("Avery Stone")
        saved = histories.CHAPTER_CHARACTERS
        histories.CHAPTER_CHARACTERS = len(histories._block(digests[1])) + 5            # room for one digest per chapter
        try:
            self.assertEqual([(label, [d["n"] for d in part]) for label, part in histories._chapters(digests)],
                             [("2024", [1]), ("2025, part 1", [2]), ("2025, part 2", [3])])
        finally:
            histories.CHAPTER_CHARACTERS = saved

    def test_nothing_is_written_twice_and_a_history_can_be_thrown_away_and_rewritten(self):
        histories.set_people(["Jordan Pike"])
        histories.start()
        self.finish(histories.runner)
        with self.assertRaises(QueryError):
            histories.start()
        self.assertEqual(len(self.new_requests()), 1)
        self.assertEqual([h["name"] for h in histories.overview()["histories"]], ["Jordan Pike"])
        histories.forget("Jordan Pike")
        with self.assertRaises(QueryError):
            histories.read("Jordan Pike")
        histories.start()
        self.finish(histories.runner)
        self.assertEqual(len(self.new_requests()), 2)
        self.assertEqual(histories.read("Jordan Pike")["history"]["headline"], "History of Jordan Pike")

    def test_a_request_that_fails_twice_is_given_up_and_the_reason_is_shown(self):
        histories.set_people(["Jordan Pike"])
        self.fake.fail_ids = {"hh-" + histories._pid("Jordan Pike", False)}
        histories.start()
        self.finish(histories.runner)
        self.assertEqual(len(self.new_requests()), 2)
        now = histories.status()
        self.assertEqual((now["histories"], now["ended"]), (0, "finished"))
        self.assertEqual(now["problems"], [{"problem": "history: too long", "times": 2}])
        self.assertEqual(jobs.spent("history"), 0.0)                                    # failures cost nothing

    def test_the_histories_stop_at_their_own_cap(self):
        histories.set_people(["Avery Stone", "Jordan Pike"])
        saved = histories.CAP_USD
        histories.CAP_USD = 0.0000001
        try:
            with self.assertRaises(QueryError):
                histories.start()                            # refuses outright when nothing is left under the cap
            fullread._set("history_active", "1")             # and a run already under way stops when it gets there
            self.finish(histories.runner)
            self.assertEqual((histories.status()["ended"], histories.status()["histories"]), ("stopped at the cap", 0))
            self.assertEqual(self.new_requests(), [])
        finally:
            histories.CAP_USD = saved

    def test_the_full_read_never_mistakes_these_batches_for_its_own(self):
        histories.set_people(["Jordan Pike"])
        histories.start()
        histories.runner.step()                              # a history batch is now out at Anthropic
        self.assertEqual(fullread.adopt_lost_batches(), 0)
        self.assertEqual(len(fullread.status()["batches"]), 1)                          # still only the full read's own


class RouteTests(Base):
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

    def test_nothing_starts_without_the_confirm_button_and_plans_carry_no_text(self):
        for path in ("/api/check/start", "/api/histories/start"):
            for body in ({}, {"confirm": "yes"}):
                status, data = self.call(path, body)
                self.assertEqual(status, 400)
                self.assertIn("Confirm", data["error"])
        self.assertEqual(self.new_requests(), [])
        status, planned = self.call("/api/check/plan")
        self.assertEqual((status, planned["to_check"]), (200, 5))
        status, written = self.call("/api/histories/plan")
        self.assertEqual((status, written["to_write"]), (200, 2))
        for page in (planned, written):
            text = json.dumps(page)
            self.assertNotIn("message 3", text)
            self.assertNotIn("They planned dinner", text)
            self.assertFalse({"_items", "_ready"} & set(page))

    def test_histories_over_the_web_interface(self):
        self.assertEqual(self.call("/api/histories/people", {"names": ["Jordan Pike"]}), (200, {"chosen": ["Jordan Pike"]}))
        status, data = self.call("/api/histories/start", {"confirm": True})
        self.assertEqual((status, data["started"]), (200, True))
        self.finish(histories.runner)
        status, listing = self.call("/api/histories")
        self.assertEqual([h["headline"] for h in listing["histories"]], ["History of Jordan Pike"])
        status, found = self.call("/api/history?name=Jordan%20Pike")
        self.assertEqual(found["history"]["what_messages_cannot_show"], "Time spent in person.")
        status, digest = self.call("/api/digest?id=" + found["refs"][0])
        self.assertEqual((status, digest["messages"], digest["read_by"]), (200, 14, "Sonnet 5.5"))
        status, missing = self.call("/api/history?name=Nobody")
        self.assertEqual(status, 400)
        from imx import paths
        log = paths.activity_log().read_text(encoding="utf-8")
        self.assertIn('"histories"', log)
        self.assertNotIn("They planned dinner", log)


class LockingTests(unittest.TestCase):
    """Code holding the lock on reading.db must not call anything that takes it again."""

    def test_nothing_that_takes_the_store_lock_is_called_while_holding_it(self):
        takers = {"spent", "in_flight", "items_out", "outstanding", "attempts", "fits", "submit", "charge", "collect", "cancel",
                  "_findings", "_candidates", "_sample", "plan", "start", "stop", "status", "summary", "findings_for",
                  "_digests", "_fix_numbering", "_stored", "_handle", "_step", "overview", "read", "forget", "digest_by_number",
                  "default_people", "chosen_people", "set_people", "_setting", "_set", "_write", "rates"}
        for name in ("jobs.py", "check.py", "histories.py"):
            tree = ast.parse((ROOT / "imx" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.With) and any("_store_lock" in ast.dump(item.context_expr) for item in node.items):
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Call):
                            called = inner.func.attr if isinstance(inner.func, ast.Attribute) else getattr(inner.func, "id", "")
                            self.assertNotIn(called, takers, "%s line %d calls %s() while holding the store lock" % (name, inner.lineno, called))


if __name__ == "__main__":
    unittest.main()
