"""Shared plumbing for work sent to Anthropic in batches: the accuracy check and the histories.

(The full read has its own, older copy of this in fullread.py.)

A job hands over a list of requests, each with a short id and a note about what
it is for. This module sends them as batches, remembers which batch each is in,
and later hands every result back to the job along with its note. It also keeps
a background runner alive while there is anything to collect.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Callable, Dict, List, Optional

from . import claude_api, reading
from .claude_api import AIError

BATCH_REQUESTS = 200
BATCH_CHARACTERS = 4_000_000
POLL_SECONDS = float(os.environ.get("IMX_POLL_SECONDS", "60"))
SAFETY = 2.0                   # spending is planned as if every request cost this many times its estimate

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_batch (
    id TEXT PRIMARY KEY,               -- Anthropic's id for the batch
    kind TEXT NOT NULL,                -- which job it belongs to
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,              -- 'in_progress' or 'collected'
    requests INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS job_item (
    custom_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    batch_id TEXT,                     -- the batch it is in now; empty once collected
    note TEXT NOT NULL,                -- what this request is for, as JSON, handed back with its result
    estimate_usd REAL NOT NULL,
    attempts INTEGER NOT NULL
);
"""


def store() -> sqlite3.Connection:
    conn = reading._store()
    conn.executescript(SCHEMA)
    return conn


def spent(purpose: str) -> float:
    return reading.spent((purpose,))


def in_flight(kind: str) -> int:
    with reading._store_lock:
        conn = store()
        try:
            return conn.execute("SELECT COUNT(*) FROM job_batch WHERE kind = ? AND status = 'in_progress'", (kind,)).fetchone()[0]
        finally:
            conn.close()


def items_out(kind: str) -> int:
    with reading._store_lock:
        conn = store()
        try:
            return conn.execute("SELECT COUNT(*) FROM job_item WHERE kind = ? AND batch_id IS NOT NULL AND batch_id != ''", (kind,)).fetchone()[0]
        finally:
            conn.close()


def outstanding(kind: str) -> float:
    """The estimated cost of everything of this kind that Anthropic is still working on."""
    with reading._store_lock:
        conn = store()
        try:
            return float(conn.execute("SELECT COALESCE(SUM(estimate_usd), 0) FROM job_item WHERE kind = ? AND batch_id IS NOT NULL "
                                      "AND batch_id != ''", (kind,)).fetchone()[0])
        finally:
            conn.close()


def attempts(kind: str) -> Dict[str, int]:
    with reading._store_lock:
        conn = store()
        try:
            return {row[0]: row[1] for row in conn.execute("SELECT custom_id, attempts FROM job_item WHERE kind = ?", (kind,))}
        finally:
            conn.close()


def fits(kind: str, purpose: str, cap_usd: float, items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """The leading items that fit under the cap, planning for each to cost SAFETY times its estimate."""
    headroom = cap_usd - spent(purpose) - SAFETY * outstanding(kind)
    chosen = []
    for item in items:
        cost = SAFETY * float(item["estimate_usd"])
        if cost > headroom:
            break
        headroom -= cost
        chosen.append(item)
    return chosen


def submit(kind: str, items: List[Dict[str, object]], create: Callable = claude_api.batch_create,
           say: Callable[[str], None] = lambda _text: None, call_off: Callable = claude_api.batch_cancel) -> int:
    """Send items as batches. Each item has `custom_id`, `params`, `note` (a dict) and `estimate_usd`.

    A batch that was sent but could not be written down is called off and the job stops. Otherwise the
    next round would send the same requests again, and the cap, which counts what is written down,
    would not see either copy.
    """
    waiting = list(items)
    sent = 0
    while waiting:
        chunk: List[Dict[str, object]] = []
        size = 0
        while waiting and len(chunk) < BATCH_REQUESTS:
            weight = len(json.dumps(waiting[0]["params"], ensure_ascii=False))
            if chunk and size + weight > BATCH_CHARACTERS:
                break
            size += weight
            chunk.append(waiting.pop(0))
        say("Uploading: %d of %d sent so far" % (sent, len(items)))
        batch = create([{"custom_id": item["custom_id"], "params": item["params"]} for item in chunk])
        batch_id = str(batch.get("id") or "")
        if not batch_id:
            raise AIError("Anthropic accepted a batch but gave it no id.")
        try:
            with reading._store_lock:
                conn = store()
                try:
                    conn.execute("INSERT INTO job_batch (id, kind, created_at, status, requests) VALUES (?, ?, ?, 'in_progress', ?)",
                                 (batch_id, kind, reading._now(), len(chunk)))
                    for item in chunk:
                        conn.execute("INSERT INTO job_item (custom_id, kind, batch_id, note, estimate_usd, attempts) VALUES (?, ?, ?, ?, ?, 1) "
                                     "ON CONFLICT (custom_id) DO UPDATE SET batch_id = excluded.batch_id, note = excluded.note, "
                                     "estimate_usd = excluded.estimate_usd, attempts = attempts + 1",
                                     (item["custom_id"], kind, batch_id, json.dumps(item["note"], ensure_ascii=False), item["estimate_usd"]))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            called_off = True
            try:
                call_off(batch_id)
            except Exception:
                called_off = False
            raise AIError("A batch of %d requests was sent but could not be written down on this Mac (%s). %s Nothing more was sent. "
                          "Check that the disk is not full and that only one copy of the app is open, then start again."
                          % (len(chunk), exc, "It was called off, so at most a small part of it is charged." if called_off
                             else "It could not be called off either, so it may be charged: batch %s." % batch_id), retryable=False)
        sent += len(chunk)
    return sent


def charge(purpose: str, model: str, custom_id: str, reply: Dict[str, object], messages: int = 0, characters: int = 0) -> None:
    """Write down what one reply cost."""
    reading._write("INSERT INTO ledger (at, model, unit_id, messages, input_tokens, output_tokens, cost_usd, characters, purpose) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (reading._now(), model, custom_id, messages, reply["usage"]["input_tokens"], reply["usage"]["output_tokens"],
                    reply["cost_usd"], characters, purpose))


