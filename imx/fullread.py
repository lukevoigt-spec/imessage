"""The full read: a digest of every conversation in your history.

The pilot read a sample to choose a model. This reads everything, with the
choice you made: one model for most conversations, and a stronger one for the
people you name.

How it works:

1. Every conversation, one-to-one and group, is cut into reading units by the
   same rules as the pilot. Automated senders are left out. Exchanges too short
   to say anything (under 5 messages and under 40 words) are left out too.
2. Units are sent to Anthropic in batches. A batch is processed on Anthropic's
   side at half the usual price, usually within an hour and at most within a
   day. The app does not need to stay open while a batch is being processed;
   it needs to be open to send batches and to collect their results.
3. Results are collected, each digest is stored, and the next wave is sent,
   until everything has been read.

Safeguards:

- A spending cap. Conversations go out in waves sized so that even if every
  conversation still being processed cost twice what the pilot measured, the
  total would stay under the cap. Each wave's true cost is recorded as its
  results arrive, before the next wave is sized.
- Nothing is paid for twice. Digests from the pilot are reused, and a
  conversation already read is never sent again.
- It can be stopped. Batches still being processed are cancelled; anything
  Anthropic had already read is collected and kept, because it was paid for.
- The people who matter most are read first, so a stop or the cap falls on the
  quietest conversations.
- Nothing is lost if the app is closed at a bad moment. If Anthropic accepted
  a batch but the app never heard back (closed, or the connection dropped,
  mid-upload), the app finds that batch at Anthropic the next time it runs and
  takes it back, instead of sending the same conversations again.
"""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from . import activity, claude_api, index, reading
from .claude_api import AIError
from .query import QueryError

FULL_CAP_USD = 150.00                  # the most the full read may spend, agreed with the owner
DEFAULT_MODEL = "claude-sonnet-5-5"    # reads everything not named below
CLOSE_MODEL = "claude-opus-5-5"        # reads the people you name
SAFETY = 2.0                           # waves are sized as if each conversation cost this many times the estimate
BATCH_REQUESTS = 500                   # conversations per batch
BATCH_CHARACTERS = 4_000_000           # and a size limit per batch: uploads of a few megabytes, so each takes seconds
MAX_ATTEMPTS = 2                       # how many times a conversation is sent before it is listed as not read
POLL_SECONDS = float(os.environ.get("IMX_POLL_SECONDS", "60"))    # how often batches are checked; tests shorten it

