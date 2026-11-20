"""Deeper analysis: tone, relationships, community and language.

As with the basic statistics, every result carries a plain-language definition
of exactly what was measured. Nothing here uses AI; it is arithmetic over the
measurements stored at import (see features.py).

"Now" always means the most recent day in your local database, not today's
date, so results do not shift just because time passed since the last import.
"""

from __future__ import annotations

import math
import re
import sqlite3
from array import array
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import features, labels, stats
from .features import NEGATIVE_AT, POSITIVE_AT, URL_RE
from .query import Filters, QueryError
from .stats import BUCKET_COLUMNS, CONVERSATION_GAP_HOURS, STOPWORDS, _cached

MIN_TONE_MESSAGES = 50        # people with fewer written messages are left out of tone rankings
MIN_IMPORTANCE_MESSAGES = 30  # people with fewer messages are not scored for importance
RECENCY_HALF_LIFE_DAYS = 90   # the recency part halves for every 90 days of silence
# How much each part counts toward the importance score. Volume counts half, because how much you
# text someone says more about logistics than about closeness.
IMPORTANCE_WEIGHTS = {"volume": 0.5, "consistency": 1.0, "recency": 1.0, "two_sided": 1.0,
                      "warmth": 1.0, "depth": 1.0}
LONG_MESSAGE_WORDS = 20       # a message this long or longer counts as "long"
DRIFT_WINDOW_DAYS = 180       # "recent" versus "before" comparison window
QUIET_AFTER_DAYS = 180        # no messages for this long means "gone quiet"
QUIET_MIN_MESSAGES = 200      # ...but only for people you had at least this much history with
STEADY_MONTHS = 6             # a steady contact has messages in at least 6 of the last 12 months
MAX_PHRASE_WORDS = 8          # longest repeated phrase looked for
SIGNATURE_BACKGROUND_SAMPLE = 60000   # other people's messages compared against, per direction
MILESTONE_COUNTS = (1, 100, 1000, 5000, 10000, 25000, 50000, 100000, 250000)

DEFINITIONS = {
    "tone": "Each written message gets a tone score from a word list on this Mac: positive words and emoji raise "
            "it, negative ones lower it, 'not' reverses and 'very' strengthens. Scores here run from -100 (very "
            "negative) to +100 (very positive); most everyday messages are neutral. It reads words, not meaning, "
            "so sarcasm and inside jokes are missed. People need at least %d written messages to be ranked."
            % MIN_TONE_MESSAGES,
    "importance": "One-to-one chats only, for people with at least %d messages whom you have written to at least "
                  "once. Anyone you label as a service is left out, and people you pin as your inner circle are "
                  "always listed first. The score is an average of six parts, each 0 to 100, with volume counting "
                  "half as much as the others. Volume: how much you talk, on a scale that keeps the busiest person "
                  "from dwarfing everyone. Consistency: the share of months in your whole history with at least "
                  "one message. Recency: 100 for someone you messaged at the end of your history, halving every "
                  "%d days of silence. Two-sided: how evenly messages and conversation starts are split. Warmth: "
                  "how their average tone ranks among everyone scored. Depth: how their average message length "
                  "and share of long messages rank among everyone scored. It measures texting only. Someone you "
                  "mostly see in person or call will score lower than they matter, which is what pins are for."
                  % (MIN_IMPORTANCE_MESSAGES, RECENCY_HALF_LIFE_DAYS),
    "community": "One-to-one chats with people you have written to, measured over the 12 months up to the end of your history. Services you labelled are left out. A steady "
                 "contact has messages in at least %d of those months. Breadth is the number of people your "
                 "messaging would cover if it were spread evenly: lower means concentrated on a few people, higher "
                 "means spread across many. 'A year earlier' is the same measure for the 12 months before that."
                 % STEADY_MONTHS,
    "drift": "Compares the last %d days of your history with the %d days before. Growing: at least 20 recent "
             "messages and half again as many as before. Fading: at least 20 before and half or fewer since. "
             "Gone quiet: at least %d messages in total and none in the last %d days."
             % (DRIFT_WINDOW_DAYS, DRIFT_WINDOW_DAYS, QUIET_MIN_MESSAGES, QUIET_AFTER_DAYS),
    "streaks": "A streak is a run of consecutive days with at least one message. Milestones are the days the "
               "running message count passed each round number.",
    "balance": "One-to-one chats only. 'In a row' counts runs of three or more messages from the same person "
               "with no reply in between. 'Left unanswered' counts conversations whose last messages included a "
               "question that got no reply before %d hours of silence." % CONVERSATION_GAP_HOURS,
    "expressions": "Counts messages containing each kind of expression, split by who wrote it. A message counts "
                   "once per kind however many times the words appear.",
    "style": "Written messages only, for one-to-one chats with at least %d written messages from you. Rates are "
             "the share of messages with that feature." % MIN_TONE_MESSAGES,
    "phrases": "Repeated runs of three to eight words. Phrases made only of small connecting words, or "
               "ending mid-thought on one, are skipped. A shorter phrase is hidden when a longer one containing "
               "it is nearly as common.",
    "signature": "Words and phrases this person uses much more often than other people do, and that you use "
                 "more with them than with others: at least three times the usual rate.",
}

