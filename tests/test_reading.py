"""Tests for the reading pilot and for talking to Anthropic.

No test contacts Anthropic. Requests go either to a stand-in web server running
inside the test, or to a stand-in function, so these run offline and cost nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_insights import AVERY, JORDAN, build  # noqa: E402
from imx import claude_api, index, reading, server  # noqa: E402
from imx.claude_api import AIError  # noqa: E402
from imx.query import QueryError  # noqa: E402

GOOD_DIGEST = {"summary": "They planned dinner.", "topics": ["dinner"], "tone": "Easy and warm.", "closeness": "warm",
               "events": [{"date": "2025-01-01", "what": "dinner planned"}], "tensions": [], "promises": [],
               "people": [], "places": [], "notable": ""}


def reply(text=None, model="claude-haiku-5-5", stop_reason="end_turn", tokens_in=1000, tokens_out=200):
    """What claude_api.send() returns, for stand-in senders."""
    usage = {"input_tokens": tokens_in, "output_tokens": tokens_out}
    return {"text": json.dumps(GOOD_DIGEST) if text is None else text, "stop_reason": stop_reason, "model": model,
            "usage": usage, "cost_usd": claude_api.cost_usd(model, usage)}


class StandIn(BaseHTTPRequestHandler):
    """A pretend Anthropic. Records what it was sent and answers from a script."""
    seen = []
    script = []          # list of (status, headers, body) to answer with, in order; the last one repeats

    def log_message(self, *args):
        pass

    def _answer(self, body):
        StandIn.seen.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                             "body": json.loads(body) if body else None})
        status, headers, payload = StandIn.script.pop(0) if len(StandIn.script) > 1 else StandIn.script[0]
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self._answer(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

    def do_GET(self):
        self._answer(b"")


MESSAGE_OK = {"model": "claude-sonnet-5-5", "stop_reason": "end_turn",
              "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "hello"}],
              "usage": {"input_tokens": 1000, "output_tokens": 500}}


@unittest.skipUnless(shutil.which("curl"), "needs curl")
class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-client-test-"))
        os.environ["IMX_DATA_DIR"] = str(cls.tmp / "data")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
        os.environ["IMX_ANTHROPIC_URL"] = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for name in ("IMX_ANTHROPIC_URL", "ANTHROPIC_API_KEY"):
            os.environ.pop(name, None)
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        StandIn.seen = []
        StandIn.script = [(200, {}, MESSAGE_OK)]

    def test_request_has_the_documented_shape(self):
        schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        got = claude_api.send("claude-sonnet-5-5", "be brief", "hi there", schema=schema, effort="medium", max_tokens=8000)
        sent = StandIn.seen[0]
        self.assertEqual((sent["method"], sent["path"]), ("POST", "/v1/messages"))
        self.assertEqual(sent["headers"]["x-api-key"], "sk-ant-test-0123456789abcdef")
        self.assertEqual(sent["headers"]["anthropic-version"], "2026-10-21")
        self.assertEqual(sent["headers"]["content-type"], "application/json")
        self.assertNotIn("anthropic-beta", sent["headers"])
        self.assertEqual(sent["body"], {
            "model": "claude-sonnet-5-5", "max_tokens": 8000, "system": "be brief",
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": "hi there"}]})
        # The reply: text blocks only, and the cost worked out from the reported tokens.
        # 1,000 input at $2 per million + 500 output at $10 per million = $0.002 + $0.005.
        self.assertEqual(got["text"], "hello")
        self.assertEqual(got["usage"], {"input_tokens": 1000, "output_tokens": 500})
        self.assertAlmostEqual(got["cost_usd"], 0.007)

    def test_message_text_with_quotes_and_emoji_arrives_intact(self):
        tricky = 'She said "no" \\ and left 😀\nnext line — done'
        claude_api.send("claude-haiku-5-5", "s", tricky)
        self.assertEqual(StandIn.seen[0]["body"]["messages"][0]["content"], tricky)

    def test_fallbacks_are_only_sent_when_asked_for(self):
        claude_api.send("claude-opus-5-5", "s", "u", fallbacks=True)
        self.assertEqual(StandIn.seen[0]["body"]["fallbacks"], "default")
        self.assertEqual(StandIn.seen[0]["headers"]["anthropic-beta"], "server-side-fallback-2026-07-01")

    def test_nothing_is_left_on_disk_after_a_request(self):
        claude_api.send("claude-haiku-5-5", "s", "private words")
        leftovers = [p for p in (self.tmp / "data").rglob("*") if "request-" in str(p)]
        self.assertEqual(leftovers, [])

    def test_key_is_not_passed_where_other_programs_can_see_it(self):
        import subprocess
        captured = {}
        real = subprocess.run

        def spy(command, **kwargs):
            captured["command"], captured["input"] = command, kwargs.get("input")
            return real(command, **kwargs)

        subprocess.run = spy
        try:
            claude_api.send("claude-haiku-5-5", "s", "u")
        finally:
            subprocess.run = real
        self.assertFalse(any("sk-ant-test" in str(part) for part in captured["command"]))
        self.assertIn(b"sk-ant-test-0123456789abcdef", captured["input"])

    def test_a_declined_request_is_returned_not_raised(self):
        StandIn.script = [(200, {}, dict(MESSAGE_OK, stop_reason="refusal", content=[]))]
        got = claude_api.send("claude-sonnet-5-5", "s", "u")
        self.assertEqual((got["stop_reason"], got["text"]), ("refusal", ""))
        self.assertGreater(got["cost_usd"], 0)

    def test_errors_become_plain_messages(self):
        error = lambda kind, message: {"type": "error", "error": {"type": kind, "message": message}}  # noqa: E731
        cases = [
            ((401, {}, error("authentication_error", "invalid x-api-key")), 401, False, "did not accept the API key"),
            ((400, {}, error("invalid_request_error", "output_config: bad")), 400, False, "output_config: bad"),
            ((429, {"retry-after": "7"}, error("rate_limit_error", "slow")), 429, True, "slow down"),
            ((429, {}, error("rate_limit_error", "spend limit reached")), 429, False, "spend limit reached"),
            ((529, {}, error("overloaded_error", "busy")), 529, True, "trouble"),
        ]
        for script, status, retryable, words in cases:
            StandIn.script = [script]
            with self.assertRaises(AIError) as caught:
                claude_api.send("claude-haiku-5-5", "s", "u")
            self.assertEqual((caught.exception.status, caught.exception.retryable), (status, retryable), words)
            self.assertIn(words, str(caught.exception))
        StandIn.script = [(429, {"retry-after": "7"}, error("rate_limit_error", "slow"))]
        with self.assertRaises(AIError) as caught:
            claude_api.send("claude-haiku-5-5", "s", "u")
        self.assertEqual(caught.exception.retry_after, 7.0)

    def test_waits_as_long_as_asked_then_tries_again(self):
        busy = (429, {"retry-after": "3"}, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
        StandIn.script = [busy, busy, (200, {}, MESSAGE_OK)]
        waits = []
        got = claude_api.send_patiently("claude-haiku-5-5", "s", "u", sleep=waits.append)
        self.assertEqual(got["text"], "hello")
        self.assertEqual(waits, [3.0, 3.0])
        self.assertEqual(len(StandIn.seen), 3)

    def test_gives_up_on_an_error_that_waiting_cannot_fix(self):
        StandIn.script = [(400, {}, {"type": "error", "error": {"type": "invalid_request_error", "message": "no"}})]
        with self.assertRaises(AIError):
            claude_api.send_patiently("claude-haiku-5-5", "s", "u", sleep=lambda _s: self.fail("should not wait"))
        self.assertEqual(len(StandIn.seen), 1)

    def test_unreachable_service_is_a_plain_retryable_error(self):
        saved = os.environ["IMX_ANTHROPIC_URL"]
        os.environ["IMX_ANTHROPIC_URL"] = "http://127.0.0.1:9"       # nothing listens here
        try:
            with self.assertRaises(AIError) as caught:
                claude_api.send("claude-haiku-5-5", "s", "u")
            self.assertTrue(caught.exception.retryable)
            self.assertIn("Could not reach Anthropic", str(caught.exception))
        finally:
            os.environ["IMX_ANTHROPIC_URL"] = saved

    def test_checking_models_sends_no_message_text(self):
        StandIn.script = [(200, {}, {"id": "x"}),
                          (404, {}, {"type": "error", "error": {"type": "not_found_error", "message": "model: nope"}}),
                          (200, {}, {"id": "x"})]
        found = claude_api.check_models(reading.MODELS)
        self.assertEqual(found, {"claude-haiku-5-5": True, "claude-sonnet-5-5": False, "claude-opus-5-5": True})
        self.assertEqual([s["method"] for s in StandIn.seen], ["GET"] * 3)
        self.assertTrue(all(s["body"] is None for s in StandIn.seen))
        self.assertEqual(StandIn.seen[0]["path"], "/v1/models/claude-haiku-5-5")


FAKE_KEYCHAIN = r'''#!/bin/bash
# Stands in for the macOS `security` tool: keeps the secret in a file inside the test folder.
store="$IMX_FAKE_KEYCHAIN"
save() { while [ $# -gt 0 ]; do if [ "$1" = "-w" ]; then printf '%s' "$2" > "$store"; fi; shift; done; }
case "$1" in
  -i) [ -n "$IMX_FAKE_KEYCHAIN_NO_STDIN" ] && exit 1
      read -r line; eval "set -- $line"; save "$@";;
  add-generic-password) save "$@";;
  find-generic-password) [ -f "$store" ] && cat "$store" || exit 44;;
  delete-generic-password) rm -f "$store";;
esac
'''


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class KeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imx-key-test-"))
        (self.tmp / "bin").mkdir()
        tool = self.tmp / "bin" / "security"
        tool.write_text(FAKE_KEYCHAIN)
        tool.chmod(0o755)
        self.saved = {name: os.environ.get(name) for name in ("PATH", "ANTHROPIC_API_KEY", "IMX_FAKE_KEYCHAIN_NO_STDIN")}
        os.environ["PATH"] = "%s%s%s" % (self.tmp / "bin", os.pathsep, os.environ["PATH"])
        os.environ["IMX_FAKE_KEYCHAIN"] = str(self.tmp / "keychain")
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_key_is_saved_and_found_again(self):
        self.assertIsNone(claude_api.api_key())
        claude_api.save_key("  sk-ant-api03-ABCdef_123-456xyz7890  ")
        self.assertEqual(claude_api.api_key(), "sk-ant-api03-ABCdef_123-456xyz7890")
        claude_api.forget_key()
        self.assertIsNone(claude_api.api_key())

    def test_key_is_saved_even_where_the_quiet_way_is_not_supported(self):
        os.environ["IMX_FAKE_KEYCHAIN_NO_STDIN"] = "1"
        claude_api.save_key("sk-ant-api03-ABCdef_123-456xyz7890")
        self.assertEqual(claude_api.api_key(), "sk-ant-api03-ABCdef_123-456xyz7890")

    def test_something_that_is_not_a_key_is_refused(self):
        for bad in ("", "hello", "sk-ant with spaces in it 0123456789", 'sk-ant-"; rm -rf ~; echo "0123456789'):
            with self.assertRaises(AIError):
                claude_api.save_key(bad)
        self.assertIsNone(claude_api.api_key())


def conversation(chat, start, count, minutes_apart=1):
    """`count` alternating messages starting at 'YYYY-MM-DD HH:MM'."""
    base = time.mktime(time.strptime(start, "%Y-%m-%d %H:%M"))
    return [(chat, time.strftime("%Y-%m-%d %H:%M", time.localtime(base + i * minutes_apart * 60)), i % 2, "message %d" % i)
            for i in range(count)]


class ReadingTests(unittest.TestCase):
    """One fictional history:

    Avery, 2025: 70 messages on 1 Jan; 10 on 2 Jan (within 3 days, but the first unit is already
                 past 60, so a new unit); 5 on 3 Jan (joins the 10); 4 on 20 Jan (17 days later: new unit);
                 310 on 1 Mar (longer than 300: split into 300 + 10).
    Avery, 2024: 3 messages. Jordan, 2025: 8 messages on 5 Feb.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-reading-test-"))
        rows = (conversation(AVERY, "2024-06-01 10:00", 3) + conversation(AVERY, "2025-01-01 09:00", 70)
                + conversation(AVERY, "2025-01-02 09:00", 10) + conversation(AVERY, "2025-01-03 09:00", 5)
                + conversation(AVERY, "2025-01-20 09:00", 4) + conversation(JORDAN, "2025-02-05 09:00", 8)
                + conversation(AVERY, "2025-03-01 09:00", 310))
        build(cls.tmp, rows)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        self.conn = index.connect(readonly=True)
        store = self.tmp / "data" / "reading.db"
        if store.exists():
            store.unlink()
        reading._job = None

    def tearDown(self):
        self.conn.close()

    def run_job(self, send, cap=reading.PILOT_CAP_USD, people=None, per_person=40):
        planned = reading.plan(self.conn, people or ["Avery Stone"], "2025", per_person)
        job = reading.Job(planned["_units"], cap_usd=cap, send=send)
        job.start()
        self.wait(job)
        return job

    def wait(self, job):
        for thread in job.threads:
            thread.join(timeout=30)
        for _ in range(600):
            if not job.running():
                return
            time.sleep(0.01)
        self.fail("the run did not finish")

    def test_messages_are_cut_into_units_by_the_stated_rules(self):
        units = reading.units_for(self.conn, "Avery Stone", "2025")
        self.assertEqual([u["messages"] for u in units], [70, 15, 4, 300, 10])
        self.assertEqual(sum(u["messages"] for u in units), 399)          # every 2025 message is in exactly one unit
        first = units[0]["transcript"].splitlines()
        self.assertEqual(first[0], "[2025-01-01 09:00] Avery Stone: message 0")
        self.assertEqual(first[1], "[2025-01-01 09:01] Me: message 1")
        # The same messages always give the same unit ids, so a digest is never paid for twice.
        again = reading.units_for(self.conn, "Avery Stone", "2025")
        self.assertEqual([u["id"] for u in units], [u["id"] for u in again])
        self.assertEqual(len({u["id"] for u in units}), 5)
        self.assertEqual([u["messages"] for u in reading.units_for(self.conn, "Avery Stone", "2024")], [3])

    def test_even_spread_covers_start_and_end(self):
        self.assertEqual(reading._spread(list(range(10)), 5), [0, 2, 4, 6, 8])
        self.assertEqual(reading._spread(list(range(3)), 5), [0, 1, 2])

    def test_defaults_are_the_last_full_year_and_the_busiest_people(self):
        self.assertEqual(reading.default_year(self.conn), "2024")         # 2025 is the latest year, so 2024 is the last full one
        self.assertEqual(reading.default_people(self.conn, "2025"), ["Avery Stone", "Jordan Pike"])

    def test_plan_describes_what_would_be_sent_and_sends_nothing(self):
        planned = reading.plan(self.conn, ["Avery Stone", "Jordan Pike"], "2025", 2)
        self.assertEqual([(p["name"], p["units_in_year"], p["units_chosen"], p["messages_chosen"]) for p in planned["people"]],
                         [("Avery Stone", 5, 2, 74), ("Jordan Pike", 1, 1, 8)])      # Avery: units 0 and 2 of 5 (70 + 4)
        self.assertEqual((planned["units"], planned["messages"]), (3, 82))
        self.assertEqual([m["units_to_read"] for m in planned["models"]], [3, 3, 3])
        self.assertEqual((planned["cap_usd"], planned["spent_usd"], planned["remaining_usd"]), (30.0, 0.0, 30.0))
        # Dearer models are estimated dearer, and the estimate is the sum of its parts.
        costs = [m["estimated_cost_usd"] for m in planned["models"]]
        self.assertTrue(costs[0] < costs[1] < costs[2])
        self.assertAlmostEqual(planned["estimated_cost_usd"], sum(costs), places=2)
        self.assertFalse((self.tmp / "data" / "reading.db").exists() and reading.spent() > 0)

    def test_reading_stores_digests_and_never_pays_twice(self):
        calls = []

        def send(model, system, text, **kwargs):
            calls.append((model, text, kwargs))
            return reply(model=model)

        job = self.run_job(send)
        self.assertEqual(len(calls), 15)                                  # 5 units x 3 models
        self.assertFalse(job.capped)
        self.assertEqual([m["ok"] for m in job.status()["models"]], [5, 5, 5])
        model, text, kwargs = calls[0]
        self.assertIn("<transcript>", text)
        self.assertIn("Conversation with Avery Stone", text)
        self.assertEqual((kwargs["effort"], kwargs["max_tokens"], kwargs["schema"]), ("medium", 8000, reading.DIGEST_SCHEMA))
        self.assertNotIn("fallbacks", kwargs)                             # a fallback would blur which model wrote what
        # Spend is the sum of what each reply cost: 5 replies per model at 1,000 in / 200 out.
        # Haiku 5 x (0.0001 + 0.0001) + Sonnet 5 x (0.002 + 0.002) + Opus 5 x (0.004 + 0.004) = 0.001 + 0.02 + 0.04
        self.assertAlmostEqual(reading.spent(), 0.061)
        second = self.run_job(lambda *a, **k: self.fail("already read: nothing should be sent"))
        self.assertEqual([m["total"] for m in second.status()["models"]], [0, 0, 0])
        self.assertAlmostEqual(reading.spent(), 0.061)

    def test_spending_stops_at_the_cap(self):
        sent = []

        def send(model, system, text, **kwargs):
            sent.append(model)
            # The dearest reply the service could give: every byte sent counted as a token, and all the output allowed.
            return reply(model=model, tokens_in=len((system + text).encode("utf-8")), tokens_out=kwargs["max_tokens"])

        job = self.run_job(send, cap=0.60)
        self.assertTrue(job.capped)
        self.assertTrue(job.status()["stopped_at_cap"])
        self.assertLessEqual(reading.spent(), 0.60)
        self.assertGreater(reading.spent(), 0)
        self.assertLess(len(sent), 15)

    def test_worst_case_is_never_below_what_a_reply_can_cost(self):
        # Includes a conversation of nothing but emoji, where a character can be several tokens.
        units = reading.units_for(self.conn, "Avery Stone", "2025")
        emoji = dict(units[2], transcript="\U0001F600" * 2000)
        for unit in (units[3], emoji):
            for model in reading.MODELS:
                price_in, price_out = claude_api.PRICES[model]
                sent_bytes = len((reading.SYSTEM_PROMPT + reading._request_text(unit)).encode("utf-8"))
                dearest = (sent_bytes * price_in + reading.MAX_OUTPUT_TOKENS * price_out) / 1e6
                self.assertGreaterEqual(reading._worst_case(model, unit), dearest)

    def test_declined_and_unreadable_replies_are_recorded_and_still_counted_as_spent(self):
        def send(model, system, text, **kwargs):
            if model == "claude-sonnet-5-5":
                return reply(text="", model=model, stop_reason="refusal")
            if model == "claude-opus-5-5":
                return reply(text="I could not do that.", model=model)
            return reply(model=model)

        job = self.run_job(send)
        by_model = {m["model"]: m for m in job.status()["models"]}
        self.assertEqual(by_model["claude-haiku-5-5"]["ok"], 5)
        self.assertEqual(by_model["claude-sonnet-5-5"]["declined"], 5)
        self.assertEqual(by_model["claude-opus-5-5"]["unreadable"], 5)
        found = {row["model"]: row for row in reading.results(self.conn)["models"]}
        self.assertEqual((found["claude-sonnet-5-5"]["digests"], found["claude-sonnet-5-5"]["declined"]), (0, 5))
        self.assertGreater(found["claude-sonnet-5-5"]["spent_usd"], 0)

    def test_a_rejected_key_stops_everything(self):
        calls = []

        def send(model, system, text, **kwargs):
            calls.append(model)
            raise AIError("Anthropic did not accept the API key.", status=401)

        job = self.run_job(send)
        self.assertLessEqual(len(calls), len(reading.MODELS) * reading.WORKERS_PER_MODEL)
        self.assertTrue(any("did not accept" in problem for problem in job.status()["problems"]))
        self.assertEqual(reading.spent(), 0.0)

    def test_one_model_being_unusable_does_not_stop_the_others(self):
        def send(model, system, text, **kwargs):
            if model == "claude-opus-5-5":
                raise AIError("Anthropic rejected the request: model not available", status=400)
            return reply(model=model)

        job = self.run_job(send)
        by_model = {m["model"]: m for m in job.status()["models"]}
        self.assertEqual((by_model["claude-haiku-5-5"]["ok"], by_model["claude-sonnet-5-5"]["ok"]), (5, 5))
        self.assertEqual(by_model["claude-opus-5-5"]["ok"], 0)
        self.assertTrue(any(problem.startswith("Opus 5.5") for problem in job.status()["problems"]))

    def test_results_work_out_cost_per_thousand_messages_and_the_whole_history(self):
        self.run_job(lambda model, system, text, **k: reply(model=model))
        found = reading.results(self.conn)
        haiku = found["models"][0]
        # Haiku read all 399 of Avery's 2025 messages in 5 replies for 5 x $0.0002 = $0.001.
        self.assertEqual((haiku["digests"], haiku["messages_read"]), (5, 399))
        self.assertAlmostEqual(haiku["spent_usd"], 0.001)
        self.assertAlmostEqual(haiku["usd_per_thousand_messages"], round(0.001 / 399 * 1000, 4))
        # The whole history is 410 messages (3 + 399 + 8), so reading it all costs 0.001 / 399 x 410.
        self.assertEqual(found["messages_in_history"], 410)
        self.assertAlmostEqual(haiku["whole_history_usd"], round(0.001 / 399 * 410, 2))
        self.assertAlmostEqual(found["spent_usd"], 0.061)

    def test_review_hides_which_model_wrote_each_reading(self):
        self.run_job(lambda model, system, text, **k: reply(text=json.dumps(dict(GOOD_DIGEST, summary="by " + model)), model=model))
        waiting = reading.review_queue()
        self.assertEqual((waiting["ready"], waiting["reviewed"], waiting["wanted"]), (5, 0, 30))
        shown = reading.unit_for_review(self.conn, waiting["next"])
        self.assertEqual([r["slot"] for r in shown["readings"]], ["A", "B", "C"])
        sent_to_page = json.dumps({k: v for k, v in shown.items() if k != "readings"}) + json.dumps(
            [{k: v for k, v in r.items() if k != "digest"} for r in shown["readings"]])
        for model in reading.MODELS + list(claude_api.MODEL_NAMES.values()):
            self.assertNotIn(model, sent_to_page)
        self.assertEqual(len(shown["messages"]), shown["unit"]["messages"])
        # The letters are not always the same model: across the five units more than one order appears.
        orders = {tuple(reading._slots(unit_id)) for unit_id in waiting["all"]}
        self.assertGreater(len(orders), 1)

    def test_a_verdict_is_credited_to_the_model_behind_the_letter(self):
        self.run_job(lambda model, system, text, **k: reply(text=json.dumps(dict(GOOD_DIGEST, summary="by " + model)), model=model))
        unit_id = reading.review_queue()["next"]
        shown = reading.unit_for_review(self.conn, unit_id)
        opus_slot = next(r["slot"] for r in shown["readings"] if r["digest"]["summary"] == "by claude-opus-5-5")
        reading.save_review(unit_id, opus_slot, "wrong", "got the date wrong")
        other = next(r["slot"] for r in shown["readings"] if r["slot"] != opus_slot)
        reading.save_review(unit_id, other, "right")
        found = {row["model"]: row for row in reading.results(self.conn)["models"]}
        self.assertEqual((found["claude-opus-5-5"]["wrong"], found["claude-opus-5-5"]["reviewed"]), (1, 1))
        self.assertEqual(sum(row["right"] for row in found.values()), 1)
        after = reading.review_queue()
        self.assertEqual(after["reviewed"], 1)
        self.assertNotEqual(after["next"], unit_id)
        again = reading.unit_for_review(self.conn, unit_id)
        self.assertEqual(next(r for r in again["readings"] if r["slot"] == opus_slot)["note"], "got the date wrong")
        with self.assertRaises(QueryError):
            reading.save_review(unit_id, "A", "brilliant")

    def test_every_request_is_timed_and_waits_and_failures_are_written_down(self):
        def send(model, system, text, on_wait=None, **kwargs):
            if model == "claude-opus-5-5":
                raise AIError("Anthropic rejected the request: output_config: nope", status=400)
            if model == "claude-sonnet-5-5":
                on_wait(7.0, 429)                     # told to slow down once before each reply
            time.sleep(0.02)
            return reply(model=model)

        self.run_job(send)
        found = reading.pilot_report(self.conn)
        by_model = {m["model"]: m for m in found["models"]}
        haiku, sonnet, opus = (by_model[m] for m in reading.MODELS)
        self.assertEqual((haiku["requests"], sonnet["requests"], opus["requests"]), (5, 5, 0))
        self.assertGreaterEqual(haiku["seconds_median"], 0.0)
        self.assertEqual((haiku["times_told_to_wait"], haiku["seconds_spent_waiting"]), (0, 0.0))
        self.assertEqual((sonnet["times_told_to_wait"], sonnet["seconds_spent_waiting"]), (5, 35.0))
        # 399 messages read with 5 x 1,000 input tokens and 5 x 200 output tokens.
        self.assertEqual((haiku["input_tokens_per_message"], haiku["output_tokens_per_request"]), (round(5000 / 399, 1), 200.0))
        # Each of Opus's workers meets the rejection once and then stops, so it is written down once or twice.
        self.assertEqual(len(found["failures"]), 1)
        failure = found["failures"][0]
        self.assertEqual((failure["model"], failure["status"], failure["problem"]),
                         ("claude-opus-5-5", 400, "Anthropic rejected the request: output_config: nope"))
        self.assertIn(failure["times"], range(1, reading.WORKERS_PER_MODEL + 1))
        self.assertEqual(len(found["runs"]), 1)
        self.assertEqual((found["runs"][0]["ended"], found["runs"][0]["requests_planned"]), ("finished", 15))
        self.assertEqual((found["units_read"], found["messages_per_unit_largest"]), (5, 300))

    def test_the_report_holds_numbers_only(self):
        self.run_job(lambda model, system, text, **k: reply(model=model))
        text = reading.pilot_report(self.conn)["text"]
        for private in ("Avery", "Jordan", "Stone", "message 1", "They planned dinner"):
            self.assertNotIn(private, text)
        for expected in ("Haiku 5.5", "Opus 5.5", "per 1,000 messages", "seconds per request", "Failures (cost nothing):", "none"):
            self.assertIn(expected, text)

    def test_stop_ends_the_run_early(self):
        started = threading.Event()

        def send(model, system, text, **kwargs):
            started.set()
            time.sleep(0.05)
            return reply(model=model)

        planned = reading.plan(self.conn, ["Avery Stone"], "2025", 40)
        job = reading.Job(planned["_units"], send=send)
        job.start()
        started.wait(5)
        job.stop()
        self.wait(job)
        done = sum(m["ok"] for m in job.status()["models"])
        self.assertLess(done, 15)
        self.assertTrue(job.status()["stopped_by_you"])


