"""Search and filtering over the local database.

Every search screen, statistic, AI request and connected tool goes through the
same `Filters` object, so "messages with Avery in 2025" means exactly the same
thing everywhere in the tool.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional, Tuple

MAX_PAGE = 5000


class QueryError(Exception):
    """The search text could not be understood. The message says why, plainly."""


class Filters:
    """Which messages to look at.

    contact    exact name of a person or group chat (as shown in the People list)
    date_from  first day to include, YYYY-MM-DD
    date_to    last day to include, YYYY-MM-DD
    direction  'sent' (by you), 'received', or '' for both
    scope      'direct' (one-to-one chats), 'group', or '' for both
    """

    def __init__(self, contact: str = "", date_from: str = "", date_to: str = "",
                 direction: str = "", scope: str = "", include_reactions: bool = False):
        self.contact = (contact or "").strip()
        self.date_from = (date_from or "").strip()
        self.date_to = (date_to or "").strip()
        self.direction = (direction or "").strip()
        self.scope = (scope or "").strip()
        self.include_reactions = bool(include_reactions)
        for value in (self.date_from, self.date_to):
            if value and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                raise QueryError("Dates must look like 2025-03-31.")
        if self.direction not in ("", "sent", "received"):
            raise QueryError("Direction must be 'sent', 'received' or empty.")
        if self.scope not in ("", "direct", "group"):
            raise QueryError("Scope must be 'direct', 'group' or empty.")

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Filters":
        return cls(
            contact=str(data.get("contact") or ""),
            date_from=str(data.get("date_from") or ""),
            date_to=str(data.get("date_to") or ""),
            direction=str(data.get("direction") or ""),
            scope=str(data.get("scope") or ""),
        )

    def key(self) -> tuple:
        return (self.contact, self.date_from, self.date_to, self.direction, self.scope, self.include_reactions)

    def where(self, alias: str = "m") -> Tuple[str, list]:
        """SQL conditions and their values. Values are always passed separately, never pasted in."""
        clauses: List[str] = []
        params: list = []
        if not self.include_reactions:
            clauses.append("%s.kind != 'reaction'" % alias)
        if self.contact:
            clauses.append("%s.counterpart = ?" % alias)
            params.append(self.contact)
        if self.date_from:
            clauses.append("%s.day >= ?" % alias)
            params.append(self.date_from)
        if self.date_to:
            clauses.append("%s.day <= ?" % alias)
            params.append(self.date_to)
        if self.direction == "sent":
            clauses.append("%s.from_me = 1" % alias)
        elif self.direction == "received":
            clauses.append("%s.from_me = 0" % alias)
        if self.scope == "direct":
            clauses.append("%s.is_group = 0" % alias)
        elif self.scope == "group":
            clauses.append("%s.is_group = 1" % alias)
        return (" AND ".join(clauses) or "1=1"), params


def build_match(query: str) -> Optional[str]:
    """Turn what a person types into a safe full-text search expression.

    Supported, and nothing else:
      dinner friday        both words, anywhere in the message
      "date night"         that exact phrase
      flight OR airport    either word
      -work                exclude messages containing the word
      vacat*               words starting with "vacat"
    Returns None for an empty search (meaning: no text condition).
    """
    query = (query or "").strip()
    if not query:
        return None
    positives: List[str] = []
    negatives: List[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query):
        token = phrase or word
        if not phrase and token.upper() == "OR":
            if positives and positives[-1] != "OR":
                positives.append("OR")
            continue
        negate = not phrase and token.startswith("-") and len(token) > 1
        if negate:
            token = token[1:]
        prefix = not phrase and token.endswith("*") and len(token) > 1
        if prefix:
            token = token[:-1]
        token = token.replace('"', "").strip()
        # Skip tokens with nothing searchable in them (pure punctuation).
        if not re.search(r"\w", token, re.UNICODE):
            continue
        quoted = '"%s"%s' % (token, "*" if prefix else "")
        (negatives if negate else positives).append(quoted)
    while positives and positives[-1] == "OR":
        positives.pop()
    if not positives:
        if negatives:
            raise QueryError("A search needs at least one word to look for, not only words to exclude.")
        return None
    match = " ".join(positives)
    if negatives:
        match = "(%s) NOT (%s)" % (match, " OR ".join(negatives))
    return match


def _row(row: sqlite3.Row) -> Dict[str, object]:
    return {
        "id": row["id"], "ts": row["ts"], "day": row["day"], "hour": row["hour"],
        "from_me": bool(row["from_me"]), "sender": row["sender"], "counterpart": row["counterpart"],
        "is_group": bool(row["is_group"]), "service": row["service"], "kind": row["kind"],
        "has_attachment": bool(row["has_attachment"]), "text": row["text"],
    }


def search(conn: sqlite3.Connection, query: str, filters: Filters, limit: int = 50,
           offset: int = 0, sort: str = "newest") -> Dict[str, object]:
    """Find messages. With no search text this simply browses the filtered messages."""
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))
    match = build_match(query)
    where, params = filters.where("m")
    if match:
        source = "messages_fts f JOIN messages m ON m.id = f.rowid"
        where = "messages_fts MATCH ? AND " + where
        params = [match] + params
        order = {"oldest": "m.ts ASC", "relevance": "bm25(messages_fts), m.ts DESC"}.get(sort, "m.ts DESC")
    else:
        source = "messages m"
        order = "m.ts ASC" if sort == "oldest" else "m.ts DESC"
    try:
        total = conn.execute("SELECT COUNT(*) FROM %s WHERE %s" % (source, where), params).fetchone()[0]
        rows = conn.execute(
            "SELECT m.* FROM %s WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (source, where, order),
            params + [limit, offset],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise QueryError("That search could not be run (%s). Try simpler words." % exc)
    return {"total": total, "offset": offset, "results": [_row(r) for r in rows]}


def context(conn: sqlite3.Connection, message_id: int, before: int = 12, after: int = 12) -> Dict[str, object]:
    """The messages around one message, in the same conversation."""
    before = max(0, min(int(before), 100))
    after = max(0, min(int(after), 100))
    target = conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)).fetchone()
    if target is None:
        raise QueryError("That message was not found.")
    same_chat = "counterpart = ? AND is_group = ? AND kind != 'reaction'"
    args = (target["counterpart"], target["is_group"])
    earlier = conn.execute(
        "SELECT * FROM messages WHERE %s AND (ts < ? OR (ts = ? AND id < ?)) ORDER BY ts DESC, id DESC LIMIT ?"
        % same_chat, args + (target["ts"], target["ts"], target["id"], before),
    ).fetchall()
    later = conn.execute(
        "SELECT * FROM messages WHERE %s AND (ts > ? OR (ts = ? AND id > ?)) ORDER BY ts ASC, id ASC LIMIT ?"
        % same_chat, args + (target["ts"], target["ts"], target["id"], after),
    ).fetchall()
    rows = list(reversed(earlier)) + [target] + list(later)
    return {"target_id": target["id"], "counterpart": target["counterpart"], "messages": [_row(r) for r in rows]}


def contacts(conn: sqlite3.Connection, limit: int = 2000) -> List[Dict[str, object]]:
    """Everyone and every group you have messages with, busiest first."""
    rows = conn.execute(
        "SELECT counterpart, is_group, COUNT(*) AS total FROM messages WHERE kind != 'reaction' "
        "GROUP BY counterpart, is_group ORDER BY total DESC LIMIT ?", (int(limit),)
    ).fetchall()
    return [{"name": r["counterpart"], "is_group": bool(r["is_group"]), "total": r["total"]} for r in rows]