# Small connecting words. A phrase made only of these says nothing.
FUNCTION_WORDS = frozenset("""
a an the and or but if so of to in on at for with from by as is are was were be been am do does did have has
had will would can could should i you he she it we they me him her us them my your his its our their this
that these those there here what when where who how why not no it's i'm you're that's
""".split())
# A phrase ending on one of these is cut off mid-thought.
DANGLING = frozenset("a an the and or but of to at in on for with from by as is are was were be my your our their".split())
LEADING_JUNK = frozenset("and or but".split())

_PHRASE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9']*")


# ---------------------------------------------------------------------------------- shared

def _last_day(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute("SELECT MAX(day) FROM messages").fetchone()
    return datetime.strptime(row[0], "%Y-%m-%d").date() if row and row[0] else None


def _days_ago(last: date, days: int) -> str:
    return (last - timedelta(days=days)).strftime("%Y-%m-%d")


def _score(value: Optional[float]) -> Optional[int]:
    """Tone average (-1..1) as a whole number from -100 to 100."""
    return None if value is None else int(round(value * 100))


def _percentiles(values: Dict[str, float]) -> Dict[str, float]:
    """Where each value ranks among the others, 0 (lowest) to 100 (highest). Ties share a rank."""
    if len(values) <= 1:
        return {key: 50.0 for key in values}
    ordered = sorted(values.values())
    result = {}
    for key, value in values.items():
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        result[key] = 100.0 * (below + (equal - 1) / 2.0) / (len(ordered) - 1)
    return result


# ---------------------------------------------------------------------------------- tone

def tone_by_person(conn: sqlite3.Connection, filters: Filters, limit: int = 200) -> Dict[str, object]:
    """Average tone per person and group, with your side and theirs shown separately."""
    where, params = filters.where("m")
    rows = conn.execute(
        "SELECT counterpart, is_group, COUNT(*) AS n, AVG(tone) AS overall, "
        "AVG(CASE WHEN from_me = 1 THEN tone END) AS mine, AVG(CASE WHEN from_me = 0 THEN tone END) AS theirs, "
        "SUM(CASE WHEN tone >= ? THEN 1 ELSE 0 END) AS positive, SUM(CASE WHEN tone <= ? THEN 1 ELSE 0 END) AS negative "
        "FROM messages m WHERE %s AND m.kind = 'text' GROUP BY counterpart, is_group HAVING n >= ? "
        "ORDER BY overall DESC LIMIT ?" % where, [POSITIVE_AT, NEGATIVE_AT] + params + [MIN_TONE_MESSAGES, int(limit)]
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) AS n, AVG(tone) AS overall, AVG(CASE WHEN from_me = 1 THEN tone END) AS mine, "
        "AVG(CASE WHEN from_me = 0 THEN tone END) AS theirs, SUM(CASE WHEN tone >= ? THEN 1 ELSE 0 END) AS positive, "
        "SUM(CASE WHEN tone <= ? THEN 1 ELSE 0 END) AS negative FROM messages m WHERE %s AND m.kind = 'text'" % where,
        [POSITIVE_AT, NEGATIVE_AT] + params
    ).fetchone()

    def share(part, whole):
        return round(100.0 * (part or 0) / whole, 1) if whole else 0.0

    return {
        "definition": DEFINITIONS["tone"],
        "overall": {"messages": total["n"], "score": _score(total["overall"]), "my_score": _score(total["mine"]),
                    "their_score": _score(total["theirs"]), "positive_share": share(total["positive"], total["n"]),
                    "negative_share": share(total["negative"], total["n"])},
        "rows": [{"name": r["counterpart"], "is_group": bool(r["is_group"]), "messages": r["n"],
                  "score": _score(r["overall"]), "my_score": _score(r["mine"]), "their_score": _score(r["theirs"]),
                  "positive_share": share(r["positive"], r["n"]), "negative_share": share(r["negative"], r["n"])}
                 for r in rows],
    }


def tone_over_time(conn: sqlite3.Connection, filters: Filters, bucket: str = "month") -> Dict[str, object]:
    """How positive or negative messages were, period by period."""
    if bucket not in BUCKET_COLUMNS:
        raise QueryError("Period must be day, week, month or year.")
    column = BUCKET_COLUMNS[bucket]
    where, params = filters.where("m")
    rows = conn.execute(
        "SELECT %s AS period, COUNT(*) AS n, AVG(tone) AS overall, "
        "AVG(CASE WHEN from_me = 1 THEN tone END) AS mine, AVG(CASE WHEN from_me = 0 THEN tone END) AS theirs, "
        "SUM(CASE WHEN tone >= ? THEN 1 ELSE 0 END) AS positive, SUM(CASE WHEN tone <= ? THEN 1 ELSE 0 END) AS negative "
        "FROM messages m WHERE %s AND m.kind = 'text' GROUP BY %s ORDER BY %s" % (column, where, column, column),
        [POSITIVE_AT, NEGATIVE_AT] + params
    ).fetchall()
    return {
        "definition": DEFINITIONS["tone"], "bucket": bucket,
        "buckets": [{"period": r["period"], "messages": r["n"], "score": _score(r["overall"]),
                     "my_score": _score(r["mine"]), "their_score": _score(r["theirs"]),
                     "positive_share": round(100.0 * r["positive"] / r["n"], 1),
                     "negative_share": round(100.0 * r["negative"] / r["n"], 1)} for r in rows],
    }


