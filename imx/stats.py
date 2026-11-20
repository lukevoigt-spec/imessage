"""Statistics over the local database.

Every statistic comes with a plain-language definition (see DEFINITIONS) that
the interface shows next to the numbers, so it is always clear what was counted.
Tapbacks are never counted as messages.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from statistics import median
from typing import Dict, List, Optional

from .features import EMOJI_RE, URL_RE
from .query import Filters, QueryError, build_match

# A pause this long or longer means the next message starts a new conversation.
CONVERSATION_GAP_HOURS = 6
# Do not report reply speed for a person on fewer replies than this.
MIN_REPLIES_FOR_SPEED = 5

DEFINITIONS = {
    "people": "Counts every message with words or an attachment. Tapbacks are left out. "
              "'Sent' means sent by you.",
    "trends": "Messages per period, in this Mac's time zone. The grid shows which hours of which "
              "weekdays are busiest.",
    "response": "One-to-one chats only. A conversation starts when %d or more hours have passed "
                "since the previous message; whoever writes first started it. A reply time runs "
                "from the first unanswered message to the other person's first response, within "
                "the same conversation. Speeds are medians, shown only with at least %d replies."
                % (CONVERSATION_GAP_HOURS, MIN_REPLIES_FOR_SPEED),
    "words": "Words of three or more letters, with common filler words and web links removed. "
             "Phrases are two neighbouring words. Emoji are counted wherever they appear.",
    "keyword": "Number of messages per period that match the search text.",
}

STOPWORDS = set("""
a about above after again against all also am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself
let's me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out
over own same shan't she she'd she'll she's should shouldn't so some such than that that's the their
theirs them themselves then there there's these they they'd they'll they're they've this those through
to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's
where where's which while who who's whom why why's with won't would wouldn't you you'd you'll you're
you've your yours yourself yourselves just got get will one like know now going yes yeah okay ok lol
haha im dont thats ill ive cant didnt youre its too also still well really much want need think going
gonna today tomorrow tonight back good great thanks thank sure see say said tell told can
""".split())

WORD_RE = re.compile(r"[a-z][a-z']{2,}")
BUCKET_COLUMNS = {"day": "day", "week": "week", "month": "month", "year": "year"}

_cache: Dict[tuple, object] = {}


def _cached(conn: sqlite3.Connection, name: str, key: tuple, compute):
    """Reuse a result until the next import changes the database."""
    stamp = conn.execute("SELECT value FROM meta WHERE key = 'last_import'").fetchone()
    database = conn.execute("PRAGMA database_list").fetchone()[2]     # which file this connection reads
    full_key = (database, name, key, stamp[0] if stamp else None)
    if full_key not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        _cache[full_key] = compute()
    return _cache[full_key]


def people(conn: sqlite3.Connection, filters: Filters, limit: int = 200) -> Dict[str, object]:
    """Who you message most: totals, sent and received, per person and per group chat."""
    where, params = filters.where("m")
    totals = conn.execute(
        "SELECT COUNT(*) AS messages, COALESCE(SUM(from_me), 0) AS sent, MIN(day) AS first_day, "
        "MAX(day) AS last_day, COUNT(DISTINCT CASE WHEN is_group = 0 THEN counterpart END) AS people, "
        "COUNT(DISTINCT CASE WHEN is_group = 1 THEN counterpart END) AS groups, "
        "COUNT(DISTINCT day) AS active_days FROM messages m WHERE %s" % where, params
    ).fetchone()
    rows = conn.execute(
        "SELECT counterpart, is_group, COUNT(*) AS total, SUM(from_me) AS sent, MIN(day) AS first_day, "
        "MAX(day) AS last_day FROM messages m WHERE %s GROUP BY counterpart, is_group "
        "ORDER BY total DESC LIMIT ?" % where, params + [int(limit)]
    ).fetchall()
    grand = totals["messages"] or 0
    return {
        "definition": DEFINITIONS["people"],
        "totals": {
            "messages": grand, "sent": totals["sent"], "received": grand - totals["sent"],
            "people": totals["people"], "groups": totals["groups"], "active_days": totals["active_days"],
            "first_day": totals["first_day"], "last_day": totals["last_day"],
        },
        "rows": [{
            "name": r["counterpart"], "is_group": bool(r["is_group"]), "total": r["total"],
            "sent": r["sent"], "received": r["total"] - r["sent"],
            "share": round(100.0 * r["total"] / grand, 1) if grand else 0.0,
            "first_day": r["first_day"], "last_day": r["last_day"],
        } for r in rows],
    }


def trends(conn: sqlite3.Connection, filters: Filters, bucket: str = "month") -> Dict[str, object]:
    """Message volume over time, plus the busiest hours and weekdays."""
    if bucket not in BUCKET_COLUMNS:
        raise QueryError("Period must be day, week, month or year.")
    column = BUCKET_COLUMNS[bucket]
    where, params = filters.where("m")
    buckets = conn.execute(
        "SELECT %s AS period, SUM(from_me) AS sent, COUNT(*) - SUM(from_me) AS received "
        "FROM messages m WHERE %s GROUP BY %s ORDER BY %s" % (column, where, column, column), params
    ).fetchall()
    heatmap = [[0] * 24 for _ in range(7)]
    for dow, hour, count in conn.execute(
        "SELECT dow, hour, COUNT(*) FROM messages m WHERE %s GROUP BY dow, hour" % where, params
    ):
        heatmap[dow][hour] = count
    busiest = conn.execute(
        "SELECT day, COUNT(*) AS total FROM messages m WHERE %s GROUP BY day ORDER BY total DESC LIMIT 10"
        % where, params
    ).fetchall()
    return {
        "definition": DEFINITIONS["trends"],
        "bucket": bucket,
        "buckets": [{"period": r["period"], "sent": r["sent"], "received": r["received"]} for r in buckets],
        "heatmap": heatmap,
        "busiest_days": [{"day": r["day"], "total": r["total"]} for r in busiest],
    }


def response(conn: sqlite3.Connection, filters: Filters, limit: int = 100) -> Dict[str, object]:
    """Reply speed and who starts conversations, per person. One-to-one chats only."""

    def compute():
        direct = Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to, scope="direct")
        where, params = direct.where("m")
        rows = conn.execute(
            "SELECT counterpart, ts, from_me FROM messages m WHERE %s ORDER BY counterpart, ts, id" % where, params
        )
        gap_seconds = CONVERSATION_GAP_HOURS * 3600
        people_stats: Dict[str, dict] = {}
        current = None
        stat: Optional[dict] = None
        last_ts = 0
        run_sender = None      # who sent the current unanswered run of messages
        run_start = 0          # when that run began
        for counterpart, ts, from_me in rows:
            if counterpart != current:
                current = counterpart
                stat = people_stats.setdefault(counterpart, {
                    "my_replies": [], "their_replies": [], "started_by_me": 0, "started_by_them": 0,
                    "longest_gap": 0, "messages": 0,
                })
                last_ts, run_sender = 0, None
            stat["messages"] += 1
            new_conversation = last_ts == 0 or ts - last_ts >= gap_seconds
            if new_conversation:
                if last_ts:
                    stat["longest_gap"] = max(stat["longest_gap"], ts - last_ts)
                stat["started_by_me" if from_me else "started_by_them"] += 1
                run_sender, run_start = from_me, ts
            elif from_me != run_sender:
                # The other side answered: time from the first unanswered message to this reply.
                stat["my_replies" if from_me else "their_replies"].append(ts - run_start)
                run_sender, run_start = from_me, ts
            last_ts = ts

        result_rows = []
        all_mine: List[int] = []
        all_theirs: List[int] = []
        for name, s in people_stats.items():
            all_mine += s["my_replies"]
            all_theirs += s["their_replies"]
            conversations = s["started_by_me"] + s["started_by_them"]
            result_rows.append({
                "name": name, "messages": s["messages"], "conversations": conversations,
                "started_by_me": s["started_by_me"], "started_by_them": s["started_by_them"],
                "my_replies": len(s["my_replies"]), "their_replies": len(s["their_replies"]),
                "my_median_seconds": int(median(s["my_replies"])) if len(s["my_replies"]) >= MIN_REPLIES_FOR_SPEED else None,
                "their_median_seconds": int(median(s["their_replies"])) if len(s["their_replies"]) >= MIN_REPLIES_FOR_SPEED else None,
                "longest_gap_days": round(s["longest_gap"] / 86400.0, 1),
            })
        result_rows.sort(key=lambda r: r["messages"], reverse=True)
        started_me = sum(r["started_by_me"] for r in result_rows)
        started_them = sum(r["started_by_them"] for r in result_rows)
        return {
            "definition": DEFINITIONS["response"],
            "overall": {
                "my_median_seconds": int(median(all_mine)) if all_mine else None,
                "their_median_seconds": int(median(all_theirs)) if all_theirs else None,
                "my_replies": len(all_mine), "their_replies": len(all_theirs),
                "started_by_me": started_me, "started_by_them": started_them,
            },
            "rows": result_rows[:int(limit)],
        }

    return _cached(conn, "response", filters.key() + (limit,), compute)


def words(conn: sqlite3.Connection, filters: Filters, top: int = 60) -> Dict[str, object]:
    """Most-used words, two-word phrases and emoji in the filtered messages."""

    def compute():
        where, params = filters.where("m")
        word_counts: Counter = Counter()
        phrase_counts: Counter = Counter()
        emoji_counts: Counter = Counter()
        analyzed = 0
        for (text,) in conn.execute("SELECT text FROM messages m WHERE %s AND m.kind = 'text'" % where, params):
            analyzed += 1
            emoji_counts.update(EMOJI_RE.findall(text))
            tokens = WORD_RE.findall(URL_RE.sub(" ", text).lower().replace("’", "'"))
            previous = None
            for token in tokens:
                token = token.strip("'")
                keep = len(token) >= 3 and token not in STOPWORDS
                if keep:
                    word_counts[token] += 1
                    if previous:
                        phrase_counts[previous + " " + token] += 1
                previous = token if keep else None
        return {
            "definition": DEFINITIONS["words"],
            "messages_analyzed": analyzed,
            "words": [{"term": t, "count": c} for t, c in word_counts.most_common(top)],
            "phrases": [{"term": t, "count": c} for t, c in phrase_counts.most_common(top) if c > 1],
            "emoji": [{"term": t, "count": c} for t, c in emoji_counts.most_common(top)],
        }

    return _cached(conn, "words", filters.key() + (top,), compute)


def keyword_trend(conn: sqlite3.Connection, query: str, filters: Filters, bucket: str = "month") -> Dict[str, object]:
    """How often a word, phrase or topic comes up over time, and with whom."""
    if bucket not in BUCKET_COLUMNS:
        raise QueryError("Period must be day, week, month or year.")
    match = build_match(query)
    if not match:
        raise QueryError("Type a word or phrase to chart.")
    column = BUCKET_COLUMNS[bucket]
    where, params = filters.where("m")
    source = "messages_fts f JOIN messages m ON m.id = f.rowid WHERE messages_fts MATCH ? AND %s" % where
    try:
        buckets = conn.execute(
            "SELECT m.%s AS period, SUM(m.from_me) AS sent, COUNT(*) - SUM(m.from_me) AS received FROM %s "
            "GROUP BY m.%s ORDER BY m.%s" % (column, source, column, column), [match] + params
        ).fetchall()
        by_person = conn.execute(
            "SELECT m.counterpart AS name, COUNT(*) AS total, SUM(m.from_me) AS sent FROM %s "
            "GROUP BY m.counterpart ORDER BY total DESC LIMIT 25" % source, [match] + params
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise QueryError("That search could not be run (%s). Try simpler words." % exc)
    return {
        "definition": DEFINITIONS["keyword"],
        "query": query, "bucket": bucket,
        "total": sum(r["sent"] + r["received"] for r in buckets),
        "buckets": [{"period": r["period"], "sent": r["sent"], "received": r["received"]} for r in buckets],
        "by_person": [{"name": r["name"], "total": r["total"], "sent": r["sent"],
                       "received": r["total"] - r["sent"]} for r in by_person],
    }
