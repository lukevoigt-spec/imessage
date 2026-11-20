"""Saved topics.

A topic is a name plus a list of words and phrases, for example
Travel = flight, airport, hotel, "boarding pass". Searching a topic finds
messages containing any of its terms. Topics are stored in a small file in the
data folder and can be edited from the Topics screen. The AI topic finder can
suggest new ones, but nothing is saved until you choose to save it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List

from . import paths
from .query import Filters, QueryError, build_match

STARTER_TOPICS: Dict[str, List[str]] = {
    "Travel": ["flight", "airport", "hotel", "airbnb", "boarding", "trip", "vacation", "passport"],
    "Money": ["venmo", "zelle", "invoice", "refund", "budget", "payment", "paid", "bill"],
    "Health": ["doctor", "appointment", "sick", "medicine", "prescription", "dentist", "hospital"],
    "School": ["school", "homework", "teacher", "pickup", "practice", "tutor", "grades"],
    "Plans": ["dinner", "lunch", "reservation", "tonight", "weekend", "party", "birthday"],
}

MAX_TERMS = 60


def load() -> Dict[str, List[str]]:
    path = paths.topics_file()
    if not path.exists():
        return dict(STARTER_TOPICS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): [str(t) for t in v] for k, v in data.items() if isinstance(v, list)}
    except (ValueError, OSError):
        return dict(STARTER_TOPICS)


def _store(topics: Dict[str, List[str]]) -> None:
    paths.topics_file().write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_terms(terms: List[str]) -> List[str]:
    """Tidy a list of terms: trim, drop empties and duplicates, cap the length."""
    seen = set()
    cleaned: List[str] = []
    for term in terms:
        term = re.sub(r'["*]', "", str(term)).strip().lower()
        if term and re.search(r"\w", term) and term not in seen:
            seen.add(term)
            cleaned.append(term)
    return cleaned[:MAX_TERMS]


def save(name: str, terms: List[str]) -> Dict[str, List[str]]:
    name = (name or "").strip()
    cleaned = clean_terms(terms)
    if not name:
        raise QueryError("A topic needs a name.")
    if not cleaned:
        raise QueryError("A topic needs at least one word or phrase.")
    topics = load()
    topics[name] = cleaned
    _store(topics)
    return topics


def delete(name: str) -> Dict[str, List[str]]:
    topics = load()
    topics.pop((name or "").strip(), None)
    _store(topics)
    return topics


def to_query(terms: List[str]) -> str:
    """The search text equivalent to a topic: any of its terms."""
    parts = ['"%s"' % t if " " in t else t for t in clean_terms(terms)]
    return " OR ".join(parts)


def overview(conn: sqlite3.Connection, filters: Filters) -> List[Dict[str, object]]:
    """Every saved topic with how many messages match it under the current filters."""
    where, params = filters.where("m")
    result = []
    for name, terms in sorted(load().items()):
        query = to_query(terms)
        match = build_match(query)
        total = 0
        if match:
            total = conn.execute(
                "SELECT COUNT(*) FROM messages_fts f JOIN messages m ON m.id = f.rowid "
                "WHERE messages_fts MATCH ? AND %s" % where, [match] + params
            ).fetchone()[0]
        result.append({"name": name, "terms": terms, "query": query, "total": total})
    return result
