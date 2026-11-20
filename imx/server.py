"""The local web interface.

Runs a small web server that only this Mac can reach (address 127.0.0.1), and
opens it in your browser. Three protections keep other things out:

1. It listens on 127.0.0.1 only, so no other device on the network can connect.
2. Every data request must carry a secret token created fresh each time the
   tool starts. A web page you happen to visit cannot know it, so it cannot
   read your messages or trigger an AI request.
3. Requests addressed to any other host name are refused.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Tuple
from urllib.parse import parse_qs, urlparse

from . import (__version__, activity, ai, check, claude_api, extract, fullread, histories, index, insights, junk, labels, paths,
               portrait, query, reading,
               science, signals, stats, topics, update)
from .query import Filters, QueryError

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY_BYTES = 1_000_000


class Handler(BaseHTTPRequestHandler):
    token = ""
    allowed_hosts: Tuple[str, ...] = ()
    server_version = "iMessageExplorer/" + __version__

    def log_message(self, fmt, *args):  # keep the Terminal window quiet
        pass

    # ---- plumbing ----------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # The page may only load its own files and talk to this server. Nothing external.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                         "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.allowed_hosts

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-IMX-Token", ""), self.token)

    def _filters(self, params: Dict[str, str]) -> Filters:
        return Filters.from_dict(params)

    # ---- routes ------------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        parsed = urlparse(self.path)
        if parsed.path in STATIC_FILES:
            name, content_type = STATIC_FILES[parsed.path]
            body = (paths.static_dir() / name).read_bytes()
            if name == "index.html":
                body = body.replace(b"__IMX_TOKEN__", self.token.encode("ascii"))
            return self._send(200, body, content_type)
        if parsed.path.startswith("/api/"):
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._api(parsed.path, params, None)
        self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            return self._json(413, {"error": "Request too large."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "The request was not valid."})
        self._api(parsed.path, {}, body)

    def _update(self) -> None:
        try:
            self._json(200, _run_update())
        except QueryError as exc:
            self._json(409, {"error": str(exc)})
        except extract.SourceError as exc:
            self._json(403, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "The update failed: %s" % exc})

    def _app(self, path: str, body) -> None:
        """Checking for a newer version, installing it, and quitting. None of these need any messages."""
        try:
            if path == "/api/app/check" and body is None:
                return self._json(200, update.check())
            if path == "/api/app/update" and body is not None:
                found = update.check()
                if not found["update_available"]:
                    return self._json(200, {"updated_to": None, "message": "You already have the newest version."})
                result = update.apply(str(found["latest"]))
                self._json(200, result)
                return _stop_soon("restart")
            if path == "/api/quit" and body is not None:
                self._json(200, {"stopping": True})
                return _stop_soon("quit")
        except update.UpdateError as exc:
            return self._json(502, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": "Something went wrong: %s" % exc})
        self._json(404, {"error": "Unknown request."})

    def _api(self, path: str, params: Dict[str, str], body) -> None:
        if not self._token_ok():
            return self._json(403, {"error": "This page is out of date. Reload it."})
        # These two work before the first import and before an upgrade, so they come first.
        if path == "/api/update" and body is not None:
            return self._update()
        if path == "/api/status":
            return self._json(200, _status())
        if path.startswith("/api/app") or path == "/api/quit":
            return self._app(path, body)
        routes = POST_ROUTES if body is not None else GET_ROUTES
        route = routes.get(path)
        if route is None:
            return self._json(404, {"error": "Unknown request."})
        if not index.is_ready():
            return self._json(409, {"error": "No messages have been brought in yet. Press Update messages."})
        if not index.is_current():
            return self._json(409, {"error": "This version adds new measurements. Press Update messages to add them."})
        conn = index.connect(readonly=True)
        try:
            self._json(200, route(conn, body if body is not None else params))
        except QueryError as exc:
            self._json(400, {"error": str(exc)})
        except ai.AIError as exc:
            self._json(502, {"error": str(exc)})
        except Exception as exc:  # show a plain message instead of a broken page
            self._json(500, {"error": "Something went wrong: %s" % exc})
        finally:
            conn.close()


_update_lock = threading.Lock()


# Set while the server is running, so a request can ask it to stop or restart.
_running = {"httpd": None, "then": None}


def _stop_soon(then: str) -> None:
    """Stop the server just after the current reply has gone out. `then` is "restart" or "quit"."""
    httpd = _running["httpd"]
    if httpd is None:
        return
    _running["then"] = then
    threading.Timer(0.3, httpd.shutdown).start()


def _status() -> dict:
    """Whether there is anything to show yet. Works before the first import and before an upgrade."""
    return {"version": __version__, "ready": index.is_ready(), "current": index.is_current(),
            "updating": _update_lock.locked(), "can_update_itself": update.is_managed()}


def _run_update() -> dict:
    """Bring in new messages. Only one update runs at a time."""
    if not _update_lock.acquire(blocking=False):
        raise QueryError("An update is already running. Give it a minute.")
    try:
        report = index.run_import(progress=lambda _line: None)
        stats._cache.clear()
        return {"report": report, "summary": index.describe_report(report)}
    finally:
        _update_lock.release()


def _meta(conn, _params) -> dict:
    totals = stats.people(conn, Filters())["totals"]
    return {
        "version": __version__,
        "import": index.last_report(conn),
        "totals": totals,
        "ai": ai.status(),
        "data_folder": str(paths.data_dir()),
        "launcher": str(paths.launcher()),
        "python": sys.executable,
        "activity": activity.recent(30),
    }


def _search(conn, p) -> dict:
    return query.search(conn, p.get("q", ""), Filters.from_dict(p), limit=int(p.get("limit", 50)),
                        offset=int(p.get("offset", 0)), sort=p.get("sort", "newest"))


GET_ROUTES: Dict[str, Callable] = {
    "/api/meta": _meta,
    "/api/contacts": lambda conn, p: {"contacts": query.contacts(conn)},
    "/api/search": _search,
    "/api/context": lambda conn, p: query.context(conn, int(p.get("id", 0))),
    "/api/stats/people": lambda conn, p: stats.people(conn, Filters.from_dict(p)),
    "/api/stats/trends": lambda conn, p: stats.trends(conn, Filters.from_dict(p), p.get("bucket", "month")),
    "/api/stats/response": lambda conn, p: stats.response(conn, Filters.from_dict(p)),
    "/api/stats/words": lambda conn, p: stats.words(conn, Filters.from_dict(p)),
    "/api/stats/keyword": lambda conn, p: stats.keyword_trend(conn, p.get("q", ""), Filters.from_dict(p), p.get("bucket", "month")),
    "/api/topics": lambda conn, p: {"topics": topics.overview(conn, Filters.from_dict(p))},
    "/api/tone/people": lambda conn, p: insights.tone_by_person(conn, Filters.from_dict(p)),
    "/api/tone/time": lambda conn, p: insights.tone_over_time(conn, Filters.from_dict(p), p.get("bucket", "month")),
    "/api/tone/moments": lambda conn, p: insights.tone_moments(conn, Filters.from_dict(p)),
    "/api/importance": lambda conn, p: insights.importance(conn, Filters.from_dict(p)),
    "/api/community": lambda conn, p: dict(insights.community(conn), drift=insights.drift(conn)),
    "/api/balance": lambda conn, p: insights.balance(conn, Filters.from_dict(p)),
    "/api/expressions": lambda conn, p: insights.expressions(conn, Filters.from_dict(p), p.get("bucket", "year")),
    "/api/style": lambda conn, p: insights.style(conn, Filters.from_dict(p)),
    "/api/phrases": lambda conn, p: insights.long_phrases(conn, Filters.from_dict(p)),
    "/api/profile": lambda conn, p: insights.profile(conn, p.get("contact", ""), deep=False),
    "/api/signature": lambda conn, p: insights.signature(conn, p.get("contact", "")),
    "/api/reactions": lambda conn, p: signals.reactions(conn, Filters.from_dict(p)),
    "/api/read-times": lambda conn, p: signals.read_times(conn, Filters.from_dict(p)),
    "/api/direct-replies": lambda conn, p: signals.replies(conn, Filters.from_dict(p)),
    "/api/sharing": lambda conn, p: signals.sharing(conn, Filters.from_dict(p)),
    "/api/language": lambda conn, p: science.language(conn, Filters.from_dict(p)),
    "/api/circles": lambda conn, p: science.circles(conn),
    "/api/labels": lambda conn, p: {"labels": labels.load(), "choices": list(labels.LABELS)},
    "/api/home": lambda conn, p: _home(conn),
    "/api/junk": lambda conn, p: junk.report(conn),
    "/api/reading/plan": lambda conn, p: _reading_plan(conn, p),
    "/api/reading/status": lambda conn, p: reading.status(),
    "/api/reading/results": lambda conn, p: reading.results(conn),
    "/api/reading/report": lambda conn, p: reading.pilot_report(conn),
    "/api/me": lambda conn, p: dict(portrait.read(), status=portrait.status()),
    "/api/me/plan": lambda conn, p: _without(portrait.plan(conn), "_ready"),
    "/api/me/status": lambda conn, p: portrait.status(),
    "/api/me/charts": lambda conn, p: portrait.charts(p.get("name", ""), p.get("group") == "1"),
    "/api/me/brain": lambda conn, p: portrait.brain_lines(),
    "/api/me/brain/export": lambda conn, p: portrait.brain_export(),
    "/api/check/plan": lambda conn, p: _without(check.plan(conn, int(p.get("size") or check.SAMPLE)), "_items"),
    "/api/check/status": lambda conn, p: check.status(),
    "/api/check/summary": lambda conn, p: check.summary(),
    "/api/histories": lambda conn, p: histories.overview(),
    "/api/histories/plan": lambda conn, p: _without(histories.plan(), "_ready"),
    "/api/histories/status": lambda conn, p: histories.status(),
    "/api/history": lambda conn, p: histories.read(p.get("name", ""), p.get("group") == "1"),
    "/api/digest": lambda conn, p: histories.digest_by_number(p.get("id", "")),
    "/api/full/plan": lambda conn, p: _full_plan(conn),
    "/api/full/status": lambda conn, p: fullread.status(),
    "/api/full/people": lambda conn, p: {"people": fullread.digest_people()},
    "/api/full/digests": lambda conn, p: fullread.digests_for(p.get("name", ""), p.get("group") == "1", int(p.get("offset") or 0),
                                                             newest_first=p.get("newest") == "1"),
    "/api/reading/queue": lambda conn, p: reading.review_queue(),
    "/api/reading/unit": lambda conn, p: reading.unit_for_review(conn, p.get("id", "")),
}


def _home(conn) -> dict:
    """Everything the opening screen shows, in one request."""
    ranked = insights.importance(conn)
    hidden = junk.report(conn)
    return {
        "totals": stats.people(conn, Filters())["totals"],
        "inner_circle": [r for r in ranked["rows"] if r["pinned"]],
        "suggested": [r for r in ranked["rows"] if not r["pinned"]][:8],
        "people_scored": ranked["people_scored"],
        "community": insights.community(conn),
        "drift": insights.drift(conn, limit=6),
        "import": index.last_report(conn),
        "hidden": {"senders": len(hidden["hidden"]), "messages": hidden["hidden_messages"], "suggested": len(hidden["suggested"])},
    }


def _relabelled() -> None:
    """After a label changes: re-mark who is hidden, and forget cached answers worked out before."""
    junk.refresh()
    stats._cache.clear()


def _save_label(conn, body) -> dict:
    labels.save(str(body.get("name") or ""), str(body.get("label") or ""), bool(body.get("pinned")))
    _relabelled()
    return {"labels": labels.load()}


def _junk_change(conn, body) -> dict:
    """Hide or restore senders from the Hidden senders screen."""
    names = [str(name) for name in (body.get("names") or [])][:5000]
    if not names:
        raise QueryError("Choose a sender first.")
    action = labels.hide if body.get("action") == "hide" else labels.keep if body.get("action") == "keep" else None
    if action is None:
        raise QueryError("Unknown request.")
    for name in names:
        action(name)
    _relabelled()
    return {"changed": len(names)}


def _ai_args(body: dict) -> tuple:
    return str(body.get("q") or ""), Filters.from_dict(body.get("filters") or {}), str(body.get("strategy") or "recent")


def _ai_ask(conn, body) -> dict:
    # The confirm flag is set only by the button on the preview panel.
    if body.get("confirm") is not True:
        raise QueryError("Confirm the preview before sending messages to the AI.")
    q, filters, strategy = _ai_args(body)
    return ai.ask(conn, str(body.get("question") or ""), q, filters, strategy)


def _ai_topics(conn, body) -> dict:
    if body.get("confirm") is not True:
        raise QueryError("Confirm the preview before sending messages to the AI.")
    q, filters, strategy = _ai_args(body)
    return ai.discover_topics(conn, q, filters, strategy)


def _reading_args(p: dict) -> tuple:
    people = p.get("people") or []
    if isinstance(people, str):
        people = [name for name in people.split("|") if name]
    return [str(name) for name in people], str(p.get("year") or ""), int(p.get("per_person") or reading.UNITS_PER_PERSON)


def _reading_plan(conn, p) -> dict:
    planned = reading.plan(conn, *_reading_args(p))
    planned.pop("_units")            # transcripts stay on the server; the page only needs the counts
    planned["key"] = claude_api.status()
    return planned


def _reading_start(conn, body) -> dict:
    # The confirm flag is set only by the button under the preview of what would be sent.
    if body.get("confirm") is not True:
        raise QueryError("Confirm the preview before sending conversations to the AI.")
    return reading.start(conn, *_reading_args(body))


def _without(planned: dict, key: str) -> dict:
    """A plan as the page sees it: the counts, without the text that would be sent."""
    planned.pop(key, None)
    planned["key"] = claude_api.status()
    return planned


def _confirmed(body: dict) -> None:
    # The confirm flag is set only by the button under the preview of what would be sent.
    if body.get("confirm") is not True:
        raise QueryError("Confirm the preview before sending anything to the AI.")


def _portrait_start(conn, body) -> dict:
    _confirmed(body)
    return portrait.start(conn)


def _save_brain_line(conn, body) -> dict:
    return portrait.save_brain_line(int(body.get("id") or 0), body.get("text") if isinstance(body.get("text"), str) else None,
                                    body.get("confirmed") if isinstance(body.get("confirmed"), bool) else None)


def _check_start(conn, body) -> dict:
    _confirmed(body)
    return check.start(conn, int(body.get("size") or check.SAMPLE))


def _histories_start(conn, body) -> dict:
    _confirmed(body)
    return histories.start()


def _full_plan(conn) -> dict:
    planned = fullread.plan(conn)
    planned.pop("_pending")          # transcripts stay on the server; the page only needs the counts
    planned["key"] = claude_api.status()
    return planned


def _full_start(conn, body) -> dict:
    # The confirm flag is set only by the button under the preview of what would be sent.
    if body.get("confirm") is not True:
        raise QueryError("Confirm the preview before sending conversations to the AI.")
    return fullread.start(conn)


def _save_key(conn, body) -> dict:
    """Store the API key in the Keychain, then ask Anthropic (sending no messages) which models it may use."""
    claude_api.save_key(str(body.get("key") or ""))
    return {"saved": True, "models": claude_api.check_models(reading.MODELS)}


POST_ROUTES: Dict[str, Callable] = {
    "/api/junk/change": _junk_change,
    "/api/me/start": _portrait_start,
    "/api/me/stop": lambda conn, b: portrait.stop(),
    "/api/me/forget": lambda conn, b: portrait.forget(bool(b.get("everything"))),
    "/api/me/brain/save": _save_brain_line,
    "/api/check/start": _check_start,
    "/api/check/stop": lambda conn, b: check.stop(),
    "/api/histories/start": _histories_start,
    "/api/histories/stop": lambda conn, b: histories.stop(),
    "/api/histories/people": lambda conn, b: {"chosen": histories.set_people(list(b.get("names") or []))},
    "/api/histories/forget": lambda conn, b: histories.forget(str(b.get("name") or ""), bool(b.get("group"))),
    "/api/full/start": _full_start,
    "/api/full/stop": lambda conn, b: fullread.stop(),
    "/api/full/close-people": lambda conn, b: {"close_people": fullread.set_close_people(list(b.get("names") or []))},
    "/api/key/save": _save_key,
    "/api/key/check": lambda conn, b: {"models": claude_api.check_models(reading.MODELS)},
    "/api/reading/start": _reading_start,
    "/api/reading/stop": lambda conn, b: reading.stop(),
    "/api/reading/review": lambda conn, b: reading.save_review(str(b.get("unit_id") or ""), str(b.get("slot") or ""),
                                                               str(b.get("verdict") or ""), str(b.get("note") or "")),
    "/api/labels/save": _save_label,
    "/api/topics/save": lambda conn, b: {"saved": bool(topics.save(str(b.get("name") or ""), list(b.get("terms") or [])))},
    "/api/topics/delete": lambda conn, b: {"deleted": bool(topics.delete(str(b.get("name") or "")) is not None)},
    "/api/ai/preview": lambda conn, b: ai.preview(conn, *_ai_args(b)),
    "/api/ai/ask": _ai_ask,
    "/api/ai/topics": _ai_topics,
}


def make_server(port: int = 0) -> Tuple[ThreadingHTTPServer, str]:
    """Create the server on 127.0.0.1. Port 0 lets the system pick a free one."""
    token = secrets.token_urlsafe(32)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual = httpd.server_address[1]
    Handler.token = token
    Handler.allowed_hosts = ("127.0.0.1:%d" % actual, "localhost:%d" % actual)
    return httpd, "http://127.0.0.1:%d/" % actual


def serve(port: int = 8765, open_browser: bool = True) -> str:
    """Run until stopped. Returns "restart" if a new version was just installed, otherwise "quit"."""
    try:
        httpd, url = make_server(port)
    except OSError:
        httpd, url = make_server(0)  # the usual port is busy: take any free one
    _running["httpd"], _running["then"] = httpd, None
    print("iMessage Explorer %s is running at %s" % (__version__, url))
    print("Leave this window open while you use it. To stop, use Quit on the Setup screen or close this window.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    for job in (fullread, check, histories, portrait):
        job.resume_if_needed()       # carry on with anything that was under way when the app was last closed
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
        _running["httpd"] = None
    return _running["then"] or "quit"