# Quiet conversations: when a year holds few messages, exchanges up to this far apart are read together.
SPARSE_YEAR_MESSAGES = 200
SPARSE_JOIN_SECONDS = 45 * 86400
# Too little to digest.
MIN_MESSAGES = 5
MIN_WORDS = 40

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch (
    id TEXT PRIMARY KEY,               -- Anthropic's id for the batch
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,              -- 'in_progress' or 'collected'
    requests INTEGER NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0,
    errored INTEGER NOT NULL DEFAULT 0,
    not_processed INTEGER NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0,
    collected_at TEXT
);
-- One row per conversation that has been sent at least once.
CREATE TABLE IF NOT EXISTS sent (
    unit_id TEXT NOT NULL,
    model TEXT NOT NULL,
    batch_id TEXT,                     -- the batch it is in now; empty once that batch has been collected
    attempts INTEGER NOT NULL,
    estimate_usd REAL NOT NULL,
    PRIMARY KEY (unit_id, model)
);
"""


def _store() -> sqlite3.Connection:
    conn = reading._store()
    conn.executescript(STORE_SCHEMA)
    return conn


def _setting(key: str, default: str = "") -> str:
    with reading._store_lock:
        conn = _store()
        try:
            row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default
        finally:
            conn.close()


def _set(key: str, value: str) -> None:
    with reading._store_lock:
        conn = _store()
        try:
            conn.execute("INSERT OR REPLACE INTO setting VALUES (?, ?)", (key, value))
            conn.commit()
        finally:
            conn.close()


# ---- who is read by the stronger model ------------------------------------------------------------------

def close_people() -> List[str]:
    try:
        return [str(name) for name in json.loads(_setting("close_people", "[]"))]
    except ValueError:
        return []


def set_close_people(names: List[str]) -> List[str]:
    clean = sorted({str(name).strip() for name in names if str(name).strip()})[:50]
    _set("close_people", json.dumps(clean, ensure_ascii=False))
    return clean


def model_for(unit: Dict[str, object], close: Optional[set] = None) -> str:
    close = set(close_people()) if close is None else close
    return CLOSE_MODEL if (not unit["is_group"] and unit["counterpart"] in close) else DEFAULT_MODEL


# ---- cutting the whole history into units -------------------------------------------------------------------

def all_units(conn: sqlite3.Connection) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    """Every reading unit in the history, and a count of what was left out as too short."""
    rows = conn.execute(
        "SELECT counterpart, is_group, year, %s FROM messages WHERE junk = 0 "
        "AND NOT (kind = 'reaction' AND react_type >= 3000) ORDER BY counterpart, is_group, year, ts, id" % reading.MESSAGE_COLUMNS)
    units: List[Dict[str, object]] = []
    skipped = {"units": 0, "messages": 0}
    for (counterpart, is_group, _year), group in itertools.groupby(rows, key=lambda r: (r["counterpart"], r["is_group"], r["year"])):
        messages = list(group)
        join = SPARSE_JOIN_SECONDS if len(messages) < SPARSE_YEAR_MESSAGES else reading.JOIN_WITHIN_SECONDS
        for unit in reading.cut_into_units(messages, counterpart, bool(is_group), join):
            if unit["messages"] < MIN_MESSAGES and unit["words"] < MIN_WORDS:
                skipped["units"] += 1
                skipped["messages"] += unit["messages"]
            else:
                units.append(unit)
    return units, skipped


# ---- estimating cost from what the pilot measured -----------------------------------------------------------

def rates() -> Dict[str, Tuple[float, float, float]]:
    """For each model: input tokens per character, fixed input tokens per request, and output tokens per request.

    Worked out from replies already paid for (a straight-line fit of input tokens against conversation
    size), so the estimate rests on your own messages. Falls back to round figures before any exist.
    """
    found: Dict[str, Tuple[float, float, float]] = {}
    with reading._store_lock:
        conn = _store()
        try:
            for model in (DEFAULT_MODEL, CLOSE_MODEL):
                rows = conn.execute("SELECT characters, input_tokens, output_tokens FROM ledger WHERE model = ? AND characters > 0 "
                                    "AND purpose IN ('digest', 'full')", (model,)).fetchall()
                if len(rows) < 10:
                    found[model] = (1 / reading.CHARS_PER_TOKEN, float(reading.INSTRUCTION_TOKENS), float(reading.OUTPUT_TOKENS_GUESS))
                    continue
                n = float(len(rows))
                sum_x, sum_y = sum(r[0] for r in rows), sum(r[1] for r in rows)
                sum_xx, sum_xy = sum(r[0] * r[0] for r in rows), sum(r[0] * r[1] for r in rows)
                spread = n * sum_xx - sum_x * sum_x
                slope = (n * sum_xy - sum_x * sum_y) / spread if spread else sum_y / max(1.0, sum_x)
                fixed = max(0.0, (sum_y - slope * sum_x) / n)
                found[model] = (max(0.0, slope), fixed, sum(r[2] for r in rows) / n)
        finally:
            conn.close()
    return found


def estimate(unit: Dict[str, object], model: str, known: Dict[str, Tuple[float, float, float]]) -> float:
    """What reading one unit is expected to cost at the batch price."""
    per_character, fixed, output = known[model]
    price_in, price_out = claude_api.PRICES[model]
    return ((unit["characters"] * per_character + fixed) * price_in + output * price_out) / 1_000_000.0 * claude_api.BATCH_PRICE_FACTOR


# ---- where everything stands ---------------------------------------------------------------------------------

def _state() -> Tuple[set, Dict[Tuple[str, str], sqlite3.Row]]:
    """Which (unit, model) pairs have a digest, and the record of everything sent."""
    with reading._store_lock:
        conn = _store()
        try:
            done = {(row[0], row[1]) for row in conn.execute("SELECT unit_id, model FROM digest WHERE status IN ('ok', 'declined')")}
            sent = {(row["unit_id"], row["model"]): row for row in conn.execute("SELECT * FROM sent")}
        finally:
            conn.close()
    return done, sent


def spent() -> float:
    return reading.spent(("full",))


def plan(conn: sqlite3.Connection) -> Dict[str, object]:
    """Everything the full read would do, without sending anything."""
    units, skipped = all_units(conn)
    close = set(close_people())
    known = rates()
    done, sent = _state()
    volume: Dict[Tuple[str, bool], int] = {}
    for unit in units:
        key = (unit["counterpart"], unit["is_group"])
        volume[key] = volume.get(key, 0) + unit["messages"]
    models = {model: {"model": model, "name": claude_api.MODEL_NAMES[model], "units": 0, "messages": 0, "already_read": 0,
                      "being_read": 0, "to_send": 0, "not_read": 0, "estimated_cost_usd": 0.0} for model in (DEFAULT_MODEL, CLOSE_MODEL)}
    pending: List[Dict[str, object]] = []
    for unit in units:
        model = model_for(unit, close)
        row = models[model]
        row["units"] += 1
        row["messages"] += unit["messages"]
        record = sent.get((unit["id"], model))
        if (unit["id"], model) in done:
            row["already_read"] += 1
        elif record is not None and record["batch_id"]:
            row["being_read"] += 1
        elif record is not None and record["attempts"] >= MAX_ATTEMPTS:
            row["not_read"] += 1
        else:
            cost = estimate(unit, model, known)
            row["to_send"] += 1
            row["estimated_cost_usd"] += cost
            # Closest people first, then the busiest conversations, each in date order.
            pending.append(dict(unit, model=model, estimate_usd=cost,
                                order=(0 if model == CLOSE_MODEL else 1, -volume[(unit["counterpart"], unit["is_group"])],
                                       unit["counterpart"], unit["first_ts"])))
    pending.sort(key=lambda unit: unit["order"])
    for row in models.values():
        row["estimated_cost_usd"] = round(row["estimated_cost_usd"], 2)
    already = spent()
    people = {key for key in volume if not key[1]}
    return {
        "conversations": len(volume), "people": len(people), "groups": len(volume) - len(people),
        "units": len(units), "messages": sum(unit["messages"] for unit in units),
        "first_day": datetime.fromtimestamp(min(u["first_ts"] for u in units)).strftime("%Y-%m-%d") if units else None,
        "last_day": datetime.fromtimestamp(max(u["last_ts"] for u in units)).strftime("%Y-%m-%d") if units else None,
        "too_short": skipped,
        "models": [models[DEFAULT_MODEL], models[CLOSE_MODEL]],
        "close_people": sorted(close),
        "close_people_found": sorted(name for name in close if (name, False) in volume),
        "to_send": len(pending),
        "estimated_cost_usd": round(sum(unit["estimate_usd"] for unit in pending), 2),
        "cap_usd": FULL_CAP_USD, "spent_usd": round(already, 2), "remaining_usd": round(max(0.0, FULL_CAP_USD - already), 2),
        "estimate_from_your_messages": all(len_ok for len_ok in _measured().values()),
        "_pending": pending,
    }


def _measured() -> Dict[str, bool]:
    """Whether each model's estimate rests on replies already paid for."""
    with reading._store_lock:
        conn = _store()
        try:
            return {model: conn.execute("SELECT COUNT(*) FROM ledger WHERE model = ? AND characters > 0 AND purpose IN ('digest', 'full')",
                                        (model,)).fetchone()[0] >= 10 for model in (DEFAULT_MODEL, CLOSE_MODEL)}
        finally:
            conn.close()