def problem_text(result: dict) -> str:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    inner = error.get("error") if isinstance(error.get("error"), dict) else error
    return str(inner.get("message") or inner.get("type") or result.get("type") or "no detail given")[:300]


def collect(kind: str, handle: Callable[[str, dict, Optional[Dict[str, object]], str], None],
            get: Callable = claude_api.batch_get, results: Callable = claude_api.batch_results) -> int:
    """Collect every finished batch of this kind.

    For each result, `handle(custom_id, note, reply, problem)` is called: `reply` is the model's reply
    (text, stop_reason, model, usage, cost_usd at the batch price), or None with `problem` saying why.
    """
    with reading._store_lock:
        conn = store()
        try:
            waiting = [row[0] for row in conn.execute("SELECT id FROM job_batch WHERE kind = ? AND status = 'in_progress' ORDER BY created_at", (kind,))]
        finally:
            conn.close()
    collected = 0
    for batch_id in waiting:
        if str(get(batch_id).get("processing_status")) != "ended":
            continue
        lines = results(batch_id)
        with reading._store_lock:
            conn = store()
            try:
                notes = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT custom_id, note FROM job_item WHERE batch_id = ?", (batch_id,))}
            finally:
                conn.close()
        for line in lines:
            custom_id = str(line.get("custom_id") or "")
            if custom_id not in notes:
                continue
            result = line.get("result") if isinstance(line.get("result"), dict) else {}
            if result.get("type") == "succeeded" and isinstance(result.get("message"), dict):
                asked = str(notes[custom_id].get("model") or "")
                handle(custom_id, notes[custom_id], claude_api.read_message(result["message"], asked, claude_api.BATCH_PRICE_FACTOR), "")
            else:
                if result.get("type") != "errored":
                    # Cancelled or out of time: not counted as a try.
                    reading._write("UPDATE job_item SET attempts = attempts - 1 WHERE custom_id = ?", (custom_id,))
                handle(custom_id, notes[custom_id], None, problem_text(result))
        with reading._store_lock:
            conn = store()
            try:
                conn.execute("UPDATE job_item SET batch_id = NULL WHERE batch_id = ?", (batch_id,))
                conn.execute("UPDATE job_batch SET status = 'collected' WHERE id = ?", (batch_id,))
                conn.commit()
            finally:
                conn.close()
        collected += 1
    return collected


def cancel(kind: str, cancel_batch: Callable = claude_api.batch_cancel) -> None:
    with reading._store_lock:
        conn = store()
        try:
            waiting = [row[0] for row in conn.execute("SELECT id FROM job_batch WHERE kind = ? AND status = 'in_progress'", (kind,))]
        finally:
            conn.close()
    for batch_id in waiting:
        try:
            cancel_batch(batch_id)
        except AIError:
            pass


class Runner:
    """Keeps one job moving in the background while the app is open.

    `step()` does one round and returns True while there is more to do.
    """

    def __init__(self, step: Callable[[], bool], poll_seconds: Optional[float] = None,
                 on_refusal: Optional[Callable[[str], bool]] = None):
        self._step = step
        # Called when Anthropic refuses in a way waiting will not fix. Returns True if the runner should keep going.
        self.on_refusal = on_refusal
        self.poll_seconds = POLL_SECONDS if poll_seconds is None else poll_seconds
        self.wake = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.problem = ""
        self.stage = ""
        self.lock = threading.Lock()
        self.one_at_a_time = threading.Lock()

    def say(self, text: str) -> None:
        self.stage = text

    def step(self) -> bool:
        with self.one_at_a_time:
            return self._step()

    def ensure_running(self) -> None:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        self.wake.set()

    def _loop(self) -> None:
        pause = self.poll_seconds
        while True:
            try:
                more = self.step()
                self.problem = ""
                pause = self.poll_seconds
            except AIError as exc:
                self.problem = str(exc)
                more = True if exc.retryable or self.on_refusal is None else self.on_refusal(str(exc))
                pause = min(max(pause, self.poll_seconds) * 2, 900)
            except Exception as exc:                 # never let the runner die silently
                self.problem = "Unexpected problem: %s" % exc
                more = True
                pause = min(max(pause, self.poll_seconds) * 2, 900)
            if not more:
                self.stage = ""
                return                               # any problem text stays, so the screen can show why it stopped
            self.wake.clear()
            self.wake.wait(pause)