def tone_moments(conn: sqlite3.Connection, filters: Filters) -> Dict[str, object]:
    """The warmest and most tense days, and the most positive and most negative messages."""
    from .query import _row
    where, params = filters.where("m")
    base = "FROM messages m WHERE %s AND m.kind = 'text'" % where
    warm = conn.execute(
        "SELECT day, COUNT(*) AS n, AVG(tone) AS overall %s GROUP BY day HAVING n >= 10 AND overall >= ? "
        "ORDER BY overall DESC LIMIT 8" % base, params + [POSITIVE_AT]).fetchall()
    tense = conn.execute(
        "SELECT day, COUNT(*) AS n, AVG(tone) AS overall, SUM(CASE WHEN tone <= ? THEN 1 ELSE 0 END) AS negative %s "
        "GROUP BY day HAVING n >= 10 AND negative >= 3 ORDER BY 1.0 * negative / n DESC, negative DESC LIMIT 8" % base,
        [NEGATIVE_AT] + params).fetchall()
    positive = conn.execute("SELECT * %s AND m.words >= 5 AND m.tone >= ? ORDER BY m.tone DESC, m.words DESC LIMIT 10"
                            % base, params + [POSITIVE_AT]).fetchall()
    negative = conn.execute("SELECT * %s AND m.words >= 5 AND m.tone <= ? ORDER BY m.tone ASC, m.words DESC LIMIT 10"
                            % base, params + [NEGATIVE_AT]).fetchall()
    return {
        "definition": "Days need at least 10 written messages. Warmest days have the highest average tone. Most "
                      "tense days have the largest share of negative messages, with at least 3. Messages need at "
                      "least 5 words and must actually score positive or negative.",
        "best_days": [{"day": r["day"], "messages": r["n"], "score": _score(r["overall"])} for r in warm],
        "worst_days": [{"day": r["day"], "messages": r["n"], "score": _score(r["overall"]),
                        "negative_messages": r["negative"],
                        "negative_share": round(100.0 * r["negative"] / r["n"], 1)} for r in tense],
        "most_positive": [dict(_row(r), score=_score(r["tone"])) for r in positive],
        "most_negative": [dict(_row(r), score=_score(r["tone"])) for r in negative],
    }


# ---------------------------------------------------------------------------------- importance and community

def importance(conn: sqlite3.Connection, filters: Optional[Filters] = None, limit: int = 500) -> Dict[str, object]:
    """A transparent importance score per person, built from six parts."""
    filters = filters or Filters()

    def compute():
        dated = Filters(date_from=filters.date_from, date_to=filters.date_to, scope="direct")
        where, params = dated.where("m")
        rows = conn.execute(
            "SELECT counterpart, COUNT(*) AS n, SUM(from_me) AS sent, MIN(day) AS first_day, MAX(day) AS last_day, "
            "COUNT(DISTINCT month) AS active_months, AVG(CASE WHEN kind = 'text' THEN tone END) AS tone, "
            "AVG(CASE WHEN kind = 'text' THEN words END) AS avg_words, "
            "SUM(CASE WHEN words >= ? THEN 1 ELSE 0 END) AS long_messages, "
            "SUM(CASE WHEN kind = 'text' THEN 1 ELSE 0 END) AS texts "
            "FROM messages m WHERE %s GROUP BY counterpart HAVING n >= ? AND sent > 0" % where,
            [LONG_MESSAGE_WORDS] + params + [MIN_IMPORTANCE_MESSAGES]
        ).fetchall()
        known = labels.load()
        rows = [r for r in rows if known.get(r["counterpart"], {}).get("label") != "service"]
        span = conn.execute("SELECT MIN(day), MAX(day), COUNT(DISTINCT month) FROM messages m WHERE %s" % where,
                            params).fetchone()
        if not rows or not span[0]:
            return {"definition": DEFINITIONS["importance"], "people_scored": 0, "rows": []}
        first = datetime.strptime(span[0], "%Y-%m-%d").date()
        last = datetime.strptime(span[1], "%Y-%m-%d").date()
        history_months = (last.year - first.year) * 12 + (last.month - first.month) + 1
        starts = {r["name"]: r for r in stats.response(conn, dated, limit=100000)["rows"]}
        biggest = max(r["n"] for r in rows)
        warmth = _percentiles({r["counterpart"]: r["tone"] or 0.0 for r in rows})
        length = _percentiles({r["counterpart"]: r["avg_words"] or 0.0 for r in rows})
        long_share = _percentiles({r["counterpart"]: (r["long_messages"] / float(r["texts"])) if r["texts"] else 0.0
                                   for r in rows})
        result = []
        for r in rows:
            name = r["counterpart"]
            last_day = datetime.strptime(r["last_day"], "%Y-%m-%d").date()
            silent_days = (last - last_day).days
            message_balance = 1.0 - abs(2 * r["sent"] - r["n"]) / float(r["n"])
            s = starts.get(name)
            if s and s["conversations"]:
                start_balance = 1.0 - abs(s["started_by_me"] - s["started_by_them"]) / float(s["conversations"])
                two_sided = 100.0 * (message_balance + start_balance) / 2.0
            else:
                two_sided = 100.0 * message_balance
            parts = {
                "volume": 100.0 * math.log1p(r["n"]) / math.log1p(biggest),
                "consistency": 100.0 * r["active_months"] / history_months,
                "recency": 100.0 * 0.5 ** (silent_days / float(RECENCY_HALF_LIFE_DAYS)),
                "two_sided": two_sided,
                "warmth": warmth[name],
                "depth": (length[name] + long_share[name]) / 2.0,
            }
            weighted = sum(parts[key] * IMPORTANCE_WEIGHTS[key] for key in parts) / sum(IMPORTANCE_WEIGHTS.values())
            tag = known.get(name, {})
            result.append({
                "name": name, "score": int(round(weighted)),
                "label": tag.get("label", ""), "pinned": bool(tag.get("pinned")),
                "parts": {key: int(round(value)) for key, value in parts.items()},
                "messages": r["n"], "sent": r["sent"], "received": r["n"] - r["sent"],
                "first_day": r["first_day"], "last_day": r["last_day"], "days_since_last": silent_days,
                "years_known": round((last - datetime.strptime(r["first_day"], "%Y-%m-%d").date()).days / 365.25, 1),
                "tone_score": _score(r["tone"]), "average_words": round(r["avg_words"] or 0.0, 1),
            })
        # Pinned people first, whatever their score. Texting cannot see who you live with.
        result.sort(key=lambda item: (item["pinned"], item["score"], item["messages"]), reverse=True)
        for position, item in enumerate(result, start=1):
            item["rank"] = position
        return {"definition": DEFINITIONS["importance"], "people_scored": len(result), "rows": result}

    data = _cached(conn, "importance", (filters.date_from, filters.date_to, labels.version()), compute)
    return dict(data, rows=data["rows"][:int(limit)])