# ---- sending a wave -----------------------------------------------------------------------------------------

def _outstanding() -> float:
    """The estimated cost of everything Anthropic is still processing."""
    with reading._store_lock:
        conn = _store()
        try:
            return float(conn.execute("SELECT COALESCE(SUM(estimate_usd), 0) FROM sent WHERE batch_id IS NOT NULL AND batch_id != ''").fetchone()[0])
        finally:
            conn.close()


def send_wave(conn: sqlite3.Connection, create: Callable = claude_api.batch_create,
              say: Callable[[str], None] = lambda _text: None) -> Dict[str, object]:
    """Send as many waiting conversations as safely fit under the cap. Returns what was sent."""
    say("Working out what to send")
    planned = plan(conn)
    pending = planned["_pending"]
    headroom = FULL_CAP_USD - spent() - SAFETY * _outstanding()
    wave: List[Dict[str, object]] = []
    for unit in pending:
        cost = SAFETY * unit["estimate_usd"]
        if cost > headroom:
            break                                   # in priority order: never skip ahead to cheaper, less important ones
        headroom -= cost
        wave.append(unit)
    sent_units = batches = 0
    in_wave = len(wave)
    while wave:
        chunk: List[Dict[str, object]] = []
        size = 0
        while wave and len(chunk) < BATCH_REQUESTS and (not chunk or size + wave[0]["characters"] <= BATCH_CHARACTERS):
            size += wave[0]["characters"]
            chunk.append(wave.pop(0))
        requests = [{"custom_id": unit["id"],
                     "params": claude_api.message_body(unit["model"], reading.SYSTEM_PROMPT, reading._request_text(unit),
                                                       schema=reading.DIGEST_SCHEMA, effort=reading.EFFORT,
                                                       max_tokens=reading.MAX_OUTPUT_TOKENS)} for unit in chunk]
        say("Uploading: %s of %s stretches in this wave sent so far" % (format(sent_units, ","), format(in_wave, ",")))
        batch = create(requests)                    # raises AIError if Anthropic will not take it; nothing is recorded then
        batch_id = str(batch.get("id") or "")
        if not batch_id:
            raise AIError("Anthropic accepted a batch but gave it no id.")
        with reading._store_lock:
            store = _store()
            try:
                store.execute("INSERT INTO batch (id, created_at, status, requests) VALUES (?, ?, 'in_progress', ?)",
                              (batch_id, reading._now(), len(chunk)))
                for unit in chunk:
                    store.execute("INSERT OR REPLACE INTO unit (id, counterpart, first_ts, last_ts, messages, characters, is_group) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (unit["id"], unit["counterpart"], unit["first_ts"], unit["last_ts"],
                                                                  unit["messages"], unit["characters"], 1 if unit["is_group"] else 0))
                    store.execute("INSERT INTO sent (unit_id, model, batch_id, attempts, estimate_usd) VALUES (?, ?, ?, 1, ?) "
                                  "ON CONFLICT (unit_id, model) DO UPDATE SET batch_id = excluded.batch_id, attempts = attempts + 1, "
                                  "estimate_usd = excluded.estimate_usd", (unit["id"], unit["model"], batch_id, unit["estimate_usd"]))
                store.commit()
            finally:
                store.close()
        sent_units += len(chunk)
        batches += 1
    return {"sent": sent_units, "batches": batches, "still_waiting": len(pending) - sent_units}