class ReadingRouteTests(unittest.TestCase):
    """The same safeguards, checked through the web interface's own requests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-reading-route-test-"))
        build(cls.tmp, conversation(AVERY, "2025-01-01 09:00", 12) + conversation(AVERY, "2026-01-01 09:00", 2))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-0123456789abcdef"
        cls.httpd, cls.url = server.make_server(0)
        cls.token = server.Handler.token
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
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

    def test_plan_reaches_the_page_without_any_message_text(self):
        status, planned = self.call("/api/reading/plan")
        self.assertEqual(status, 200)
        self.assertEqual((planned["year"], planned["units"], planned["messages"]), ("2025", 1, 12))
        self.assertNotIn("_units", planned)
        self.assertNotIn("message 3", json.dumps(planned))

    def test_nothing_starts_without_the_confirm_button(self):
        original = reading.Job
        reading.Job = lambda *a, **k: self.fail("a run must not start without confirmation")
        try:
            for body in ({}, {"confirm": "yes"}, {"confirm": 1}):
                status, data = self.call("/api/reading/start", body)
                self.assertEqual(status, 400)
                self.assertIn("Confirm", data["error"])
        finally:
            reading.Job = original

    def test_a_confirmed_run_reads_and_logs_no_message_text(self):
        from imx import paths
        original = claude_api.send_patiently
        reading._job = None
        try:
            # start() takes its sender as a default argument, so swap the job's sender instead.
            real_job = reading.Job
            reading.Job = lambda units, **k: real_job(units, send=lambda model, s, t, **kw: reply(model=model))
            status, data = self.call("/api/reading/start", {"confirm": True, "year": "2025"})
            self.assertEqual(status, 200)
            self.assertTrue(data["started"])
            for _ in range(100):
                status, now = self.call("/api/reading/status")
                if not now["running"]:
                    break
                time.sleep(0.05)
            self.assertEqual([m["ok"] for m in now["models"]], [1, 1, 1])
            log = paths.activity_log().read_text(encoding="utf-8")
            self.assertIn('"reading_pilot"', log)
            self.assertIn("Avery Stone", log)
            self.assertNotIn("message 3", log)
            status, waiting = self.call("/api/reading/queue")
            status, shown = self.call("/api/reading/unit?id=" + waiting["next"])
            self.assertEqual(len(shown["messages"]), 12)
            status, saved = self.call("/api/reading/review", {"unit_id": waiting["next"], "slot": "B", "verdict": "missing", "note": "n"})
            self.assertEqual((status, saved), (200, {"saved": True}))
            status, found = self.call("/api/reading/results")
            self.assertEqual(sum(row["missing"] for row in found["models"]), 1)
        finally:
            reading.Job = real_job
            claude_api.send_patiently = original


if __name__ == "__main__":
    unittest.main()