def _year_window(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, object]:
    """Community measures for one 12-month window of one-to-one messages."""
    skip = labels.services()
    rows = [r for r in conn.execute(
        "SELECT counterpart, COUNT(*) AS n, COUNT(DISTINCT month) AS months, SUM(from_me) AS sent "
        "FROM messages WHERE is_group = 0 AND kind != 'reaction' AND day > ? AND day <= ? GROUP BY counterpart "
        "HAVING sent > 0", (start, end)
    ).fetchall() if r["counterpart"] not in skip]
    total = sum(r["n"] for r in rows)
    counts = sorted((r["n"] for r in rows), reverse=True)
    steady = [r for r in rows if r["months"] >= STEADY_MONTHS]
    two_way = [r for r in steady if 0.25 <= r["sent"] / float(r["n"]) <= 0.75]
    entropy = -sum((n / float(total)) * math.log(n / float(total)) for n in counts) if total else 0.0
    return {
        "people": len(rows), "messages": total, "steady_contacts": len(steady),
        "two_way_steady": len(two_way),
        "breadth": round(math.exp(entropy), 1) if total else 0.0,
        "top1_share": round(100.0 * counts[0] / total, 1) if total else 0.0,
        "top5_share": round(100.0 * sum(counts[:5]) / total, 1) if total else 0.0,
    }


def community(conn: sqlite3.Connection) -> Dict[str, object]:
    """The shape and strength of your texting circle, now and a year earlier, plus its history by year."""

    def compute():
        last = _last_day(conn)
        if last is None:
            return {"definition": DEFINITIONS["community"], "now": {}, "year_earlier": {}, "by_year": []}
        end = last.strftime("%Y-%m-%d")
        now = _year_window(conn, _days_ago(last, 365), end)
        earlier = _year_window(conn, _days_ago(last, 730), _days_ago(last, 365))
        now["active_last_30_days"] = conn.execute(
            "SELECT COUNT(DISTINCT counterpart) FROM messages WHERE is_group = 0 AND kind != 'reaction' AND day > ?",
            (_days_ago(last, 30),)).fetchone()[0]
        now["active_groups"] = conn.execute(
            "SELECT COUNT(DISTINCT counterpart) FROM messages WHERE is_group = 1 AND kind != 'reaction' AND day > ?",
            (_days_ago(last, 365),)).fetchone()[0]
        by_year: Dict[str, Dict[str, int]] = {}
        for year, people in conn.execute(
            "SELECT year, COUNT(*) FROM (SELECT year, counterpart FROM messages WHERE is_group = 0 AND "
            "kind != 'reaction' GROUP BY year, counterpart HAVING COUNT(*) >= 20) GROUP BY year"
        ):
            by_year.setdefault(year, {"year": year, "regular_people": 0, "new_people": 0})["regular_people"] = people
        for year, people in conn.execute(
            "SELECT SUBSTR(first_day, 1, 4) AS year, COUNT(*) FROM (SELECT counterpart, MIN(day) AS first_day "
            "FROM messages WHERE is_group = 0 AND kind != 'reaction' GROUP BY counterpart HAVING COUNT(*) >= 20) "
            "GROUP BY year"
        ):
            by_year.setdefault(year, {"year": year, "regular_people": 0, "new_people": 0})["new_people"] = people
        return {
            "definition": DEFINITIONS["community"], "as_of": end, "now": now, "year_earlier": earlier,
            "by_year": [by_year[y] for y in sorted(by_year)],
            "by_year_definition": "Regular people: those with at least 20 one-to-one messages that year. "
                                  "New people: those whose first message with you was that year.",
        }

    return _cached(conn, "community", (labels.version(),), compute)


