"""Finding and hiding automated senders: one-time codes, bank alerts, marketing and the like.

These are not people, and they crowd out the ones who are. This module decides
which one-to-one senders are automated, and marks their messages as hidden so
that every screen, the AI features and connected AI tools leave them out.

Nothing is ever deleted. Hidden messages stay in your local database, a switch
brings them back into view, and any sender can be restored with one click.

How a sender is judged (group chats are never hidden):

  Hidden automatically
    - The sender is a short code: a number of 3 to 6 digits, which only
      automated services use.
    - You have never really written to them, and at least half of what they
      sent looks automated: a verification code, a bank or card alert, opt-out
      wording such as "Reply STOP", or a "do not reply" notice. For a number or
      address that is not in your contacts, a bare web link or an order,
      delivery or appointment notice counts too.

  Suggested, for you to decide
    - You have never really written to them and they are not in your contacts.
    - You wrote to them only a few times and nearly everything they sent looks automated.

"Really written" ignores one-word replies such as STOP or YES, which is how
people answer automated texts.

You always win: a sender you pin, label as a person, or restore is never
hidden, and a sender you label as "service" is always hidden.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List

from . import labels

RULES_VERSION = "2"            # raise when the rules change, so stored results are recomputed
CANDIDATE_MAX_SENT = 5         # only senders you wrote to at most this many times are examined
MOSTLY_AUTOMATED = 0.5
NEARLY_ALL_AUTOMATED = 0.7

SHORT_CODE = re.compile(r"^\d{3,6}$")
RAW_HANDLE = re.compile(r"^(\+?[\d\s().\-]{3,}|[^@\s]+@[^@\s]+\.[^@\s]+)$")     # a phone number or an email address, not a name

# One-word answers people give to automated texts.
KEYWORD_REPLIES = {"stop", "yes", "no", "y", "n", "c", "help", "start", "unstop", "confirm", "cancel", "ok", "1", "2", "9"}

_CODE_WORD = r"(code|passcode|password|otp|pin|verification|verify|one[- ]time|security|login|log[- ]in|sign[- ]in|2fa|authenticat\w*)"
STRONG_SIGNS = [
    # a code: a code-like word near a run of 4 to 8 digits
    re.compile(r"\b%s\b.{0,80}\b\d{4,8}\b|\b\d{4,8}\b.{0,80}\b%s\b" % (_CODE_WORD, _CODE_WORD), re.I | re.S),
    # opt-out and carrier wording
    re.compile(r"\b(reply|text|txt)\s+(stop|help|yes|y|c)\b|\bstop\s*(2|to)\s*(end|cancel|quit|opt|unsub\w*|stop)\b|"
               r"msg\s*&\s*data|message and data rates|\bopt[- ]?out\b|\bunsubscribe\b", re.I),
    # an amount of money next to banking words
    re.compile(r"\$\s?\d[\d,]*(\.\d{2})?.{0,120}\b(transaction|purchase|charged?|debit|credit|card|account|acct|balance|payment|"
               r"withdrawal|deposit|transfer|spent|statement|due)\b|"
               r"\b(transaction|purchase|charged?|debit|credit|card|account|acct|balance|payment|withdrawal|deposit|transfer|spent|"
               r"statement|due)\b.{0,120}\$\s?\d[\d,]*(\.\d{2})?", re.I | re.S),
    re.compile(r"\b(do not reply|don't reply|no[- ]?reply|automated (message|text|alert)|fraud alert|account alert)\b", re.I),
]
WEAK_SIGNS = [
    re.compile(r"https?://|\bwww\.", re.I),
    re.compile(r"\byour (order|package|delivery|shipment|appointment|reservation|prescription|refill|booking|ride|driver|table)\b", re.I),
    re.compile(r"\b(has shipped|out for delivery|was delivered|is ready for pick ?up|appointment (reminder|is|on)|confirm your appointment)\b", re.I),
]


def looks_automated(text: str, strict: bool) -> bool:
    """Whether one message reads like something a system sent. `strict` uses only the strongest signs."""
    if any(sign.search(text) for sign in STRONG_SIGNS):
        return True
    return not strict and any(sign.search(text) for sign in WEAK_SIGNS)


def classify(conn: sqlite3.Connection) -> Dict[str, dict]:
    """Judge every one-to-one sender. Returns only those that are automated or suspected, by name."""
    found: Dict[str, dict] = {}
    senders = conn.execute(
        "SELECT counterpart, COUNT(*) AS n, SUM(from_me) AS sent, MAX(day) AS last_day FROM messages "
        "WHERE is_group = 0 AND kind != 'reaction' GROUP BY counterpart").fetchall()
    for sender in senders:
        name, total, sent = sender["counterpart"], sender["n"], sender["sent"] or 0
        entry = {"messages": total, "sent": sent, "last_day": sender["last_day"]}
        if SHORT_CODE.match(name.strip()):
            example = conn.execute("SELECT text FROM messages WHERE counterpart = ? AND is_group = 0 AND from_me = 0 "
                                   "AND kind = 'text' ORDER BY ts DESC LIMIT 1", (name,)).fetchone()
            found[name] = dict(entry, tier="auto", sample=(example[0] if example else "")[:160],
                               reason="A short code number, which only automated services use.")
            continue
        if sent > CANDIDATE_MAX_SENT:
            continue                                    # someone you write to: not examined at all
        in_contacts = not RAW_HANDLE.match(name.strip())
        received = automated = really_sent = 0
        sample = ""
        for from_me, text in conn.execute(
                "SELECT from_me, text FROM messages WHERE counterpart = ? AND is_group = 0 AND kind != 'reaction'", (name,)):
            if from_me:
                if (text or "").strip().lower().strip(".!") not in KEYWORD_REPLIES:
                    really_sent += 1
                continue
            received += 1
            if text and looks_automated(text, strict=in_contacts):
                automated += 1
                sample = sample or text
            elif not sample and text:
                sample = text
        share = (automated / received) if received else 0.0
        entry.update(sample=sample[:160], automated=automated, received=received)
        if really_sent == 0 and received and share >= MOSTLY_AUTOMATED:
            found[name] = dict(entry, tier="auto", reason="You never wrote to them, and %d of their %d messages look automated."
                               % (automated, received))
        elif really_sent == 0 and received and not in_contacts:
            found[name] = dict(entry, tier="suggested", reason="You never wrote to them and they are not in your contacts.")
        elif received >= 5 and share >= NEARLY_ALL_AUTOMATED:
            wrote = "once" if really_sent == 1 else "%d times" % really_sent if really_sent else "only one-word replies"
            found[name] = dict(entry, tier="suggested", reason="You wrote to them %s, but %d of their %d messages look automated."
                               % (wrote, automated, received))
    return found


def hidden_names(found: Dict[str, dict]) -> set:
    """Who ends up hidden once your own choices are applied."""
    people = labels.load()
    hidden = {name for name, entry in found.items() if entry["tier"] == "auto"}
    hidden |= {name for name, entry in people.items() if entry["label"] == "service"}
    # A pin, a label for a person, or "keep" always wins over the automatic rules.
    hidden -= {name for name, entry in people.items()
               if entry.get("keep") or entry["pinned"] or (entry["label"] and entry["label"] != "service")}
    return hidden


def apply(conn: sqlite3.Connection) -> Dict[str, object]:
    """Work out who is automated and mark their messages hidden. Needs a connection that can write."""
    found = classify(conn)
    hidden = hidden_names(found)
    conn.execute("UPDATE messages SET junk = 0 WHERE junk != 0")
    names = sorted(hidden)
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        conn.execute("UPDATE messages SET junk = 1 WHERE is_group = 0 AND counterpart IN (%s)" % ",".join("?" * len(chunk)), chunk)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('junk_report', ?)", (json.dumps(found, ensure_ascii=False),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('junk_rules', ?)", (RULES_VERSION,))
    conn.commit()
    return {"hidden_senders": len(hidden)}


def is_applied(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'junk_rules'").fetchone()
    except sqlite3.Error:
        return False
    return row is not None and row[0] == RULES_VERSION


def refresh() -> Dict[str, object]:
    """Re-apply after you hide or restore someone. Opens its own short-lived writing connection."""
    from . import index                                  # here, not at the top: index imports this module
    conn = index.connect()
    try:
        return apply(conn)
    finally:
        conn.close()


def report(conn: sqlite3.Connection) -> Dict[str, object]:
    """Everything the Hidden senders screen shows."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'junk_report'").fetchone()
    found: Dict[str, dict] = json.loads(row[0]) if row else {}
    people = labels.load()
    hidden = hidden_names(found)
    counts = {name: (n, last) for name, n, last in conn.execute(
        "SELECT counterpart, COUNT(*), MAX(day) FROM messages WHERE is_group = 0 AND kind != 'reaction' GROUP BY counterpart")}

    def row_for(name: str, reason: str) -> dict:
        entry = found.get(name, {})
        messages, last_day = counts.get(name, (entry.get("messages", 0), entry.get("last_day", "")))
        return {"name": name, "reason": reason, "messages": messages, "last_day": last_day, "sample": entry.get("sample", "")}

    hidden_rows: List[dict] = []
    for name in hidden:
        chosen = people.get(name, {}).get("label") == "service"
        hidden_rows.append(dict(row_for(name, "You marked this sender as a service." if chosen and found.get(name, {}).get("tier") != "auto"
                                         else found[name]["reason"] if name in found else "You marked this sender as a service."),
                                by_you=chosen))
    suggested = [row_for(name, entry["reason"]) for name, entry in found.items()
                 if entry["tier"] == "suggested" and name not in hidden and not people.get(name, {}).get("keep")
                 and not people.get(name, {}).get("pinned") and not people.get(name, {}).get("label")]
    kept = [row_for(name, found[name]["reason"]) for name in found if people.get(name, {}).get("keep")]
    for rows in (hidden_rows, suggested, kept):
        rows.sort(key=lambda r: (-r["messages"], r["name"]))
    return {
        "hidden": hidden_rows,
        "suggested": suggested,
        "kept": kept,
        "hidden_messages": sum(r["messages"] for r in hidden_rows),
        "definition": ("A sender is hidden automatically when it is a short code (a number of 3 to 6 digits), or when you have never "
                       "really written to it and at least half of what it sent looks automated: verification codes, bank and card "
                       "alerts, opt-out wording such as Reply STOP, or do-not-reply notices. One-word answers like STOP or YES do "
                       "not count as writing to someone. Group chats are never hidden. Nothing is deleted: hidden messages stay in "
                       "your local database and can be brought back here at any time."),
    }
