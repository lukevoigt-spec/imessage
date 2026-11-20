"""Signals Apple records beyond the words: reactions, read times, direct replies, edits, attachments, links.

Each of these depends on fields that vary with the macOS version and with other
people's settings. Every result says how much data it rests on, so a thin or
missing signal is visible instead of looking like a real zero.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from statistics import median
from typing import Dict, List

from . import features
from .query import Filters, _row
from .stats import _cached

REACTION_NAMES = {2000: "love", 2001: "like", 2002: "dislike", 2003: "laugh", 2004: "emphasis", 2005: "question"}
OTHER_REACTION = "other"       # custom emoji and sticker reactions on newer systems

DEFINITIONS = {
    "reactions": "A reaction is a tapback on a specific message: love, like, dislike, laugh, emphasis or question. "
                 "Each is matched to the message it was placed on, so 'given' means I reacted to their message and "
                 "'received' means they reacted to mine. Removed reactions are not subtracted. Reactions whose "
                 "message is not in the local database are counted as unmatched.",
    "read_times": "Time from a message being sent to it being read, where this Mac recorded it. For messages you "
                  "received, that is when you opened them. For messages you sent, it exists only if the other "
                  "person shares read receipts. Typical times are medians over one-to-one chats. Coverage shows "
                  "how many messages of any kind have a read time at all.",
    "replies": "A direct reply is a message sent as a reply to one specific earlier message, not just the next "
               "message in the chat. Edited and unsent counts come from Apple's own markers; the original wording "
               "of an edited or unsent message is not kept.",
    "sharing": "Attachments are counted by kind from Apple's record of each file. A message with two photos counts "
               "once as a photo message. Stickers are left out. Links are grouped by website.",
}

_DOMAIN_RE = re.compile(r"(?:https?://|www\.)([^/\s?#:]+)", re.IGNORECASE)


def _with_reactions(filters: Filters) -> Filters:
    return Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to,
                   direction=filters.direction, scope=filters.scope, include_reactions=True)


def _kind(react_type: int) -> str:
    return REACTION_NAMES.get(react_type, OTHER_REACTION)


def reactions(conn: sqlite3.Connection, filters: Filters, limit: int = 40) -> Dict[str, object]:
    """Who reacts to whose messages, with which reaction, and which of your messages land."""
    where, params = _with_reactions(filters).where("r")
    base = ("FROM messages r LEFT JOIN messages t ON t.guid = r.react_to "
            "WHERE %s AND r.kind = 'reaction' AND r.react_type BETWEEN 2000 AND 2999" % where)
    total, matched = conn.execute("SELECT COUNT(*), COUNT(t.id) %s" % base, params).fetchone()
    by_type: Dict[str, Dict[str, int]] = {}
    for react_type, from_me, count in conn.execute(
        "SELECT r.react_type, r.from_me, COUNT(*) %s AND t.id IS NOT NULL GROUP BY r.react_type, r.from_me" % base, params
    ):
        entry = by_type.setdefault(_kind(react_type), {"given": 0, "received": 0})
        entry["given" if from_me else "received"] += count
    people: Dict[str, dict] = {}
    for name, is_group, from_me, react_type, count in conn.execute(
        "SELECT r.counterpart, r.is_group, r.from_me, r.react_type, COUNT(*) %s AND t.id IS NOT NULL "
        "GROUP BY r.counterpart, r.is_group, r.from_me, r.react_type" % base, params
    ):
        person = people.setdefault(name, {"name": name, "is_group": bool(is_group), "given": 0, "received": 0,
                                          "laughs_given": 0, "laughs_received": 0})
        side = "given" if from_me else "received"
        person[side] += count
        if react_type == 2003:
            person["laughs_" + side] += count
    # How many messages each side wrote, to turn counts into "reacts to N in every 100".
    mwhere, mparams = filters.where("m")
    for name, mine, theirs in conn.execute(
        "SELECT counterpart, SUM(from_me), COUNT(*) - SUM(from_me) FROM messages m WHERE %s GROUP BY counterpart"
        % mwhere, mparams
    ):
        if name in people:
            people[name]["given_per_100"] = round(100.0 * people[name]["given"] / theirs, 1) if theirs else None
            people[name]["received_per_100"] = round(100.0 * people[name]["received"] / mine, 1) if mine else None
    rows = sorted(people.values(), key=lambda p: p["given"] + p["received"], reverse=True)[:int(limit)]
    landed = conn.execute(
        "SELECT t.*, COUNT(*) AS reactions %s AND t.from_me = 1 GROUP BY t.id ORDER BY reactions DESC, t.ts DESC LIMIT 10"
        % base, params).fetchall()
    return {
        "definition": DEFINITIONS["reactions"],
        "total": total, "matched": matched, "unmatched": total - matched,
        "given": sum(v["given"] for v in by_type.values()), "received": sum(v["received"] for v in by_type.values()),
        "by_type": [dict(kind=k, **v) for k, v in sorted(by_type.items(), key=lambda kv: -(kv[1]["given"] + kv[1]["received"]))],
        "by_person": rows,
        "most_reacted_to": [dict(_row(r), reactions=r["reactions"]) for r in landed],
    }


def read_times(conn: sqlite3.Connection, filters: Filters, limit: int = 60) -> Dict[str, object]:
    """How long messages wait before being read, by you and (where shared) by them."""

    def compute():
        where, params = filters.where("m")
        coverage = conn.execute(
            "SELECT SUM(CASE WHEN from_me = 0 THEN 1 ELSE 0 END), SUM(CASE WHEN from_me = 0 AND read_ts IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(from_me), SUM(CASE WHEN from_me = 1 AND read_ts IS NOT NULL THEN 1 ELSE 0 END) FROM messages m WHERE %s"
            % where, params).fetchone()
        delays: Dict[str, Dict[str, List[int]]] = {}
        mine_all: List[int] = []
        theirs_all: List[int] = []
        for name, from_me, delay in conn.execute(
            "SELECT counterpart, from_me, read_ts - ts FROM messages m WHERE %s AND read_ts IS NOT NULL AND is_group = 0"
            % where, params
        ):
            person = delays.setdefault(name, {"mine": [], "theirs": []})
            if from_me:
                person["theirs"].append(delay)       # a message I sent, read by them
                theirs_all.append(delay)
            else:
                person["mine"].append(delay)         # a message I received, read by me
                mine_all.append(delay)

        def summary(values: List[int]) -> dict:
            if not values:
                return {"messages": 0, "median_seconds": None, "within_5_minutes": None, "over_a_day": None}
            return {"messages": len(values), "median_seconds": int(median(values)),
                    "within_5_minutes": round(100.0 * sum(1 for v in values if v <= 300) / len(values), 1),
                    "over_a_day": round(100.0 * sum(1 for v in values if v > 86400) / len(values), 1)}

        rows = [{"name": name, "me_reading_them": summary(d["mine"]), "them_reading_me": summary(d["theirs"])}
                for name, d in delays.items() if len(d["mine"]) + len(d["theirs"]) >= 5]
        rows.sort(key=lambda r: r["me_reading_them"]["messages"] + r["them_reading_me"]["messages"], reverse=True)
        received, received_read, sent, sent_read = [value or 0 for value in coverage]
        return {
            "definition": DEFINITIONS["read_times"],
            "coverage": {"received": received, "received_with_read_time": received_read,
                         "sent": sent, "sent_with_read_time": sent_read,
                         "received_share": round(100.0 * received_read / received, 1) if received else 0.0,
                         "sent_share": round(100.0 * sent_read / sent, 1) if sent else 0.0},
            "me_reading_them": summary(mine_all), "them_reading_me": summary(theirs_all),
            "rows": rows,
        }

    data = _cached(conn, "read_times", filters.key(), compute)
    return dict(data, rows=data["rows"][:int(limit)])


def replies(conn: sqlite3.Connection, filters: Filters, limit: int = 40) -> Dict[str, object]:
    """Direct replies, edited messages and unsent messages, split by who did it."""
    where, params = filters.where("m")
    sums = (
        "SUM(CASE WHEN reply_to IS NOT NULL AND from_me = 1 THEN 1 ELSE 0 END) AS replies_me, "
        "SUM(CASE WHEN reply_to IS NOT NULL AND from_me = 0 THEN 1 ELSE 0 END) AS replies_them, "
        "SUM(CASE WHEN flags & %d AND from_me = 1 THEN 1 ELSE 0 END) AS edited_me, "
        "SUM(CASE WHEN flags & %d AND from_me = 0 THEN 1 ELSE 0 END) AS edited_them, "
        "SUM(CASE WHEN flags & %d AND from_me = 1 THEN 1 ELSE 0 END) AS unsent_me, "
        "SUM(CASE WHEN flags & %d AND from_me = 0 THEN 1 ELSE 0 END) AS unsent_them"
        % (features.EDITED, features.EDITED, features.UNSENT, features.UNSENT))

    def unpack(row) -> dict:
        return {"direct_replies": {"me": row["replies_me"] or 0, "them": row["replies_them"] or 0},
                "edited": {"me": row["edited_me"] or 0, "them": row["edited_them"] or 0},
                "unsent": {"me": row["unsent_me"] or 0, "them": row["unsent_them"] or 0}}

    total = conn.execute("SELECT %s FROM messages m WHERE %s" % (sums, where), params).fetchone()
    people = conn.execute(
        "SELECT counterpart, is_group, %s FROM messages m WHERE %s GROUP BY counterpart, is_group "
        "HAVING replies_me + replies_them + edited_me + edited_them + unsent_me + unsent_them > 0 "
        "ORDER BY replies_me + replies_them + edited_me + edited_them + unsent_me + unsent_them DESC LIMIT ?"
        % (sums, where), params + [int(limit)]).fetchall()
    years = conn.execute("SELECT year, %s FROM messages m WHERE %s GROUP BY year ORDER BY year" % (sums, where), params).fetchall()
    return {
        "definition": DEFINITIONS["replies"], "totals": unpack(total),
        "by_person": [dict(unpack(r), name=r["counterpart"], is_group=bool(r["is_group"])) for r in people],
        "by_year": [dict(unpack(r), period=r["year"]) for r in years],
    }


def sharing(conn: sqlite3.Connection, filters: Filters, limit: int = 40) -> Dict[str, object]:
    """Photos, videos, audio, voice messages and links: what you share, with whom."""

    def compute():
        where, params = filters.where("m")
        kinds = (("photos", "att & %d" % features.PHOTO), ("videos", "att & %d" % features.VIDEO),
                 ("audio", "att & %d" % features.AUDIO), ("files", "att & %d" % features.OTHER_FILE),
                 ("voice_messages", "flags & %d" % features.VOICE), ("links", "flags & %d" % features.LINK))
        sums = ", ".join(
            "SUM(CASE WHEN %s AND from_me = 1 THEN 1 ELSE 0 END) AS %s_me, "
            "SUM(CASE WHEN %s AND from_me = 0 THEN 1 ELSE 0 END) AS %s_them" % (cond, name, cond, name)
            for name, cond in kinds)

        def unpack(row) -> dict:
            return {name: {"me": row[name + "_me"] or 0, "them": row[name + "_them"] or 0} for name, _ in kinds}

        total = conn.execute("SELECT %s FROM messages m WHERE %s" % (sums, where), params).fetchone()
        any_shared = " + ".join("%s_me + %s_them" % (name, name) for name, _ in kinds)
        people = conn.execute(
            "SELECT counterpart, is_group, %s FROM messages m WHERE %s GROUP BY counterpart, is_group "
            "HAVING %s > 0 ORDER BY %s DESC LIMIT ?" % (sums, where, any_shared, any_shared), params + [int(limit)]).fetchall()
        years = conn.execute("SELECT year, %s FROM messages m WHERE %s GROUP BY year ORDER BY year" % (sums, where), params).fetchall()
        sites_me: Counter = Counter()
        sites_them: Counter = Counter()
        for text, from_me in conn.execute(
            "SELECT text, from_me FROM messages m WHERE %s AND flags & %d" % (where, features.LINK), params
        ):
            # Tidy each address first ("www." dropped, lower case), then count each website once per message.
            domains = set()
            for domain in _DOMAIN_RE.findall(text):
                domain = domain.lower().strip(".")
                domains.add(domain[4:] if domain.startswith("www.") else domain)
            for domain in domains:
                (sites_me if from_me else sites_them)[domain] += 1
        sites = Counter(sites_me)
        sites.update(sites_them)
        return {
            "definition": DEFINITIONS["sharing"], "totals": unpack(total),
            "by_person": [dict(unpack(r), name=r["counterpart"], is_group=bool(r["is_group"])) for r in people],
            "by_year": [dict(unpack(r), period=r["year"]) for r in years],
            "websites": [{"site": site, "me": sites_me[site], "them": sites_them[site]} for site, _ in sites.most_common(40)],
        }

    return _cached(conn, "sharing", filters.key() + (limit,), compute)