def drift(conn: sqlite3.Connection, limit: int = 15) -> Dict[str, object]:
    """Who you are talking to more, who has faded, and who has gone quiet."""

    def compute():
        last = _last_day(conn)
        if last is None:
            return {"definition": DEFINITIONS["drift"], "growing": [], "fading": [], "gone_quiet": []}
        recent_from, before_from = _days_ago(last, DRIFT_WINDOW_DAYS), _days_ago(last, 2 * DRIFT_WINDOW_DAYS)
        rows = conn.execute(
            "SELECT counterpart, COUNT(*) AS total, MAX(day) AS last_day, "
            "SUM(CASE WHEN day > ? THEN 1 ELSE 0 END) AS recent, "
            "SUM(CASE WHEN day > ? AND day <= ? THEN 1 ELSE 0 END) AS before "
            "FROM messages WHERE is_group = 0 AND kind != 'reaction' GROUP BY counterpart HAVING SUM(from_me) > 0",
            (recent_from, before_from, recent_from)
        ).fetchall()
        skip = labels.services()
        growing, fading, quiet = [], [], []
        for r in rows:
            if r["counterpart"] in skip:
                continue
            item = {"name": r["counterpart"], "recent": r["recent"], "before": r["before"], "total": r["total"],
                    "last_day": r["last_day"]}
            if r["recent"] >= 20 and r["recent"] >= 1.5 * r["before"]:
                growing.append(item)
            elif r["before"] >= 20 and r["recent"] <= 0.5 * r["before"]:
                fading.append(item)
            if r["total"] >= QUIET_MIN_MESSAGES and r["recent"] == 0 and r["last_day"] <= _days_ago(last, QUIET_AFTER_DAYS):
                item["days_silent"] = (last - datetime.strptime(r["last_day"], "%Y-%m-%d").date()).days
                quiet.append(item)
        growing.sort(key=lambda i: i["recent"] - i["before"], reverse=True)
        fading.sort(key=lambda i: i["before"] - i["recent"], reverse=True)
        quiet.sort(key=lambda i: i["total"], reverse=True)
        return {"definition": DEFINITIONS["drift"], "as_of": last.strftime("%Y-%m-%d"),
                "growing": growing, "fading": fading, "gone_quiet": quiet}

    data = _cached(conn, "drift", (labels.version(),), compute)
    return dict(data, growing=data["growing"][:limit], fading=data["fading"][:limit], gone_quiet=data["gone_quiet"][:limit])


# ---------------------------------------------------------------------------------- streaks and balance

def streaks(conn: sqlite3.Connection, filters: Filters) -> Dict[str, object]:
    """Longest runs of consecutive days, the biggest day, and when round-number message counts were passed."""
    where, params = filters.where("m")
    days = conn.execute("SELECT day, COUNT(*) AS n FROM messages m WHERE %s GROUP BY day ORDER BY day" % where,
                        params).fetchall()
    if not days:
        return {"definition": DEFINITIONS["streaks"], "days_with_messages": 0}
    best = (0, None, None)
    run_start = previous = None
    run = 0
    biggest = max(days, key=lambda r: r["n"])
    running = 0
    wanted = list(MILESTONE_COUNTS)
    milestones = []
    for row in days:
        current = datetime.strptime(row["day"], "%Y-%m-%d").date()
        if previous is not None and (current - previous).days == 1:
            run += 1
        else:
            run, run_start = 1, current
        if run > best[0]:
            best = (run, run_start, current)
        previous = current
        running += row["n"]
        while wanted and running >= wanted[0]:
            milestones.append({"count": wanted.pop(0), "day": row["day"]})
    last = _last_day(conn)
    current_streak = run if previous is not None and last is not None and (last - previous).days == 0 else 0
    first = conn.execute("SELECT * FROM messages m WHERE %s ORDER BY m.ts, m.id LIMIT 1" % where, params).fetchone()
    from .query import _row
    first_day = datetime.strptime(days[0]["day"], "%Y-%m-%d").date()
    return {
        "definition": DEFINITIONS["streaks"],
        "days_with_messages": len(days), "first_day": days[0]["day"], "last_day": days[-1]["day"],
        "years_since_first": round((last - first_day).days / 365.25, 1) if last else None,
        "first_message": _row(first) if first else None,
        "longest_streak": {"days": best[0], "from": best[1].strftime("%Y-%m-%d"), "to": best[2].strftime("%Y-%m-%d")},
        "current_streak_days": current_streak,
        "biggest_day": {"day": biggest["day"], "messages": biggest["n"]},
        "milestones": milestones,
    }


def balance(conn: sqlite3.Connection, filters: Filters, limit: int = 100) -> Dict[str, object]:
    """Who carries the conversation: words, runs of messages, questions asked and questions left unanswered."""

    def compute():
        direct = Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to, scope="direct")
        where, params = direct.where("m")
        gap = CONVERSATION_GAP_HOURS * 3600
        last_ts = conn.execute("SELECT MAX(ts) FROM messages").fetchone()[0] or 0
        people: Dict[str, dict] = {}

        def close(stat, sender, run_has_question, ended_at):
            # A conversation still inside the gap at the end of the data may yet be answered.
            if stat is not None and run_has_question and last_ts - ended_at >= gap:
                stat["unanswered_by_them" if sender else "unanswered_by_me"] += 1

        current = stat = None
        previous_ts = 0
        run_sender = None
        run_length = 0
        run_question = False
        for name, ts, from_me, words, flags in conn.execute(
            "SELECT counterpart, ts, from_me, words, flags FROM messages m WHERE %s ORDER BY counterpart, ts, id"
            % where, params
        ):
            if name != current:
                close(stat, run_sender, run_question, previous_ts)
                current = name
                stat = people.setdefault(name, {
                    "my_messages": 0, "their_messages": 0, "my_words": 0, "their_words": 0,
                    "my_runs": 0, "their_runs": 0, "my_questions": 0, "their_questions": 0,
                    "unanswered_by_me": 0, "unanswered_by_them": 0})
                previous_ts, run_sender, run_length, run_question = 0, None, 0, False
            new_conversation = previous_ts and ts - previous_ts >= gap
            if new_conversation:
                close(stat, run_sender, run_question, previous_ts)
            if new_conversation or from_me != run_sender:
                run_sender, run_length, run_question = from_me, 0, False
            run_length += 1
            if run_length == 3:
                stat["my_runs" if from_me else "their_runs"] += 1
            asked = bool(flags & features.QUESTION)
            run_question = run_question or asked
            side = "my" if from_me else "their"
            stat[side + "_messages"] += 1
            stat[side + "_words"] += words
            if asked:
                stat[side + "_questions"] += 1
            previous_ts = ts
        close(stat, run_sender, run_question, previous_ts)

        rows = []
        for name, s in people.items():
            total_words = s["my_words"] + s["their_words"]
            rows.append(dict(s, name=name, messages=s["my_messages"] + s["their_messages"],
                             my_word_share=round(100.0 * s["my_words"] / total_words, 1) if total_words else None,
                             my_average_words=round(s["my_words"] / float(s["my_messages"]), 1) if s["my_messages"] else None,
                             their_average_words=round(s["their_words"] / float(s["their_messages"]), 1) if s["their_messages"] else None))
        rows.sort(key=lambda r: r["messages"], reverse=True)
        return {"definition": DEFINITIONS["balance"], "rows": rows}

    data = _cached(conn, "balance", filters.key(), compute)
    return dict(data, rows=data["rows"][:int(limit)])


