"""Tests for the full read: every conversation digested through Anthropic's batch service.

No test contacts Anthropic. `FakeAnthropic` stands in for the batch service at the
lowest level, so everything above it (requests, waves, collection, the cap) is the real code.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
from test_reading import GOOD_DIGEST, conversation  # noqa: E402
from imx import claude_api, fullread, index, junk, labels, reading, server, stats  # noqa: E402
from imx.claude_api import AIError  # noqa: E402
from imx.query import QueryError  # noqa: E402

AVERY, JORDAN, GROUP = 1, 2, 3          # chats in the fictional database; 3 is the group "Lake Weekend"


def build(folder: Path, rows) -> None:
    """A fictional Messages database from (chat, when, from_me, text) rows, imported."""
    chat_db = make_fixture.build(folder / "fixture")
    db = sqlite3.connect(str(chat_db))
    db.execute("DELETE FROM message")
    db.execute("DELETE FROM chat_message_join")
    handle = {AVERY: 1, JORDAN: 2, GROUP: 1}
    for number, (chat, when, from_me, text) in enumerate(rows, start=1):
        db.execute("INSERT INTO message %s VALUES (?, ?, ?, NULL, ?, ?, ?, 'iMessage', 0, 0, 0)" % make_fixture.MESSAGE_COLUMNS,
                   (number, "guid-%05d" % number, text, make_fixture.apple_ns(when), from_me, 0 if from_me else handle[chat]))
        db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, number))
    db.commit()
    db.close()
    os.environ["IMX_DATA_DIR"] = str(folder / "data")
    os.environ["IMX_CHAT_DB"] = str(chat_db)
    os.environ["IMX_ADDRESSBOOK_DIR"] = str(folder / "fixture" / "AddressBook")
    index.run_import(progress=lambda _line: None)


class FakeAnthropic:
    """Stands in for Anthropic's batch service. Use as `claude_api._curl`."""

    def __init__(self, polls_until_done=0, tokens=(1000, 200)):
        self.polls_until_done = polls_until_done
        self.tokens = tokens
        self.batches = {}                 # id -> {"requests": [...], "polls": n, "cancelled": bool}
        self.refuse_create = None         # set to (status, headers, message) to make the next create fail
        self.lose_reply = False           # take the next batch, but let the app think the upload failed
        self.created_at = "2099-01-01T00:00:00.123456789Z"
        self.fail_ids = set()             # custom ids that come back as errored
        self.calls = []

    def __call__(self, method, path, body, key, betas=()):
        self.calls.append((method, path))
        reply = lambda status, payload, headers=None: (status, headers or {}, json.dumps(payload).encode())   # noqa: E731
        if method == "POST" and path == "/v1/messages/batches":
            if self.refuse_create:
                status, headers, message = self.refuse_create
                self.refuse_create = None
                return reply(status, {"type": "error", "error": {"type": "rate_limit_error", "message": message}}, headers)
            batch_id = "msgbatch_%04d" % (len(self.batches) + 1)
            self.batches[batch_id] = {"requests": body["requests"], "polls": 0, "cancelled": False}
            if self.lose_reply:
                self.lose_reply = False
                raise AIError("Could not reach Anthropic: connection reset", retryable=True)
            return reply(200, {"id": batch_id, "processing_status": "in_progress"})
        if method == "GET" and path.startswith("/v1/messages/batches?"):
            return reply(200, {"has_more": False, "data": [
                {"id": batch_id, "created_at": self.created_at, "processing_status": "in_progress",
                 "request_counts": {"processing": len(batch["requests"]), "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}}
                for batch_id, batch in self.batches.items()]})
        if method == "POST" and path.endswith("/cancel"):
            self.batches[path.split("/")[-2]]["cancelled"] = True
            return reply(200, {"processing_status": "canceling"})
        batch_id = path.split("/")[4]
        batch = self.batches[batch_id]
        if method == "GET" and path.endswith("/results"):
            lines = []
            for request in batch["requests"]:
                custom_id, params = request["custom_id"], request["params"]
                if batch["cancelled"]:
                    result = {"type": "canceled"}
                elif custom_id in self.fail_ids:
                    result = {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error", "message": "too long"}}}
                else:
                    result = {"type": "succeeded", "message": {
                        "model": params["model"], "stop_reason": "end_turn",
                        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(GOOD_DIGEST)}],
                        "usage": {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}}}
                lines.append(json.dumps({"custom_id": custom_id, "result": result}))
            return 200, {}, "\n".join(reversed(lines)).encode()         # out of order, as the real service may be
        batch["polls"] += 1
        done = batch["cancelled"] or batch["polls"] > self.polls_until_done
        return reply(200, {"id": batch_id, "processing_status": "ended" if done else "in_progress"})

    def sent_ids(self):
        return [request["custom_id"] for batch in self.batches.values() for request in batch["requests"]]


class FullReadTests(unittest.TestCase):
    """One fictional history:

    Avery, 2025: a 70-message conversation and a 20-message one.     -> 2 units
    Jordan, 2025: 8 messages in January and 6 in February (quiet year: read together). -> 1 unit
    Jordan, 2024: "thanks" / "np" (too short to digest).             -> left out
    Group "Lake Weekend", 2025: 12 messages.                         -> 1 unit
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-full-test-"))
        build(cls.tmp, conversation(AVERY, "2025-01-01 09:00", 70) + conversation(AVERY, "2025-03-01 09:00", 20)
              + conversation(JORDAN, "2025-01-10 09:00", 8) + conversation(JORDAN, "2025-02-10 09:00", 6)
              + [(JORDAN, "2024-05-01 10:00", 0, "thanks"), (JORDAN, "2024-05-01 10:01", 1, "np")]
              + conversation(GROUP, "2025-04-01 09:00", 12))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"
        cls.real_curl = claude_api._curl

    @classmethod
    def tearDownClass(cls):
        claude_api._curl = cls.real_curl
        os.environ.pop("ANTHROPIC_API_KEY", None)
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        store = self.tmp / "data" / "reading.db"
        if store.exists():
            store.unlink()
        self.fake = FakeAnthropic()
        claude_api._curl = self.fake
        self.conn = index.connect(readonly=True)
        fullread.runner.problem = ""
        # These tests drive the runner by hand, one round at a time, so its background thread stays off.
        self.real_ensure = fullread.runner.ensure_running
        fullread.runner.ensure_running = lambda: None

    def tearDown(self):
        fullread.runner.ensure_running = self.real_ensure
        self.conn.close()

    def run_to_end(self, rounds=20):
        """Drive the runner by hand, one round at a time, the way its background thread would."""
        for _ in range(rounds):
            if not fullread.runner.step():
                return
        self.fail("the full read did not finish")

    def begin(self):
        fullread._set("started_epoch", "1")              # so batches the stand-in holds count as made during this read
        fullread._set("active", "1")

    def test_the_whole_history_is_cut_into_units_and_short_exchanges_are_left_out(self):
        units, skipped = fullread.all_units(self.conn)
        found = sorted((u["counterpart"], u["is_group"], u["messages"]) for u in units)
        self.assertEqual(found, [("Avery Stone", False, 20), ("Avery Stone", False, 70), ("Jordan Pike", False, 14), ("Lake Weekend", True, 12)])
        self.assertEqual(skipped, {"units": 1, "messages": 2})
        group = next(u for u in units if u["is_group"])
        self.assertTrue(reading._request_text(group).startswith('Group chat "Lake Weekend":'))
        # The dense conversation is cut exactly as the pilot cut it, so pilot digests are reused.
        pilot_ids = {u["id"] for u in reading.units_for(self.conn, "Avery Stone", "2025")}
        self.assertEqual({u["id"] for u in units if u["counterpart"] == "Avery Stone"}, pilot_ids)

    def test_the_people_you_name_go_to_the_stronger_model(self):
        fullread.set_close_people(["Avery Stone", "  ", "Nobody Here"])
        planned = fullread.plan(self.conn)
        by_model = {m["model"]: m for m in planned["models"]}
        self.assertEqual((by_model["claude-opus-5-5"]["units"], by_model["claude-opus-5-5"]["messages"]), (2, 90))
        self.assertEqual((by_model["claude-sonnet-5-5"]["units"], by_model["claude-sonnet-5-5"]["messages"]), (2, 26))
        self.assertEqual(planned["close_people_found"], ["Avery Stone"])       # the name that matched nobody is reported, not hidden
        self.assertEqual(planned["close_people"], ["Avery Stone", "Nobody Here"])
        self.assertEqual((planned["conversations"], planned["people"], planned["groups"], planned["units"], planned["messages"]), (3, 2, 1, 4, 116))
        # Closest person first, then the busier of the rest.
        self.assertEqual([u["counterpart"] for u in planned["_pending"]], ["Avery Stone", "Avery Stone", "Jordan Pike", "Lake Weekend"])
        self.assertEqual(self.fake.calls, [])                                   # planning sends nothing

    def test_a_full_run_reads_everything_once_at_half_price(self):
        fullread.set_close_people(["Avery Stone"])
        self.begin()
        self.run_to_end()
        self.assertEqual(len(self.fake.sent_ids()), 4)
        self.assertEqual(len(set(self.fake.sent_ids())), 4)
        request = self.fake.batches["msgbatch_0001"]["requests"][0]
        self.assertEqual(request["params"]["model"], "claude-opus-5-5")
        self.assertEqual(request["params"]["output_config"], {"effort": "medium", "format": {"type": "json_schema", "schema": reading.DIGEST_SCHEMA}})
        self.assertNotIn("fallbacks", request["params"])                        # not allowed inside a batch
        self.assertRegex(request["custom_id"], r"^[a-zA-Z0-9_-]{1,64}$")
        now = fullread.status()
        self.assertEqual((now["read"], now["messages_read"], now["being_read"], now["not_read"], now["active"], now["ended"]),
                         (4, 116, 0, 0, False, "finished"))
        # Each reply is 1,000 tokens in and 200 out, at half price:
        # Opus 2 x (0.004 + 0.004) / 2 = 0.008; Sonnet 2 x (0.002 + 0.002) / 2 = 0.004.
        self.assertAlmostEqual(fullread.spent(), 0.012)
        self.assertEqual(reading.spent(), 0.0)                                  # the pilot's total is separate
        # Starting again finds nothing to do, and sends nothing.
        with self.assertRaises(QueryError):
            fullread.start(self.conn)
        self.assertEqual(len(self.fake.batches), 1)

    def test_digests_the_pilot_paid_for_are_not_bought_again(self):
        fullread.set_close_people(["Avery Stone"])
        pilot_unit = reading.units_for(self.conn, "Avery Stone", "2025")[0]
        reading.read_unit(pilot_unit, "claude-opus-5-5", send=lambda model, s, t, **k: {
            "text": json.dumps(GOOD_DIGEST), "stop_reason": "end_turn", "model": model,
            "usage": {"input_tokens": 1000, "output_tokens": 200}, "cost_usd": 0.008})
        planned = fullread.plan(self.conn)
        opus = next(m for m in planned["models"] if m["model"] == "claude-opus-5-5")
        self.assertEqual((opus["already_read"], opus["to_send"]), (1, 1))
        self.begin()
        self.run_to_end()
        self.assertNotIn(pilot_unit["id"], self.fake.sent_ids())
        self.assertEqual(len(self.fake.sent_ids()), 3)

    def test_waves_keep_spending_under_the_cap_even_at_twice_the_estimate(self):
        estimates = [u["estimate_usd"] for u in fullread.plan(self.conn)["_pending"]]
        saved = fullread.FULL_CAP_USD
        # Room for the first two conversations at twice their estimate, and no more.
        fullread.FULL_CAP_USD = 2 * (estimates[0] + estimates[1]) + 0.000001
        try:
            first = fullread.send_wave(self.conn)
            self.assertEqual((first["sent"], first["still_waiting"]), (2, 2))
            self.assertEqual(fullread.send_wave(self.conn)["sent"], 0)          # nothing more fits while those are out
            fullread.collect()
            # They came back costing 0.002 each (1,000 in, 200 out, Sonnet, half price), so there is room again.
            self.assertAlmostEqual(fullread.spent(), 0.004)
            self.assertGreaterEqual(fullread.send_wave(self.conn)["sent"], 1)
            self.assertLessEqual(fullread.spent() + fullread.SAFETY * fullread._outstanding(), fullread.FULL_CAP_USD)
        finally:
            fullread.FULL_CAP_USD = saved

    def test_the_run_ends_at_the_cap_and_says_so(self):
        saved = fullread.FULL_CAP_USD
        fullread.FULL_CAP_USD = 0.0000001
        try:
            self.begin()
            self.run_to_end()
            now = fullread.status()
            self.assertEqual((now["read"], now["ended"], now["active"]), (0, "stopped at the cap", False))
            self.assertEqual(self.fake.batches, {})
        finally:
            fullread.FULL_CAP_USD = saved

    def test_results_are_collected_only_when_a_batch_has_finished(self):
        self.fake.polls_until_done = 2
        self.begin()
        self.assertTrue(fullread.runner.step())          # sends
        self.assertTrue(fullread.runner.step())          # still processing
        self.assertEqual(fullread.status()["being_read"], 4)
        self.assertEqual(fullread.status()["read"], 0)
        self.run_to_end()
        self.assertEqual(fullread.status()["read"], 4)

    def test_a_conversation_that_fails_is_tried_twice_then_listed_as_not_read(self):
        units, _ = fullread.all_units(self.conn)
        bad = units[0]["id"]
        self.fake.fail_ids = {bad}
        self.begin()
        self.run_to_end()
        self.assertEqual(self.fake.sent_ids().count(bad), 2)
        now = fullread.status()
        self.assertEqual((now["read"], now["not_read"], now["ended"]), (3, 1, "finished"))
        self.assertEqual(now["problems"], [{"problem": "full read: too long", "times": 2}])
        self.assertAlmostEqual(fullread.spent(), 3 * 0.002)                     # failures cost nothing

    def test_stopping_cancels_what_is_out_and_keeps_what_was_read(self):
        self.fake.polls_until_done = 99
        self.begin()
        fullread.runner.step()
        self.assertEqual(fullread.status()["being_read"], 4)
        fullread.stop(cancel=claude_api.batch_cancel)
        self.assertTrue(self.fake.batches["msgbatch_0001"]["cancelled"])
        for _ in range(5):
            if not fullread.runner.step():
                break
        now = fullread.status()
        self.assertEqual((now["being_read"], now["read"], now["active"], now["ended"]), (0, 0, False, "stopped by you"))
        self.assertEqual(len(self.fake.batches), 1)                             # nothing new was sent after the stop
        # Cancelled conversations were never read, so they do not count as tries: a later start sends them again.
        self.fake.polls_until_done = 0
        fullread.start(self.conn)
        self.run_to_end()
        self.assertEqual(fullread.status()["read"], 4)

    def test_a_refusal_that_waiting_can_fix_is_retried_and_one_that_it_cannot_stops_the_run(self):
        self.fake.refuse_create = (429, {"retry-after": "30"}, "slow down")
        self.begin()
        with self.assertRaises(AIError) as caught:
            fullread.runner.step()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(fullread.status()["being_read"], 0)                    # nothing was recorded as sent
        self.run_to_end()                                                       # the next round goes through
        self.assertEqual(fullread.status()["read"], 4)

    def test_a_batch_anthropic_took_but_the_app_never_heard_about_is_taken_back_not_sent_again(self):
        fullread.set_close_people(["Avery Stone"])
        fullread.start(self.conn)
        self.fake.lose_reply = True
        with self.assertRaises(AIError):
            fullread.runner.step()                       # the upload "failed", but Anthropic has the batch
        self.assertEqual(fullread.status()["being_read"], 0)
        self.assertEqual(len(self.fake.batches), 1)
        self.run_to_end()
        # The same four conversations were never sent a second time, and their results were collected and paid for once.
        self.assertEqual(len(self.fake.batches), 1)
        self.assertEqual(len(self.fake.sent_ids()), 4)
        now = fullread.status()
        self.assertEqual((now["read"], now["messages_read"], now["ended"]), (4, 116, "finished"))
        self.assertAlmostEqual(fullread.spent(), 0.012)                         # Opus for Avery's two, Sonnet for the rest
        by_model = {row[0]: row[1] for row in fullread._store().execute("SELECT model, COUNT(*) FROM digest GROUP BY model")}
        self.assertEqual(by_model, {"claude-opus-5-5": 2, "claude-sonnet-5-5": 2})

    def test_batches_from_before_this_read_began_are_left_alone(self):
        self.fake.created_at = "2020-01-01T00:00:00Z"
        self.fake.batches["msgbatch_old"] = {"requests": [{"custom_id": "someone-elses", "params": {"model": "claude-sonnet-5-5"}}],
                                             "polls": 0, "cancelled": False}
        fullread.start(self.conn)
        self.assertEqual(fullread.adopt_lost_batches(), 0)
        self.assertEqual(fullread.status()["batches"], [])

    def test_timestamps_from_anthropic_are_understood(self):
        self.assertEqual(fullread._epoch("1970-01-01T00:00:10Z"), 10.0)
        self.assertEqual(fullread._epoch("1970-01-01T00:00:10.500000123Z"), 10.5)
        self.assertEqual(fullread._epoch("1970-01-01T01:00:10+01:00"), 10.0)
        self.assertEqual(fullread._epoch("not a time"), 0.0)

    def test_uploads_are_split_into_small_batches_and_the_screen_says_what_is_happening(self):
        saved = fullread.BATCH_REQUESTS
        fullread.BATCH_REQUESTS = 3
        said = []
        try:
            outcome = fullread.send_wave(self.conn, say=said.append)
        finally:
            fullread.BATCH_REQUESTS = saved
        self.assertEqual((outcome["sent"], outcome["batches"]), (4, 2))
        self.assertEqual([len(batch["requests"]) for batch in self.fake.batches.values()], [3, 1])
        self.assertEqual(said, ["Working out what to send", "Uploading: 0 of 4 stretches in this wave sent so far",
                                "Uploading: 3 of 4 stretches in this wave sent so far"])

    def test_estimates_rest_on_what_the_pilot_measured(self):
        before = fullread.plan(self.conn)
        self.assertFalse(before["estimate_from_your_messages"])
        # Twelve paid replies in which every character cost exactly half a token, plus 300 fixed, and 500 out.
        with reading._store_lock:
            store = fullread._store()
            for size in range(1000, 13000, 1000):
                store.execute("INSERT INTO ledger (at, model, unit_id, messages, input_tokens, output_tokens, cost_usd, characters, purpose) "
                              "VALUES ('t', 'claude-sonnet-5-5', 'u', 10, ?, 500, 0, ?, 'digest')", (size // 2 + 300, size))
            store.commit()
            store.close()
        per_character, fixed, output = fullread.rates()["claude-sonnet-5-5"]
        self.assertAlmostEqual(per_character, 0.5)
        self.assertAlmostEqual(fixed, 300.0)
        self.assertAlmostEqual(output, 500.0)
        unit = {"characters": 4000}
        # (4,000 x 0.5 + 300) x $2 + 500 x $10, per million, at half price.
        self.assertAlmostEqual(fullread.estimate(unit, "claude-sonnet-5-5", fullread.rates()), (2300 * 2 + 500 * 10) / 1e6 / 2)

    def test_digests_read_back_in_date_order(self):
        fullread.set_close_people(["Avery Stone"])
        self.begin()
        self.run_to_end()
        people = fullread.digest_people()
        self.assertEqual([(p["name"], p["is_group"], p["digests"], p["messages"]) for p in people],
                         [("Avery Stone", False, 2, 90), ("Jordan Pike", False, 1, 14), ("Lake Weekend", True, 1, 12)])
        found = fullread.digests_for("Avery Stone")
        self.assertEqual([(d["first_day"], d["messages"], d["read_by"]) for d in found["digests"]],
                         [("2025-01-01", 70, "Opus 5.5"), ("2025-03-01", 20, "Opus 5.5")])
        self.assertEqual(found["digests"][0]["digest"]["summary"], GOOD_DIGEST["summary"])
        self.assertEqual(fullread.digests_for("Avery Stone", newest_first=True)["digests"][0]["first_day"], "2025-03-01")
        self.assertEqual(fullread.digests_for("Lake Weekend", is_group=True)["total"], 1)
        self.assertEqual(fullread.digests_for("Lake Weekend", is_group=False)["total"], 0)

    def test_automated_senders_are_never_sent(self):
        labels.hide("Jordan Pike")
        junk.refresh()
        stats._cache.clear()
        try:
            self.conn.close()
            self.conn = index.connect(readonly=True)
            units, _ = fullread.all_units(self.conn)
            self.assertNotIn("Jordan Pike", {u["counterpart"] for u in units})
        finally:
            (self.tmp / "data" / "people.json").unlink()
            junk.refresh()

    def test_the_background_runner_finishes_on_its_own(self):
        runner = fullread.Runner(poll_seconds=0.01)
        self.fake.polls_until_done = 2
        self.begin()
        runner.ensure_running()
        for _ in range(500):
            if not runner.thread.is_alive():
                break
            time.sleep(0.01)
        self.assertFalse(runner.thread.is_alive())
        self.assertEqual((fullread.status()["read"], fullread.status()["ended"]), (4, "finished"))


class LockingTests(unittest.TestCase):
    """reading.db is guarded by one lock. Code holding it must not call anything that takes it again:
    that is how the app would freeze. This reads the source and checks."""

    def test_nothing_that_takes_the_store_lock_is_called_while_holding_it(self):
        import ast
        takers = {"_setting", "_set", "spent", "close_people", "set_close_people", "rates", "_measured", "_state", "_outstanding",
                  "status", "_write", "_done_pairs", "_in_flight", "_began", "plan", "collect", "send_wave", "adopt_lost_batches",
                  "digest_people", "digests_for", "results", "pilot_report", "review_queue", "unit_for_review", "save_review", "read_unit"}
        for name in ("fullread.py", "reading.py"):
            tree = ast.parse((ROOT / "imx" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.With):
                    continue
                if not any("_store_lock" in ast.dump(item.context_expr) for item in node.items):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        called = inner.func.attr if isinstance(inner.func, ast.Attribute) else getattr(inner.func, "id", "")
                        self.assertNotIn(called, takers, "%s line %d calls %s() while holding the store lock" % (name, inner.lineno, called))

    def test_a_read_started_by_the_previous_version_still_knows_when_it_began(self):
        tmp = Path(tempfile.mkdtemp(prefix="imx-began-test-"))
        saved = os.environ.get("IMX_DATA_DIR")
        os.environ["IMX_DATA_DIR"] = str(tmp)
        try:
            self.assertEqual(fullread._began(), 0.0)
            fullread._set("started_at", "2026-11-20 03:15:00")           # all the previous version recorded
            self.assertEqual(fullread._began(), time.mktime(time.strptime("2026-11-20 03:15:00", "%Y-%m-%d %H:%M:%S")))
            fullread._set("started_epoch", "12345.5")
            self.assertEqual(fullread._began(), 12345.5)
        finally:
            if saved is None:
                os.environ.pop("IMX_DATA_DIR", None)
            else:
                os.environ["IMX_DATA_DIR"] = saved
            shutil.rmtree(str(tmp), ignore_errors=True)


class FullReadRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-full-route-test-"))
        build(cls.tmp, conversation(AVERY, "2025-01-01 09:00", 12) + conversation(JORDAN, "2025-01-02 09:00", 9))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"
        cls.real_curl = claude_api._curl
        cls.fake = FakeAnthropic()
        claude_api._curl = cls.fake
        fullread.runner.poll_seconds = 0.01
        cls.httpd, cls.url = server.make_server(0)
        cls.token = server.Handler.token
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for _ in range(300):                                   # let the background runner finish before the folder goes
            if fullread.runner.thread is None or not fullread.runner.thread.is_alive():
                break
            time.sleep(0.01)
        claude_api._curl = cls.real_curl
        fullread.runner.poll_seconds = fullread.POLL_SECONDS
        os.environ.pop("ANTHROPIC_API_KEY", None)
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

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

    def test_plan_confirm_run_and_read_back(self):
        status, saved = self.call("/api/full/close-people", {"names": ["Avery Stone"]})
        self.assertEqual((status, saved), (200, {"close_people": ["Avery Stone"]}))
        status, planned = self.call("/api/full/plan")
        self.assertEqual((status, planned["units"], planned["to_send"], planned["cap_usd"]), (200, 2, 2, 150.0))
        self.assertNotIn("_pending", planned)
        self.assertNotIn("message 3", json.dumps(planned))                     # no message text reaches the page
        for body in ({}, {"confirm": "yes"}):
            status, data = self.call("/api/full/start", body)
            self.assertEqual(status, 400)
        self.assertEqual(self.fake.batches, {})
        status, data = self.call("/api/full/start", {"confirm": True})
        self.assertEqual((status, data["started"]), (200, True))
        for _ in range(300):
            status, now = self.call("/api/full/status")
            if not now["active"] and not now["being_read"]:
                break
            time.sleep(0.02)
        self.assertEqual((now["read"], now["ended"]), (2, "finished"))
        self.assertEqual(len(now["batches"][0]["id"]), 8)
        status, people = self.call("/api/full/people")
        self.assertEqual([p["name"] for p in people["people"]], ["Avery Stone", "Jordan Pike"])
        status, found = self.call("/api/full/digests?name=Avery%20Stone")
        self.assertEqual((found["total"], found["digests"][0]["read_by"]), (1, "Opus 5.5"))
        from imx import paths
        log = paths.activity_log().read_text(encoding="utf-8")
        self.assertIn('"full_read"', log)
        self.assertNotIn("message 3", log)


if __name__ == "__main__":
    unittest.main()
