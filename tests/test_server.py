"""Tests for the web server's protections, the AI safeguards and the AI-tool connection.

No test contacts Anthropic. The one place a request would be sent is replaced
with a stand-in, so these run offline and cost nothing.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
from imx import activity, ai, claude_api, index, mcp_server, paths, server  # noqa: E402
from imx.query import Filters, QueryError  # noqa: E402


def FakeResponse():
    """Stands in for Anthropic's reply."""
    return {"model": "claude-opus-5-5", "stop_reason": "end_turn", "text": "", "usage": {}, "cost_usd": 0.0}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="imx-server-test-"))
        fixture = cls.tmp / "fixture"
        os.environ["IMX_DATA_DIR"] = str(cls.tmp / "data")
        os.environ["IMX_CHAT_DB"] = str(make_fixture.build(fixture))
        os.environ["IMX_ADDRESSBOOK_DIR"] = str(fixture / "AddressBook")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        index.run_import(progress=lambda _line: None)
        cls.httpd, cls.url = server.make_server(0)
        cls.token = server.Handler.token
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def call(self, path, body=None, token=True, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-IMX-Token"] = self.token if token is True else token
        if host:
            headers["Host"] = host
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url.rstrip("/") + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}"), response.headers
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw), error.headers
            except ValueError:
                return error.code, {"raw": raw.decode(errors="replace")}, error.headers

    # ---- who may talk to the server -------------------------------------------------

    def test_listens_only_on_this_machine(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_data_needs_the_secret_token(self):
        self.assertEqual(self.call("/api/meta", token=False)[0], 403)
        self.assertEqual(self.call("/api/meta", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/search?q=dinner", token=False)[0], 403)
        self.assertEqual(self.call("/api/ai/ask", body={"confirm": True}, token=False)[0], 403)
        self.assertEqual(self.call("/api/meta")[0], 200)

    def test_other_host_names_are_refused(self):
        self.assertEqual(self.call("/api/meta", host="evil.example")[0], 403)
        self.assertEqual(self.call("/", host="evil.example:80")[0], 403)

    def test_page_cannot_load_anything_external(self):
        status, _body, headers = self.call("/api/meta")
        policy = headers.get("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_search_and_statistics_over_http(self):
        status, data, _ = self.call("/api/search?q=dinner+friday&contact=Avery+Stone")
        self.assertEqual((status, data["total"]), (200, 2))
        self.assertEqual(self.call("/api/stats/people")[1]["totals"]["messages"], 20)
        self.assertEqual(self.call("/api/stats/trends?bucket=fortnight")[0], 400)
        self.assertEqual(self.call("/api/search?from=notadate")[0], 200)   # unknown parameter names are ignored
        self.assertEqual(self.call("/api/search?date_from=notadate")[0], 400)
        self.assertEqual(self.call("/api/nope")[0], 404)

    # ---- labels, the opening screen, and updating from the browser -------------------

    def test_status_and_home(self):
        status, data, _ = self.call("/api/status")
        self.assertEqual((status, data["ready"], data["current"], data["updating"]), (200, True, True, False))
        self.assertEqual(self.call("/api/status", token=False)[0], 403)
        status, home, _ = self.call("/api/home")
        self.assertEqual(status, 200)
        self.assertEqual(home["totals"]["messages"], 20)
        self.assertEqual(home["hidden"], {"senders": 1, "messages": 1, "suggested": 0})
        self.assertEqual(home["inner_circle"], [])
        self.assertIn("signals", home["import"])

    def test_labels_over_http(self):
        from imx import labels
        try:
            status, data, _ = self.call("/api/labels/save", body={"name": "Avery Stone", "label": "family", "pinned": True})
            self.assertEqual((status, data["labels"]["Avery Stone"]), (200, {"label": "family", "pinned": True, "keep": False}))
            self.assertEqual(self.call("/api/labels/save", body={"name": "Avery Stone", "label": "nemesis"})[0], 400)
            self.assertEqual(self.call("/api/labels/save", body={"name": "Avery Stone", "label": "family"}, token=False)[0], 403)
            self.assertEqual(self.call("/api/labels")[1]["choices"], ["family", "close friend", "friend", "work", "service"])
        finally:
            labels.save("Avery Stone")

    def test_update_from_the_browser(self):
        self.assertEqual(self.call("/api/update", body={}, token=False)[0], 403)      # needs the secret token
        status, data, _ = self.call("/api/update", body={})
        self.assertEqual(status, 200)
        self.assertEqual(data["report"]["total_in_local_database"], 22)              # nothing doubled
        self.assertIn("Import finished", data["summary"])

    def test_only_one_update_runs_at_a_time(self):
        self.assertTrue(server._update_lock.acquire(blocking=False))
        try:
            status, data, _ = self.call("/api/update", body={})
            self.assertEqual(status, 409)
            self.assertIn("already running", data["error"])
        finally:
            server._update_lock.release()

    def test_new_signal_screens_over_http(self):
        for path in ("/api/reactions", "/api/read-times", "/api/direct-replies", "/api/sharing", "/api/language", "/api/circles"):
            status, data, _ = self.call(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(any(key.endswith("definition") for key in data), path)

    # ---- AI safeguards ----------------------------------------------------------------

    def test_nothing_is_sent_without_confirmation(self):
        calls = []
        original = ai._call
        ai._call = lambda *a, **k: calls.append(a) or (FakeResponse(), "answer")
        try:
            status, data, _ = self.call("/api/ai/ask", body={"question": "What about dinner?", "q": "dinner", "filters": {}})
            self.assertEqual(status, 400)
            self.assertIn("Confirm", data["error"])
            status, _data, _ = self.call("/api/ai/topics", body={"filters": {}, "confirm": "yes"})   # must be exactly true
            self.assertEqual(status, 400)
            self.assertEqual(calls, [])
        finally:
            ai._call = original

    def test_preview_sends_nothing_and_reports_what_is_left_out(self):
        original = ai.MAX_MESSAGES
        ai.MAX_MESSAGES = 5
        try:
            status, data, _ = self.call("/api/ai/preview", body={"q": "", "filters": {}, "strategy": "recent"})
        finally:
            ai.MAX_MESSAGES = original
        self.assertEqual(status, 200)
        self.assertEqual(data["would_send"], 5)
        # 22 imported, minus 1 tapback, 2 messages with no words (a photo, an unreadable body) and 1 automated code.
        self.assertEqual(data["total_matching"], 18)
        self.assertEqual(data["not_sent"], 13)
        self.assertEqual(data["last_day"], "2025-06-01")      # 'recent' keeps the newest

    def test_confirmed_request_sends_only_the_selection_and_logs_no_text(self):
        seen = {}
        original = ai._call

        def fake(user_content, schema=None):
            seen["content"] = user_content
            return FakeResponse(), "They agreed on Friday."

        ai._call = fake
        try:
            status, data, _ = self.call("/api/ai/ask", body={
                "question": "When is dinner?", "q": "dinner", "filters": {"contact": "Avery Stone"},
                "strategy": "recent", "confirm": True})
        finally:
            ai._call = original
        self.assertEqual(status, 200)
        self.assertEqual((data["answer"], data["messages_sent"], data["total_matching"]), ("They agreed on Friday.", 2, 2))
        self.assertIn("Are we still on for dinner friday?", seen["content"])
        self.assertNotIn("budget", seen["content"])           # Jordan's messages were not part of the selection
        self.assertNotIn("kayak", seen["content"])
        self.assertTrue(seen["content"].rstrip().endswith("Question: When is dinner?"))
        log = paths.activity_log().read_text(encoding="utf-8")
        self.assertIn('"ask_ai"', log)
        self.assertIn("Avery Stone", log)
        self.assertNotIn("dinner friday", log)                # the log never holds message text

    def test_missing_key_gives_a_plain_message(self):
        # With no API key saved, a request must fail with words, not a crash, and nothing is sent.
        conn = index.connect(readonly=True)
        saved_path = os.environ["PATH"]
        os.environ["PATH"] = ""                               # no Keychain tool to find a key with
        try:
            with self.assertRaises(ai.AIError) as caught:
                ai.ask(conn, "anything?", "dinner", Filters(), "recent")
            self.assertIn("API key", str(caught.exception))
        finally:
            os.environ["PATH"] = saved_path
            conn.close()

    def test_api_key_comes_only_from_the_environment_or_the_keychain(self):
        saved_path = os.environ["PATH"]
        os.environ["PATH"] = ""                               # no Keychain tool available
        try:
            self.assertIsNone(claude_api.api_key())           # nothing on disk is ever consulted
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-the-environment"
            self.assertEqual(claude_api.api_key(), "sk-ant-from-the-environment")
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ["PATH"] = saved_path

    def rpc(self, *requests):
        out = io.StringIO()
        mcp_server.serve(io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_mcp_handshake_and_tool_list(self):
        replies = self.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        self.assertEqual(len(replies), 3)                     # the notification gets no reply
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        names = [t["name"] for t in replies[1]["result"]["tools"]]
        self.assertEqual(names, ["search_messages", "get_conversation_context", "list_conversations",
                                 "message_statistics", "person_profile", "keyword_trend", "list_topics"])
        for tool in replies[1]["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
        self.assertEqual(replies[2]["result"], {})

    def test_mcp_search_is_capped_and_logged(self):
        replies = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "search_messages", "arguments": {"query": "", "limit": 9999}}})
        result = json.loads(replies[0]["result"]["content"][0]["text"])
        self.assertLessEqual(len(result["messages"]), mcp_server.MAX_RESULTS)
        self.assertEqual(result["total_matching"], 20)        # the automated code is not offered to AI tools
        self.assertIn("connected_tool", paths.activity_log().read_text(encoding="utf-8"))

    def test_mcp_offers_nothing_that_changes_data(self):
        names = " ".join(t["name"] for t in mcp_server.TOOLS)
        for word in ("delete", "save", "write", "update", "import", "send"):
            self.assertNotIn(word, names)

    def test_mcp_bad_input_does_not_end_the_connection(self):
        out = io.StringIO()
        mcp_server.serve(io.StringIO(
            "this is not json\n"
            + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "no_such_tool"}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                          "params": {"name": "get_conversation_context", "arguments": {"message_id": "abc"}}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                          "params": {"name": "list_conversations", "arguments": {}}}) + "\n"), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertTrue(replies[1]["result"]["isError"])
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertFalse(replies[3]["result"]["isError"])

    def test_activity_list(self):
        activity.record("connected_tool", tool="list_conversations", messages_returned=0)
        self.assertEqual(activity.recent(1)[0]["tool"], "list_conversations")


if __name__ == "__main__":
    unittest.main()