# ---------------------------------------------------------------------------------- expressions and style

def expressions(conn: sqlite3.Connection, filters: Filters, bucket: str = "year", limit: int = 40) -> Dict[str, object]:
    """How often affection, thanks, apologies, laughter, questions, prayer and encouragement appear."""
    if bucket not in BUCKET_COLUMNS:
        raise QueryError("Period must be day, week, month or year.")
    where, params = filters.where("m")
    sums = ", ".join(
        "SUM(CASE WHEN flags & %d AND from_me = 1 THEN 1 ELSE 0 END) AS %s_me, "
        "SUM(CASE WHEN flags & %d AND from_me = 0 THEN 1 ELSE 0 END) AS %s_them" % (bit, name, bit, name)
        for name, bit, _examples in features.EXPRESSIONS)
    base = "FROM messages m WHERE %s AND m.kind = 'text'" % where

    def unpack(row):
        return {name: {"me": row[name + "_me"] or 0, "them": row[name + "_them"] or 0}
                for name, _bit, _examples in features.EXPRESSIONS}

    total = conn.execute("SELECT COUNT(*) AS n, %s %s" % (sums, base), params).fetchone()
    people = conn.execute("SELECT counterpart, is_group, COUNT(*) AS n, %s %s GROUP BY counterpart, is_group "
                          "ORDER BY n DESC LIMIT ?" % (sums, base), params + [int(limit)]).fetchall()
    column = BUCKET_COLUMNS[bucket]
    periods = conn.execute("SELECT %s AS period, COUNT(*) AS n, %s %s GROUP BY %s ORDER BY %s"
                           % (column, sums, base, column, column), params).fetchall()
    return {
        "definition": DEFINITIONS["expressions"],
        "kinds": [{"name": name, "looks_for": examples} for name, _bit, examples in features.EXPRESSIONS],
        "messages": total["n"], "totals": unpack(total), "bucket": bucket,
        "by_person": [dict(unpack(r), name=r["counterpart"], is_group=bool(r["is_group"]), messages=r["n"]) for r in people],
        "over_time": [dict(unpack(r), period=r["period"], messages=r["n"]) for r in periods],
    }


_STYLE_SUMS = (
    "COUNT(*) AS n, AVG(words) AS words, "
    "AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS emoji, AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS exclaim, "
    "AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS question, AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS shout, "
    "AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS lower_start, AVG(CASE WHEN flags & %d THEN 100.0 ELSE 0 END) AS link, "
    "AVG(CASE WHEN words >= %d THEN 100.0 ELSE 0 END) AS long"
    % (features.EMOJI, features.EXCLAIM, features.QUESTION, features.SHOUT, features.LOWER_START, features.LINK,
       LONG_MESSAGE_WORDS)
)


def _style_row(row) -> Dict[str, object]:
    return {"messages": row["n"], "average_words": round(row["words"] or 0.0, 1),
            "emoji_rate": round(row["emoji"] or 0.0, 1), "exclamation_rate": round(row["exclaim"] or 0.0, 1),
            "question_rate": round(row["question"] or 0.0, 1), "all_caps_rate": round(row["shout"] or 0.0, 1),
            "lowercase_start_rate": round(row["lower_start"] or 0.0, 1), "link_rate": round(row["link"] or 0.0, 1),
            "long_message_rate": round(row["long"] or 0.0, 1)}


def style(conn: sqlite3.Connection, filters: Filters, limit: int = 60) -> Dict[str, object]:
    """How you write overall, how others write to you, and how your style shifts from person to person."""
    base = Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to, scope=filters.scope)
    where, params = base.where("m")
    sql = "SELECT %s FROM messages m WHERE %s AND m.kind = 'text' AND m.from_me = ?" % (_STYLE_SUMS, where)
    mine = conn.execute(sql, params + [1]).fetchone()
    theirs = conn.execute(sql, params + [0]).fetchone()
    direct = Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to, scope="direct")
    dwhere, dparams = direct.where("m")
    rows = conn.execute(
        "SELECT counterpart, from_me, %s FROM messages m WHERE %s AND m.kind = 'text' GROUP BY counterpart, from_me"
        % (_STYLE_SUMS, dwhere), dparams
    ).fetchall()
    people: Dict[str, dict] = {}
    for r in rows:
        people.setdefault(r["counterpart"], {})["me" if r["from_me"] else "them"] = _style_row(r)
    listed = [{"name": name, "me": sides["me"], "them": sides.get("them")}
              for name, sides in people.items() if "me" in sides and sides["me"]["messages"] >= MIN_TONE_MESSAGES]
    listed.sort(key=lambda item: item["me"]["messages"], reverse=True)
    return {
        "definition": DEFINITIONS["style"],
        "me": _style_row(mine) if mine["n"] else None, "them": _style_row(theirs) if theirs["n"] else None,
        "by_person": listed[:int(limit)],
    }