# ---- collecting results ---------------------------------------------------------------------------------------

def _problem_text(result: dict) -> str:
    """Anthropic's own words for why one request in a batch failed."""
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    inner = error.get("error") if isinstance(error.get("error"), dict) else error
    return str(inner.get("message") or inner.get("type") or result.get("type") or "no detail given")[:300]


def _epoch(stamp: str) -> float:
    """Anthropic's timestamp (such as 2026-11-20T09:30:00.123456Z) as seconds since 1970. 0 if unreadable."""
    try:
        text = stamp.strip().replace("Z", "+00:00")
        if "." in text:                                  # Python 3.9 needs at most six digits of fraction
            head, rest = text.split(".", 1)
            digits = "".join(itertools.takewhile(str.isdigit, rest))
            text = head + "." + digits[:6].ljust(6, "0") + rest[len(digits):]
        return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _began() -> float:
    """When this full read started, as seconds since 1970. 0 if it never has."""
    try:
        recorded = float(_setting("started_epoch", "0") or 0)
    except ValueError:
        recorded = 0.0
    if recorded:
        return recorded
    # A read started by an earlier version of the app recorded only the local date and time.
    try:
        return time.mktime(time.strptime(_setting("started_at"), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return 0.0


def adopt_lost_batches(list_batches: Callable = claude_api.batch_list) -> int:
    """Find batches Anthropic accepted that this app has no record of, and take them back.

    That happens when the app is closed, or the connection drops, after Anthropic has taken a
    batch but before the app hears its id. Without this the same conversations would be sent,
    and paid for, a second time. Returns how many were found.
    """
    began = _began()
    if not began:
        return 0
    with reading._store_lock:
        store = _store()
        try:
            known = {row[0] for row in store.execute("SELECT id FROM batch")}
        finally:
            store.close()
    found = 0
    for batch in list_batches():
        batch_id = str(batch.get("id") or "")
        # Only batches made since this full read began: anything older belongs to something else.
        if not batch_id or batch_id in known or _epoch(str(batch.get("created_at") or "")) < began - 600:
            continue
        counts = batch.get("request_counts") if isinstance(batch.get("request_counts"), dict) else {}
        with reading._store_lock:
            store = _store()
            try:
                store.execute("INSERT OR IGNORE INTO batch (id, created_at, status, requests) VALUES (?, ?, 'in_progress', ?)",
                              (batch_id, reading._now(), sum(int(counts.get(k) or 0) for k in counts)))
                store.commit()
            finally:
                store.close()
        found += 1
    return found


def collect(get: Callable = claude_api.batch_get, results: Callable = claude_api.batch_results,
            lookup: Optional[Callable[[], Dict[str, Dict[str, object]]]] = None) -> Dict[str, int]:
    """Check every batch still being processed, and store the results of any that have finished.

    `lookup` returns every unit in the history by id. It is used only for a batch the app took back
    without a record of what was in it.
    """
    with reading._store_lock:
        store = _store()
        try:
            waiting = [row["id"] for row in store.execute("SELECT id FROM batch WHERE status = 'in_progress' ORDER BY created_at")]
        finally:
            store.close()
    collected = digests = 0
    for batch_id in waiting:
        if str(get(batch_id).get("processing_status")) != "ended":
            continue
        lines = results(batch_id)
        # Looked up before the store is locked: both of these read the store themselves.
        with reading._store_lock:
            store = _store()
            try:
                recorded = {row[0] for row in store.execute("SELECT unit_id FROM sent WHERE batch_id = ?", (batch_id,))}
            finally:
                store.close()
        strangers = [str(line.get("custom_id") or "") for line in lines if str(line.get("custom_id") or "") not in recorded]
        everything = lookup() if (strangers and lookup is not None) else {}
        close = set(close_people()) if strangers else set()
        with reading._store_lock:
            store = _store()
            try:
                models = {row["unit_id"]: row["model"] for row in store.execute("SELECT unit_id, model FROM sent WHERE batch_id = ?", (batch_id,))}
                units = {row["id"]: dict(row) for row in store.execute(
                    "SELECT * FROM unit WHERE id IN (SELECT unit_id FROM sent WHERE batch_id = ?)", (batch_id,))}
                if strangers:
                    # A batch taken back without a record of its contents: work out what each result belongs to.
                    for unit_id in strangers:
                        unit = everything.get(unit_id)
                        if unit is None:
                            continue
                        model = model_for(unit, close)
                        models[unit_id] = model
                        units[unit_id] = unit
                        store.execute("INSERT OR REPLACE INTO unit (id, counterpart, first_ts, last_ts, messages, characters, is_group) "
                                      "VALUES (?, ?, ?, ?, ?, ?, ?)", (unit["id"], unit["counterpart"], unit["first_ts"], unit["last_ts"],
                                                                      unit["messages"], unit["characters"], 1 if unit["is_group"] else 0))
                        store.execute("INSERT OR IGNORE INTO sent (unit_id, model, batch_id, attempts, estimate_usd) VALUES (?, ?, ?, 1, 0)",
                                      (unit_id, model, batch_id))
                succeeded = errored = other = 0
                cost = 0.0
                for line in lines:
                    unit_id = str(line.get("custom_id") or "")
                    result = line.get("result") if isinstance(line.get("result"), dict) else {}
                    model = models.get(unit_id)
                    if model is None or unit_id not in units:
                        continue
                    kind = result.get("type")
                    if kind == "succeeded" and isinstance(result.get("message"), dict):
                        reply = claude_api.read_message(result["message"], model, claude_api.BATCH_PRICE_FACTOR)
                        digest = None if reply["stop_reason"] == "refusal" else reading._parse_digest(str(reply["text"]))
                        outcome = "declined" if reply["stop_reason"] == "refusal" else ("ok" if digest is not None else "unreadable")
                        unit = units[unit_id]
                        store.execute("INSERT INTO ledger (at, model, unit_id, messages, input_tokens, output_tokens, cost_usd, "
                                      "characters, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'full')",
                                      (reading._now(), model, unit_id, unit["messages"], reply["usage"]["input_tokens"],
                                       reply["usage"]["output_tokens"], reply["cost_usd"], unit["characters"]))
                        store.execute("INSERT OR REPLACE INTO digest VALUES (?, ?, ?, ?, ?)",
                                      (unit_id, model, outcome, json.dumps(digest, ensure_ascii=False) if digest else "", reading._now()))
                        succeeded += 1
                        digests += 1
                        cost += float(reply["cost_usd"])
                    elif kind == "errored":
                        store.execute("INSERT INTO failure VALUES (?, ?, ?, ?)", (reading._now(), model, 0, "full read: " + _problem_text(result)))
                        errored += 1
                    else:
                        # Cancelled, or the batch ran out of time: not this conversation's fault, so it does not count as a try.
                        store.execute("UPDATE sent SET attempts = attempts - 1 WHERE unit_id = ? AND model = ?", (unit_id, model))
                        other += 1
                # Everything in this batch is no longer being processed, whatever happened to it.
                store.execute("UPDATE sent SET batch_id = NULL WHERE batch_id = ?", (batch_id,))
                store.execute("UPDATE batch SET status = 'collected', succeeded = ?, errored = ?, not_processed = ?, spent_usd = ?, "
                              "collected_at = ? WHERE id = ?", (succeeded, errored, other, cost, reading._now(), batch_id))
                store.commit()
            finally:
                store.close()
        collected += 1
    return {"batches_collected": collected, "digests": digests}


# ---- the background runner ---------------------------------------------------------------------------------------

class Runner:
    """Keeps the full read moving while the app is open: collect what has finished, then send the next wave."""

    def __init__(self, poll_seconds: float = POLL_SECONDS):
        self.poll_seconds = poll_seconds
        self.wake = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.problem = ""
        self.stage = ""                              # what the runner is doing right now, in plain words
        self.last_checked = ""
        self.lock = threading.Lock()
        self.one_at_a_time = threading.Lock()      # a round never overlaps another, whoever asks for it

    def ensure_running(self) -> None:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        self.wake.set()

    def _in_flight(self) -> int:
        with reading._store_lock:
            store = _store()
            try:
                return store.execute("SELECT COUNT(*) FROM batch WHERE status = 'in_progress'").fetchone()[0]
            finally:
                store.close()

    def step(self) -> bool:
        """One round. Returns False when there is nothing left for the runner to do."""
        with self.one_at_a_time:
            return self._step()

    def _say(self, text: str) -> None:
        self.stage = text

    def _everything(self) -> Dict[str, Dict[str, object]]:
        conn = index.connect(readonly=True)
        try:
            return {unit["id"]: unit for unit in all_units(conn)[0]}
        finally:
            conn.close()

    def _step(self) -> bool:
        active = _setting("active") == "1"
        if active and not self._in_flight():
            self._say("Checking with Anthropic for anything already sent")
            adopt_lost_batches()
        if self._in_flight():
            self._say("Collecting results")
            collect(lookup=self._everything)
        self.last_checked = reading._now()
        if active and not self._in_flight():
            if not index.is_ready():
                return False
            conn = index.connect(readonly=True)
            try:
                outcome = send_wave(conn, say=self._say)
            finally:
                conn.close()
            if outcome["sent"] == 0:
                # Nothing could be sent: either everything is read, or what is left does not fit under the cap.
                # "Why it ended" is written before "no longer active", so the page never sees one without the other.
                _set("finished_at", reading._now())
                _set("ended", "finished" if outcome["still_waiting"] == 0 else "stopped at the cap")
                _set("active", "0")
                self._say("")
                return False
        self._say("Waiting for Anthropic to finish reading" if self._in_flight() else "")
        return active or self._in_flight() > 0

    def _loop(self) -> None:
        pause = self.poll_seconds
        while True:
            try:
                more = self.step()
                self.problem = ""
                pause = self.poll_seconds
            except AIError as exc:
                self.problem = str(exc)
                more = True
                if not exc.retryable and self._in_flight() == 0:
                    # Anthropic refused in a way waiting will not fix, and nothing is out being read: stop.
                    # (While batches are still out, the refusal may just mean the queue is full, so the
                    # app waits for them and tries again.)
                    _set("ended", "stopped by a problem")
                    _set("active", "0")
                    more = False
                pause = min(max(pause, self.poll_seconds) * 2, 900)
            except Exception as exc:                 # never let the runner die silently
                self.problem = "Unexpected problem: %s" % exc
                more = True
                pause = min(max(pause, self.poll_seconds) * 2, 900)
            if not more:
                return
            self.wake.clear()
            self.wake.wait(pause)


runner = Runner()


def resume_if_needed() -> None:
    """Called when the app starts: carry on with a full read that was under way."""
    try:
        if _setting("active") == "1" or runner._in_flight():
            runner.ensure_running()
    except sqlite3.Error:
        pass


def start(conn: sqlite3.Connection) -> Dict[str, object]:
    """Begin, or continue, the full read. Call only after the user confirmed the plan."""
    if claude_api.api_key() is None:
        raise QueryError("Add your Anthropic API key first.")
    planned = plan(conn)
    planned.pop("_pending")
    if planned["to_send"] == 0:
        raise QueryError("Everything has already been read, or is being read now.")
    if planned["remaining_usd"] <= 0:
        raise QueryError("The full read's spending cap of $%.2f has been reached." % FULL_CAP_USD)
    activity.record("full_read", messages_sent=planned["messages"], conversations=["%d conversations" % planned["conversations"]],
                    model="%s; %s for %s" % (DEFAULT_MODEL, CLOSE_MODEL, ", ".join(planned["close_people_found"]) or "nobody"))
    _set("target", str(status()["read"] + status()["being_read"] + planned["to_send"]))
    _set("started_at", _setting("started_at") or reading._now())
    _set("started_epoch", _setting("started_epoch") or str(time.time()))
    _set("ended", "")
    _set("active", "1")
    runner.ensure_running()
    return {"started": True, "plan": planned}


def stop(cancel: Callable = claude_api.batch_cancel) -> Dict[str, object]:
    """Stop sending. Batches being processed are cancelled; what Anthropic already read is still collected."""
    _set("ended", "stopped by you")
    _set("active", "0")
    with reading._store_lock:
        store = _store()
        try:
            waiting = [row["id"] for row in store.execute("SELECT id FROM batch WHERE status = 'in_progress'")]
        finally:
            store.close()
    for batch_id in waiting:
        try:
            cancel(batch_id)
        except AIError:
            pass                                     # it may have just finished; collecting handles either case
    runner.ensure_running()
    return status()


def status() -> Dict[str, object]:
    """Quick to work out: read from the record of what has been sent and collected."""
    with reading._store_lock:
        store = _store()
        try:
            batches = [dict(row) for row in store.execute(
                "SELECT id, created_at, status, requests, succeeded, errored, not_processed, spent_usd, collected_at FROM batch ORDER BY created_at")]
            being_read = store.execute("SELECT COUNT(*) FROM sent WHERE batch_id IS NOT NULL AND batch_id != ''").fetchone()[0]
            read = store.execute("SELECT COUNT(*), COALESCE(SUM(messages), 0) FROM ledger WHERE purpose = 'full'").fetchone()
            not_read = store.execute("SELECT COUNT(*) FROM sent s WHERE (batch_id IS NULL OR batch_id = '') AND attempts >= ? AND NOT EXISTS "
                                     "(SELECT 1 FROM digest d WHERE d.unit_id = s.unit_id AND d.model = s.model AND d.status IN ('ok', 'declined'))",
                                     (MAX_ATTEMPTS,)).fetchone()[0]
            declined = store.execute("SELECT COUNT(*) FROM digest d JOIN sent s ON s.unit_id = d.unit_id AND s.model = d.model "
                                     "WHERE d.status = 'declined'").fetchone()[0]
            problems = [dict(row) for row in store.execute(
                "SELECT problem, COUNT(*) AS times FROM failure WHERE problem LIKE 'full read:%' GROUP BY problem ORDER BY times DESC LIMIT 5")]
        finally:
            store.close()
    for batch in batches:
        batch["id"] = batch["id"][-8:]               # enough to tell them apart on screen
    return {
        "active": _setting("active") == "1",
        "ended": _setting("ended"),
        "started_at": _setting("started_at"), "finished_at": _setting("finished_at"),
        "batches": batches,
        "being_read": being_read, "read": read[0], "messages_read": read[1], "not_read": not_read, "declined": declined,
        "spent_usd": round(spent(), 2), "cap_usd": FULL_CAP_USD, "target": int(_setting("target", "0") or 0),
        "problem": runner.problem, "stage": runner.stage, "problems": problems, "last_checked": runner.last_checked,
        "key": claude_api.status(),
    }


# ---- reading the digests back ---------------------------------------------------------------------------------------

_PREFERENCE = {CLOSE_MODEL: 0, DEFAULT_MODEL: 1}


def digest_people() -> List[Dict[str, object]]:
    """Everyone with at least one digest, busiest first."""
    with reading._store_lock:
        store = _store()
        try:
            rows = store.execute(
                "SELECT u.counterpart, u.is_group, COUNT(DISTINCT u.id) AS units, SUM(u.messages) AS messages, MIN(u.first_ts) AS first_ts, "
                "MAX(u.last_ts) AS last_ts FROM unit u WHERE EXISTS (SELECT 1 FROM digest d WHERE d.unit_id = u.id AND d.status = 'ok') "
                "GROUP BY u.counterpart, u.is_group ORDER BY messages DESC").fetchall()
        finally:
            store.close()
    return [{"name": row["counterpart"], "is_group": bool(row["is_group"]), "digests": row["units"], "messages": row["messages"],
             "first_day": datetime.fromtimestamp(row["first_ts"]).strftime("%Y-%m-%d"),
             "last_day": datetime.fromtimestamp(row["last_ts"]).strftime("%Y-%m-%d")} for row in rows]


def digests_for(name: str, is_group: bool = False, offset: int = 0, limit: int = 40, newest_first: bool = False) -> Dict[str, object]:
    """One person's digests in date order: the story of the conversation, stretch by stretch."""
    with reading._store_lock:
        store = _store()
        try:
            units = store.execute("SELECT * FROM unit u WHERE counterpart = ? AND is_group = ? AND EXISTS "
                                  "(SELECT 1 FROM digest d WHERE d.unit_id = u.id AND d.status = 'ok') ORDER BY first_ts %s"
                                  % ("DESC" if newest_first else "ASC"), (name, 1 if is_group else 0)).fetchall()
            page = units[max(0, int(offset)):max(0, int(offset)) + max(1, min(int(limit), 200))]
            rows = []
            for unit in page:
                digests = store.execute("SELECT model, body FROM digest WHERE unit_id = ? AND status = 'ok'", (unit["id"],)).fetchall()
                # Where more than one model read it (the pilot), show the strongest model's reading.
                best = sorted(digests, key=lambda d: _PREFERENCE.get(d["model"], 2))[0]
                rows.append({"unit_id": unit["id"], "messages": unit["messages"],
                             "first_day": datetime.fromtimestamp(unit["first_ts"]).strftime("%Y-%m-%d"),
                             "last_day": datetime.fromtimestamp(unit["last_ts"]).strftime("%Y-%m-%d"),
                             "read_by": claude_api.MODEL_NAMES.get(best["model"], best["model"]), "digest": json.loads(best["body"])})
        finally:
            store.close()
    return {"name": name, "is_group": bool(is_group), "total": len(units), "offset": int(offset), "digests": rows}