# ---------------------------------------------------------------------------------- phrases

def _phrase_tokens(text: str) -> List[str]:
    return _PHRASE_TOKEN_RE.findall(URL_RE.sub(" ", text).lower().replace("’", "'"))


def _worth_listing(words: List[str]) -> bool:
    """Skip phrases that are only connecting words, or that start or stop mid-thought."""
    if words[-1] in DANGLING or words[0] in LEADING_JUNK:
        return False
    return any(word not in FUNCTION_WORDS for word in words)


def long_phrases(conn: sqlite3.Connection, filters: Filters, top: int = 30) -> Dict[str, object]:
    """The most repeated phrases of three to eight words."""

    def compute():
        where, params = filters.where("m")
        vocabulary: Dict[str, int] = {}
        stream = array("I")
        messages = 0
        for (text,) in conn.execute("SELECT text FROM messages m WHERE %s AND m.kind = 'text'" % where, params):
            messages += 1
            for token in _phrase_tokens(text):
                stream.append(vocabulary.setdefault(token, len(vocabulary) + 1))
            stream.append(0)                       # 0 marks the end of a message; phrases never cross it
        ids = stream.tolist()
        size = len(vocabulary) + 1
        minimum = max(3, int(round(messages / 15000.0)))
        # keys[i] identifies the frequent phrase of the current length starting at word i (0 = none).
        # A longer phrase can only be frequent if both shorter phrases inside it are, so each pass
        # only extends phrases that survived the previous one. This keeps memory small.
        keys = ids
        found: Dict[int, Counter] = {}
        for length in range(2, MAX_PHRASE_WORDS + 1):
            counts: Counter = Counter()
            extended = [0] * len(ids)
            for i in range(len(ids) - length + 1):
                left = keys[i]
                if left and keys[i + 1]:
                    key = left * size + ids[i + length - 1]
                    extended[i] = key
                    counts[key] += 1
            frequent = {key for key, count in counts.items() if count >= minimum}
            keys = [key if key in frequent else 0 for key in extended]
            if length >= 3:
                found[length] = Counter({key: counts[key] for key in frequent})
            if not frequent:
                break
        words_by_id = [""] * size
        for word, number in vocabulary.items():
            words_by_id[number] = word

        def decode(key: int, length: int) -> List[str]:
            parts = []
            for _ in range(length):
                key, number = divmod(key, size)
                parts.append(words_by_id[number])
            return parts[::-1]

        by_length: Dict[int, List[Tuple[str, int]]] = {}
        for length, counter in found.items():
            listed = []
            for key, count in counter.most_common():
                words = decode(key, length)
                if _worth_listing(words):
                    listed.append((" ".join(words), count))
            by_length[length] = listed
        # Hide a shorter phrase when a longer one containing it is nearly as common.
        result: Dict[str, List[dict]] = {"3": [], "4": [], "5": [], "6+": []}
        for length in range(3, MAX_PHRASE_WORDS + 1):
            longer = [(p, c) for n in range(length + 1, MAX_PHRASE_WORDS + 1) for p, c in by_length.get(n, [])]
            group = result[str(length) if length <= 5 else "6+"]
            added = 0
            for phrase, count in by_length.get(length, []):
                padded = " %s " % phrase
                if any(padded in " %s " % other and other_count >= 0.8 * count for other, other_count in longer):
                    continue
                group.append({"term": phrase, "count": count})
                added += 1
                if added >= top:
                    break
        result["6+"].sort(key=lambda item: item["count"], reverse=True)
        result["6+"] = result["6+"][:top]
        return {"definition": DEFINITIONS["phrases"], "messages_analyzed": messages, "minimum_count": minimum,
                "by_length": result}

    return _cached(conn, "long_phrases", filters.key() + (top,), compute)


def _candidate_terms(tokens: List[str]) -> List[str]:
    """Single distinctive words plus two, three and four word phrases from one message."""
    terms = [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]
    for length in (2, 3, 4):
        for i in range(len(tokens) - length + 1):
            words = tokens[i:i + length]
            if _worth_listing(words):
                terms.append(" ".join(words))
    return terms


def signature(conn: sqlite3.Connection, contact: str, top: int = 20) -> Dict[str, object]:
    """What is distinctive about how one person writes to you, and how you write to them."""
    if not contact:
        raise QueryError("Choose a person first.")

    def compute():
        result = {"definition": DEFINITIONS["signature"]}
        for side, from_me in (("theirs", 0), ("mine", 1)):
            target: Counter = Counter()
            target_words = 0
            for (text,) in conn.execute(
                "SELECT text FROM messages WHERE counterpart = ? AND from_me = ? AND kind = 'text'", (contact, from_me)
            ):
                tokens = _phrase_tokens(text)
                target_words += len(tokens)
                target.update(set(_candidate_terms(tokens)))     # count messages, not repeats within one
            minimum = 5 if target_words >= 2000 else 3
            candidates = {term for term, count in target.items() if count >= minimum}
            background: Counter = Counter()
            background_words = 0
            if candidates:
                # Other people's messages are the yardstick for "usual". A large, evenly spaced
                # sample measures that just as well as reading every one, and far faster.
                others = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE counterpart != ? AND from_me = ? AND kind = 'text' AND is_group = 0",
                    (contact, from_me)).fetchone()[0]
                step = max(1, -(-others // SIGNATURE_BACKGROUND_SAMPLE))
                for (text,) in conn.execute(
                    "SELECT text FROM messages WHERE counterpart != ? AND from_me = ? AND kind = 'text' AND is_group = 0 "
                    "AND id % ? = 0", (contact, from_me, step)
                ):
                    tokens = _phrase_tokens(text)
                    background_words += len(tokens)
                    background.update(term for term in set(_candidate_terms(tokens)) if term in candidates)
            scored = []
            for term in candidates:
                rate = target[term] / float(max(target_words, 1))
                usual = (background[term] + 1) / float(background_words + 1000)
                lift = rate / usual
                if lift >= 3.0:
                    scored.append((target[term] * math.log(lift), term, target[term], lift))
            scored.sort(reverse=True)
            words = [{"term": t, "count": c, "times_usual": round(l, 1)} for _s, t, c, l in scored if " " not in t][:top]
            # Keep the longest form of a phrase: drop "roger that" when "roger that captain" is about as common.
            phrase_rows = [(t, c, l) for _s, t, c, l in scored if " " in t]
            phrases = []
            for term, count, lift in phrase_rows:
                padded = " %s " % term
                if any(padded in " %s " % other and other != term and other_count >= 0.8 * count
                       for other, other_count, _lift in phrase_rows):
                    continue
                phrases.append({"term": term, "count": count, "times_usual": round(lift, 1)})
            phrases = phrases[:top]
            result[side] = {"words": words, "phrases": phrases, "words_analyzed": target_words}
        return result

    return _cached(conn, "signature", (contact, top), compute)


# ---------------------------------------------------------------------------------- person profile

def profile(conn: sqlite3.Connection, contact: str, deep: bool = True) -> Dict[str, object]:
    """Everything about one person or group chat, gathered on one page.

    With deep=False the four slowest sections (signature phrases, repeated phrases, top emoji,
    and the 'we' and 'I' measures) are left out so the page can appear at once; the interface
    then asks for them separately.
    """
    from . import science, signals
    contact = (contact or "").strip()
    if not contact:
        raise QueryError("Choose a person or group first.")
    only = Filters(contact=contact)
    summary = stats.people(conn, only)
    if not summary["totals"]["messages"]:
        raise QueryError("No messages found with '%s'. Pick a name from the People list." % contact)

    def compute():
        is_group = bool(summary["rows"][0]["is_group"])
        ranked = importance(conn)
        tag = labels.load().get(contact, {})
        tasks = {
            "replies": lambda c: next((row for row in stats.response(c, only)["rows"] if row["name"] == contact), None),
            "balance": lambda c: next((row for row in balance(c, only)["rows"] if row["name"] == contact), None),
            "tone": lambda c: (tone_by_person(c, only)["rows"] or [None])[0],
            "by_year": lambda c: stats.trends(c, only, "year")["buckets"],
            "by_month": lambda c: stats.trends(c, only, "month")["buckets"],
            "tone_by_year": lambda c: tone_over_time(c, only, "year")["buckets"],
            "streaks": lambda c: streaks(c, only),
            "expressions": lambda c: expressions(c, only, "year")["totals"],
            "style": lambda c: style(c, only),
            "reactions": lambda c: signals.reactions(c, only, limit=1),
            "read_times": lambda c: signals.read_times(c, only, limit=1),
            "sharing": lambda c: signals.sharing(c, only, limit=1)["totals"],
            "replies_and_edits": lambda c: signals.replies(c, only, limit=1)["totals"],
        }
        if deep:
            tasks.update({
                "language": lambda c: next((row for row in science.language(c, only)["rows"] if row["name"] == contact), None),
                "top_emoji": lambda c: stats.words(c, only)["emoji"][:12],
                "long_phrases": lambda c: long_phrases(c, only, top=12)["by_length"],
            })
            if not is_group:
                tasks["signature"] = lambda c: signature(c, contact)
        page = {
            "name": contact, "is_group": is_group, "totals": summary["totals"],
            "label": tag.get("label", ""), "pinned": bool(tag.get("pinned")),
            "importance": next((row for row in ranked["rows"] if row["name"] == contact), None) if not is_group else None,
            "people_scored": ranked["people_scored"],
            "importance_definition": DEFINITIONS["importance"], "tone_definition": DEFINITIONS["tone"],
            "replies_definition": stats.DEFINITIONS["response"], "balance_definition": DEFINITIONS["balance"],
            "signature_definition": DEFINITIONS["signature"], "we_talk_definition": science.DEFINITIONS["we_talk"],
            "style_definition": science.DEFINITIONS["style_matching"],
            "language": None, "top_emoji": None, "long_phrases": None, "signature": None,
        }
        # One after another, on the same connection. Running these at the same time was tried and
        # measured twice as slow, because each extra connection starts with nothing cached.
        for key, work in tasks.items():
            page[key] = work(conn)
        return page

    return _cached(conn, "profile", (contact, deep, labels.version()), compute)
