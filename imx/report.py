"""A report to read offline: everything the app has worked out, in one file.

The file is a single web page with its styles and charts inside it. It needs no
internet and no scripts, opens in any browser, and prints to PDF with each section
on a new page. It is made on this Mac at no cost; nothing is sent anywhere.

Evidence: points that rest on numbered stretches of conversation link to an
appendix giving each stretch's dates, feel and digest summary. No raw messages
are written into the file.

Every piece of text, whether written by you, by the people you talk to, or by a
model, is escaped before it goes into the page, so none of it can act as code.
"""

from __future__ import annotations

import html
import math
import sqlite3
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__, check, histories, plans, portrait, reading, stats
from .query import Filters

SECTIONS: List[Tuple[str, str]] = [
    ("numbers", "Key numbers"),
    ("portrait", "Portrait"),
    ("traits", "Traits"),
    ("overtime", "Over time"),
    ("partner", "Plan with your partner"),
    ("monthly", "Monthly check"),
    ("decade", "The next decade"),
    ("histories", "Relationship histories"),
    ("accuracy", "Accuracy check"),
    ("evidence", "Evidence"),
]
SECTION_KEYS = [key for key, _title in SECTIONS]
FLAGGED_SHOWN = 60

# Colours for the charts, the same as the app's light theme.
SERIES = "#2a78d6"
SERIES_2 = "#eb6834"
GRID = "#d8d6cf"
INK = "#1c1b19"
MUTED = "#6b6860"
FEEL_COLOURS = {"warm": "#1c5cab", "friendly": "#6da7ec", "neutral": "#cfcdc5", "mixed": "#9d9b93",
                "cool": "#f2aaa6", "tense": "#e34948", "hostile": "#9c2a2a"}
RESOLUTION_COLOURS = {"resolved": SERIES, "partly resolved": "#86b6ef", "unresolved": "#e34948", "unclear": "#b5b3ab"}


def e(value: object) -> str:
    """Text made safe for the page."""
    return html.escape("" if value is None else str(value), quote=True)


def _tag(text: str, kind: str = "") -> str:
    return '<span class="tag %s">%s</span>' % (e(kind), e(text)) if text else ""


def _para(title: str, text: str) -> str:
    return "<h4>%s</h4><p>%s</p>" % (e(title), e(text)) if text else ""


def _list(items: Sequence[str]) -> str:
    items = [item for item in items if item]
    return "<ul>%s</ul>" % "".join("<li>%s</li>" % e(item) for item in items) if items else ""


def _table(head: Sequence[str], rows: Sequence[Sequence[str]], numeric: Sequence[int] = ()) -> str:
    """A table. Cells are already-safe HTML."""
    cls = lambda i: ' class="n"' if i in numeric else ""
    return ('<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % ("".join("<th%s>%s</th>" % (cls(i), e(h)) for i, h in enumerate(head)),
               "".join("<tr>%s</tr>" % "".join("<td%s>%s</td>" % (cls(i), cell) for i, cell in enumerate(row)) for row in rows)))


def _note(text: str) -> str:
    return '<p class="note">%s</p>' % e(text)


def _fmt(n: object) -> str:
    return "{:,}".format(n) if isinstance(n, int) else ("–" if n is None else str(n))


# ---- charts, drawn as SVG inside the page ---------------------------------------------------------------------

SHORT_TRAITS = {"warmth": "Warmth", "reliability": "Reliable", "initiative": "Initiates", "directness": "Direct", "humour": "Humour",
                "openness": "Open", "conflict": "Conflict", "generosity": "Generous", "curiosity": "Curious", "follow_through": "Follows up"}


def radar(names: List[Tuple[str, str]], traits: List[dict], size: int = 300, compact: bool = False) -> str:
    """A spider chart of trait scores, 0 at the centre and 10 at the rim, with room either side for the labels."""
    by_key = {t["trait"]: t for t in traits}
    side = 56 if compact else 120                       # room for labels to the left and right
    pad = 22 if compact else 30
    radius, cy = size / 2 - pad, size / 2
    width = size + 2 * side
    cx = width / 2
    centre = cx

    def at(i: int, value: float) -> Tuple[float, float]:
        angle = -math.pi / 2 + 2 * math.pi * i / len(names)
        return cx + math.cos(angle) * radius * value / 10, cy + math.sin(angle) * radius * value / 10

    parts = ['<svg class="radar" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">'
             % (width, size, width, size, e("Trait scores: " + ", ".join("%s %s" % (name, by_key[key]["score"] if key in by_key else "not scored")
                                                                        for key, name in names)))]
    for ring in (2, 4, 6, 8, 10):
        parts.append('<polygon points="%s" fill="none" stroke="%s" stroke-width="1"/>'
                     % (" ".join("%.1f,%.1f" % at(i, ring) for i in range(len(names))), GRID))
    for i, (key, name) in enumerate(names):
        x, y = at(i, 10)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>' % (cx, cy, x, y, GRID))
        lx, ly = at(i, 11.3)
        anchor = "middle" if abs(lx - centre) < 6 else ("start" if lx > centre else "end")
        label = SHORT_TRAITS.get(key, name) if compact else name
        parts.append('<text x="%.1f" y="%.1f" text-anchor="%s" dominant-baseline="middle" font-size="%d" fill="%s">%s</text>'
                     % (lx, ly, anchor, 10 if compact else 12, MUTED, e(label)))
    scored = [(i, by_key[key]) for i, (key, _name) in enumerate(names) if key in by_key]
    if len(scored) > 2:
        parts.append('<polygon points="%s" fill="%s" fill-opacity="0.22" stroke="%s" stroke-width="2"/>'
                     % (" ".join("%.1f,%.1f" % at(i, by_key[key]["score"] if key in by_key else 0) for i, (key, _n) in enumerate(names)), SERIES, SERIES))
    for i, t in scored:
        x, y = at(i, t["score"])
        hollow = t.get("confidence") == "low"
        parts.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s" stroke="%s" stroke-width="2"/>'
                     % (x, y, 3 if compact else 4.5, "#ffffff" if hollow else SERIES, SERIES))
    parts.append("</svg>")
    return "".join(parts)


def _legend(entries: Sequence[Tuple[str, str]]) -> str:
    return '<div class="legend">%s</div>' % "".join('<span><i style="background:%s"></i>%s</span>' % (colour, e(label)) for colour, label in entries)


def feel_bars(rows: List[dict], label_key: str) -> str:
    """Share of messages by how the conversation felt, lined up on the centre of mixed and neutral together."""
    order = ["hostile", "tense", "cool", "mixed", "neutral", "friendly", "warm"]
    shaped = []
    for row in rows:
        total = sum(row.get(k, 0) for k in order) or 1
        share = lambda keys: sum(row.get(k, 0) for k in keys) / total
        shaped.append((row, total, share(["hostile", "tense", "cool"]) + share(["mixed", "neutral"]) / 2))
    if not shaped:
        return _note("Nothing to chart.")
    max_left = max(left for _r, _t, left in shaped) or 0.01
    max_right = max(1 - left for _r, _t, left in shaped) or 0.01
    label_w, bar_w, value_w, row_h = 150, 420, 70, 20
    width, height = label_w + bar_w + value_w, row_h * len(shaped) + 6
    span = max_left + max_right
    parts = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="How conversations felt">' % (width, height)]
    for n, (row, total, left) in enumerate(shaped):
        y = 3 + n * row_h
        parts.append('<text x="0" y="%d" font-size="11" fill="%s" dominant-baseline="middle">%s</text>' % (y + 7, INK, e(str(row.get(label_key, ""))[:24])))
        x = label_w + bar_w * (max_left - left) / span
        for key in order:
            w = bar_w * row.get(key, 0) / total / span
            if w > 0:
                parts.append('<rect x="%.1f" y="%d" width="%.1f" height="14" fill="%s"><title>%s</title></rect>'
                             % (x, y, max(w - 1, 0.5), FEEL_COLOURS[key], e("%s: %s %d%%" % (row.get(label_key, ""), key, round(100 * row.get(key, 0) / total)))))
                x += w
        warm = round(100 * (row.get("warm", 0) + row.get("friendly", 0)) / total)
        parts.append('<text x="%d" y="%d" font-size="11" fill="%s" text-anchor="end" dominant-baseline="middle">%d%% warm</text>' % (width, y + 7, MUTED, warm))
    centre = label_w + bar_w * max_left / span
    parts.append('<line x1="%.1f" y1="0" x2="%.1f" y2="%d" stroke="%s" stroke-width="1"/>' % (centre, centre, height, MUTED))
    parts.append("</svg>")
    return '<div class="chart">%s%s</div>' % (_legend([(FEEL_COLOURS[k], k.capitalize()) for k in order]), "".join(parts))


def stacked_columns(rows: List[dict], parts_spec: List[Tuple[str, str, str]]) -> str:
    """One column per year, stacked from a few named parts."""
    if not rows:
        return _note("Nothing to chart.")
    most = max(sum(row.get(key, 0) for key, _l, _c in parts_spec) for row in rows) or 1
    col_w, gap, chart_h, top, bottom = 26, 12, 150, 16, 18
    width = len(rows) * (col_w + gap) + gap
    parts = ['<svg viewBox="0 0 %d %d" width="%d" style="max-width:100%%" role="img" aria-label="Columns by year">' % (width, chart_h + top + bottom, width)]
    for n, row in enumerate(rows):
        x = gap + n * (col_w + gap)
        y = top + chart_h
        total = 0
        for key, label, colour in parts_spec:
            value = row.get(key, 0)
            if not value:
                continue
            h_ = chart_h * value / most
            y -= h_
            parts.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" fill="%s"><title>%s</title></rect>'
                         % (x, y, col_w, max(h_ - 1, 0.5), colour, e("%s: %s %s" % (row.get("year", ""), value, label.lower()))))
            total += value
        if total:
            parts.append('<text x="%d" y="%.1f" font-size="10" fill="%s" text-anchor="middle">%d</text>' % (x + col_w / 2, y - 4, MUTED, total))
        parts.append('<text x="%d" y="%d" font-size="10" fill="%s" text-anchor="middle">%s</text>' % (x + col_w / 2, top + chart_h + 13, MUTED, e(row.get("year", ""))))
    parts.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="%s"/></svg>' % (top + chart_h, width, top + chart_h, GRID))
    return '<div class="chart">%s%s</div>' % (_legend([(colour, label) for _k, label, colour in parts_spec]), "".join(parts))


def ranked_bars(items: List[dict]) -> str:
    if not items:
        return _note("None recorded.")
    most = max(item["count"] for item in items) or 1
    return '<div class="bars">%s</div>' % "".join(
        '<div class="bar-row"><span class="bar-name">%s</span><span class="bar-track"><i style="width:%.1f%%"></i></span><span class="n">%s</span></div>'
        % (e(item["name"]), 100.0 * item["count"] / most, _fmt(item["count"])) for item in items)


def month_bars(months: List[dict], key: str, baseline: Optional[float], base_from: str, base_to: str, recent_from: str) -> str:
    values = [m[key] for m in months if m.get(key) is not None]
    if not months:
        return ""
    most = max(values + [baseline or 0, 0.001])
    width, height = 300, 80
    slot = width / len(months)
    parts = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="By month">' % (width, height + 14)]
    for n, m in enumerate(months):
        value = m.get(key)
        if value is None:
            continue
        h_ = max(1.0, height * value / most)
        opacity = 1.0 if recent_from and m["month"] >= recent_from else (0.3 if base_from <= m["month"] <= base_to else 0.55)
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" fill-opacity="%.2f"><title>%s</title></rect>'
                     % (n * slot + 0.5, height - h_, max(slot - 1.5, 0.5), h_, SERIES, opacity, e("%s: %s" % (m["month"], value))))
    if baseline is not None:
        y = height - height * baseline / most
        parts.append('<line x1="0" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.5" stroke-dasharray="4 3"/>' % (y, width, y, INK))
    parts.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (height, width, height, GRID))
    parts.append('<text x="0" y="%d" font-size="9" fill="%s">%s</text><text x="%d" y="%d" font-size="9" fill="%s" text-anchor="end">%s</text></svg>'
                 % (height + 12, MUTED, e(months[0]["month"]), width, height + 12, MUTED, e(months[-1]["month"])))
    return "".join(parts)


# ---- evidence: digest numbers link to the appendix ------------------------------------------------------------

class Evidence:
    """Collects the digests cited anywhere in the report, per person, for the appendix."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.cited: Dict[Tuple[str, bool], set] = {}
        self.anchor_ids: Dict[Tuple[str, bool], str] = {}

    def _anchor(self, person: str, is_group: bool) -> str:
        key = (person, is_group)
        if key not in self.anchor_ids:
            self.anchor_ids[key] = "p%d" % (len(self.anchor_ids) + 1)
        return self.anchor_ids[key]

    def refs(self, person: str, is_group: bool, numbers: Sequence[int]) -> str:
        numbers = sorted({int(n) for n in numbers or [] if isinstance(n, int)})
        if not numbers:
            return ""
        if not self.enabled:
            return '<p class="refs">Evidence: %s</p>' % ", ".join("#%d" % n for n in numbers)
        self.cited.setdefault((person, is_group), set()).update(numbers)
        anchor = self._anchor(person, is_group)
        return '<p class="refs">Evidence: %s</p>' % ", ".join('<a href="#ev-%s-%d">#%d</a>' % (anchor, n, n) for n in numbers)

    def appendix(self) -> str:
        if not self.cited:
            return _note("No evidence was cited in the sections included.")
        out = ['<p>Each entry is one stretch of conversation as its digest describes it: the dates, how many messages, how it felt, '
               'and the summary a model wrote. The messages themselves are not in this file; search for the dates in the app to read them.</p>']
        for (person, is_group), numbers in sorted(self.cited.items(), key=lambda kv: kv[0][0].lower()):
            anchor = self._anchor(person, is_group)
            found = {d["n"]: d for d in histories._digests(person, is_group)}
            out.append("<h3>%s%s</h3>" % (e(person), " (group)" if is_group else ""))
            for n in sorted(numbers):
                d = found.get(n)
                if d is None:
                    out.append('<div class="ev" id="ev-%s-%d"><strong>#%d</strong> no longer found.</div>' % (anchor, n, n))
                    continue
                g = d["digest"]
                first, last = _day(d["first_ts"]), _day(d["last_ts"])
                tensions = "; ".join("%s (%s)" % (t.get("what", ""), t.get("resolution", "")) for t in g.get("tensions", []))
                out.append('<div class="ev" id="ev-%s-%d"><p><strong>#%d</strong> %s, %s messages, feel: %s</p><p>%s</p>%s</div>'
                           % (anchor, n, n, e(first if first == last else first + " to " + last), _fmt(d["messages"]), e(g.get("closeness", "")),
                              e(g.get("summary", "")), '<p class="small">Friction: %s</p>' % e(tensions) if tensions else ""))
        return "".join(out)


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---- the sections ---------------------------------------------------------------------------------------------

def _numbers(conn: sqlite3.Connection, _ev: Evidence) -> str:
    people = stats.people(conn, Filters())
    totals = people["totals"]
    measured = portrait.measured(conn)
    cards = [("messages", totals.get("messages")), ("people", totals.get("people")), ("groups", totals.get("groups"))]
    out = ['<div class="cards">%s</div>' % "".join('<div class="card"><b>%s</b><span>%s</span></div>' % (_fmt(v), e(label)) for label, v in cards),
           "<p>From %s to %s.</p>" % (e(totals.get("first_day", "")), e(totals.get("last_day", "")))]
    rows = []
    for row in [r for r in people["rows"] if not r.get("is_group")][:30]:
        m = measured.get(row["name"], {})
        rows.append([e(row["name"]), _fmt(row.get("total")), _fmt(m.get("conversations_started_by_me_percent")) + ("%" if m.get("conversations_started_by_me_percent") is not None else ""),
                     _fmt(m.get("words_written_by_me_percent")) + ("%" if m.get("words_written_by_me_percent") is not None else ""),
                     _fmt(m.get("my_typical_reply_minutes")), _fmt(m.get("their_typical_reply_minutes")),
                     _fmt(m.get("their_questions_i_left_unanswered"))])
    out.append("<h3>The people you text most</h3>")
    out.append(_table(["Person", "Messages", "Started by you", "Words by you", "Your reply (min)", "Their reply (min)", "Their questions you left"],
                      rows, numeric=(1, 2, 3, 4, 5, 6)))
    out.append(_note("Counted from the messages, one-to-one conversations only. A gap of 6 hours starts a new conversation. Reply times are medians."))
    with reading._store_lock:
        store = portrait._store()
        try:
            spend = store.execute("SELECT purpose, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS usd FROM ledger GROUP BY purpose ORDER BY usd DESC").fetchall()
        finally:
            store.close()
    if spend:
        names = {"digest": "Reading pilot", "check": "Pilot checks", "full": "Full read", "accuracy": "Accuracy check", "history": "Histories",
                 "portrait": "Portrait", "plan": "Plans"}
        out.append("<h3>What the reading cost</h3>")
        out.append(_table(["Part", "Requests", "Cost"], [[e(names.get(r["purpose"], r["purpose"])), _fmt(r["n"]), "$%.2f" % r["usd"]] for r in spend]
                          + [["<strong>Total</strong>", "", "<strong>$%.2f</strong>" % sum(r["usd"] for r in spend)]], numeric=(1, 2)))
    return "".join(out)


def _portrait(_conn: sqlite3.Connection, _ev: Evidence) -> str:
    found = portrait.read()
    g = found.get("portrait")
    if not g:
        return _note("The portrait has not been written yet.")
    items = lambda title, key, head, body: ("<h3>%s</h3>" % e(title) + "".join(
        '<div class="claim"><h4>%s</h4><p>%s</p>%s</div>' % (e(item.get(head, "")), e(item.get(body, "")),
                                                              '<p class="small">%s</p>' % e(", ".join(item.get("people", []))) if item.get("people") else "")
        for item in g.get(key, []))) if g.get(key) else ""
    quotes = "".join('<blockquote class="%s">%s</blockquote><p class="small">%s%s. %s %s</p>'
                     % ("" if q.get("found") else "unverified", e(q.get("text", "")), e("To " + q["to"] if q.get("to") else ""),
                        e(", " + q["date"] if q.get("date") else ""), e(q.get("why", "")),
                        _tag("found word for word", "good") if q.get("found") else _tag("not found word for word", "warn") + " may be paraphrased or invented")
                     for q in g.get("quotes", []))
    return "".join([
        '<p class="lead">%s</p><p>%s</p>' % (e(g.get("headline")), e(g.get("in_brief"))),
        _note("Written by %s on %s. A model's reading, three steps from what was said; it can only describe what was texted." % (found["written_by"], found["written_at"])),
        items("Who you are", "who_you_are", "title", "text"), items("In each role", "roles", "role", "text"),
        items("What you evidently care about", "values", "value", "text"),
        _para("How you communicate", g.get("how_you_communicate")), _para("Under pressure", g.get("under_pressure")),
        items("Strengths", "strengths", "what", "text"), items("Blind spots", "blind_spots", "what", "text"),
        items("Contradictions", "contradictions", "what", "text"), items("How you have changed", "how_you_have_changed", "period", "text"),
        _para("What people get from you", g.get("what_people_get_from_you")), _para("What you ask of people", g.get("what_you_ask_of_people")),
        "<h3>In your own words</h3>" + quotes if quotes else "",
        "<h3>Questions only you can answer</h3>" + _list(g.get("questions_worth_asking", [])) if g.get("questions_worth_asking") else "",
        _para("What the messages cannot show", g.get("what_messages_cannot_show")),
    ])


def _traits(_conn: sqlite3.Connection, _ev: Evidence) -> str:
    found = portrait.read()
    names = [(t["key"], t["name"]) for t in found["trait_names"]]
    meaning = {t["key"]: t["meaning"] for t in found["trait_names"]}
    g = found.get("portrait")
    if not g and not found["per_person"]:
        return _note("No trait scores yet. They are written with the portrait.")
    out = [_note("Each score from 0 to 10 is a model's judgement with a reason attached, not a measurement. 5 is unremarkable. "
                 "Hollow dots mark low confidence.")]
    if g:
        by_key = {t["trait"]: t for t in g.get("traits", [])}
        out.append("<h3>Across everyone</h3>")
        out.append('<div class="radar-pair">%s<div>%s</div></div>' % (radar(names, g.get("traits", [])), _table(
            ["Trait", "Score", "Confidence", "Why"],
            [["<strong>%s</strong><br><span class=\"small\">%s</span>" % (e(name), e(meaning[key])),
              _fmt(by_key[key]["score"]) if key in by_key else "–", e(by_key[key]["confidence"]) if key in by_key else "",
              e(by_key[key]["why"]) if key in by_key else "Not scored."] for key, name in names], numeric=(1,))))
    if found["per_person"]:
        out.append("<h3>Person by person</h3>")
        out.append('<div class="radar-grid">%s</div>' % "".join(
            '<div class="radar-cell"><strong>%s</strong>%s<p class="small">%s</p></div>' % (e(p["name"]), radar(names, p["traits"], 170, True), e(p["in_a_sentence"]))
            for p in found["per_person"]))
        rows = []
        for p in found["per_person"]:
            by_key = {t["trait"]: t for t in p["traits"]}
            rows.append([e(p["name"])] + ['<span class="heat h%d">%s</span>' % (min(5, by_key[key]["score"] // 2), by_key[key]["score"]) if key in by_key else "–"
                                         for key, _name in names])
        out.append(_table(["With"] + [name for _key, name in names], rows, numeric=tuple(range(1, len(names) + 1))))
    return "".join(out)


def _overtime(_conn: sqlite3.Connection, _ev: Evidence) -> str:
    d = portrait.charts()
    if not d["digests"]:
        return _note("Nothing to chart yet: these charts are counted from the digests of the full read.")
    resolutions = [("resolved", "Resolved", RESOLUTION_COLOURS["resolved"]), ("partly resolved", "Partly resolved", RESOLUTION_COLOURS["partly resolved"]),
                   ("unresolved", "Unresolved", RESOLUTION_COLOURS["unresolved"]), ("unclear", "Unclear", RESOLUTION_COLOURS["unclear"])]
    return "".join([
        _note("Counted from %s digests. Each digest records how its stretch of conversation felt; charts are weighted by messages." % _fmt(d["digests"])),
        "<h3>How your conversations felt, year by year</h3>", feel_bars(d["feel_by_year"], "year"),
        "<h3>Friction, and how it ended</h3>", stacked_columns(d["friction_by_year"], resolutions),
        "<h3>Promises made</h3>", stacked_columns(d["promises_by_year"], [("me", "By you", SERIES), ("them", "By them", SERIES_2)]),
        "<h3>How it feels, person by person</h3>", feel_bars(d["feel_by_person"], "name"),
        '<div class="pair"><div><h3>What you talk about</h3>%s</div><div><h3>People who come up</h3>%s<h3>Places that come up</h3>%s</div></div>'
        % (ranked_bars(d["topics"][:20]), ranked_bars(d["people"][:12]), ranked_bars(d["places"][:12])),
    ])


def _partner(_conn: sqlite3.Connection, ev: Evidence) -> str:
    found = plans.read("partner")
    g = found.get("body")
    if not g:
        return _note("The plan has not been written yet.")
    name = found["label"]
    refs = lambda item: ev.refs(name, False, item.get("refs", []))
    blocks = lambda title, key, render: ("<h3>%s</h3>" % e(title) + "".join('<div class="claim">%s%s</div>' % (render(item), refs(item)) for item in g.get(key, []))) if g.get(key) else ""
    actions = "".join('<div class="action"><h4>%d. %s</h4><p><strong>When:</strong> %s</p><p><strong>Why:</strong> %s</p><p><strong>How you will know:</strong> %s</p></div>'
                      % (i, e(a["action"]), e(a["when"]), e(a["why"]), e(a["how_you_will_know"])) for i, a in enumerate(g.get("first_month", []), start=1))
    research = _table(["Idea", "What it is", "Evidence"], [[e(r["name"]), e(r["what"]), _tag(r["strength"])] for r in found["research"]])
    return "".join([
        '<p class="lead">%s</p>' % e(g.get("in_brief")),
        _note("You and %s. Written by %s on %s, for you alone. A model's reading of digests and histories; test each point against what you know." % (name, found["written_by"], found["written_at"])),
        "<h3>Start here: the first month</h3>" + actions if actions else "",
        blocks("When this happens", "moments", lambda m: "<h4>%s</h4><p><strong>You tend to:</strong> %s</p><p><strong>Try instead:</strong> %s</p>%s"
               % (e(m["when"]), e(m["you_tend_to"]), e(m["try_instead"]), "<blockquote>%s</blockquote>" % e(m["words_that_work"]) if m.get("words_that_work") else "")),
        blocks("What is working", "what_is_working", lambda w: "<p>%s</p>" % e(w["what"])),
        blocks("What %s has asked for" % name, "what_they_have_asked_for", lambda a: '<h4>%s</h4><p class="small">%s</p>' % (e(a["ask"]), e(a["how_often"]))),
        blocks("Patterns, and what research says", "patterns", lambda p: "<h4>%s %s %s</h4><p>%s</p><p class=\"small\"><strong>What it costs:</strong> %s</p>"
               % (e(p["name"]), _tag(p.get("idea", "")), _tag(p.get("strength", "")), e(p["what_happens"]), e(p["cost"]))),
        blocks("What to stop", "what_to_stop", lambda s: "<h4>%s</h4><p>%s</p>" % (e(s["what"]), e(s["why"]))),
        "<h3>To talk about together</h3>" + _list(g.get("to_talk_about", [])) if g.get("to_talk_about") else "",
        _para("When this needs more than a plan", g.get("get_help_if")),
        "<h3>The research behind it</h3>" + research,
        _para("What the messages cannot show", g.get("what_messages_cannot_show")),
    ])


def _monthly(conn: sqlite3.Connection, _ev: Evidence) -> str:
    d = plans.measures(conn)
    if not d.get("partner") or not d.get("months"):
        return _note("No monthly check yet: choose who the plan is about in the app.")
    shown = [m for m in d["measures"] if m["label"]]
    value = lambda key, v: "–" if v is None else ("%d%%" % round(v) if key in ("started_by_me", "tapback_share") else ("%s min" % v if key == "reply_minutes" else "%s per 100" % v))
    months = d["months"][-24:]
    base, recent = d["baseline"], d["recent"]
    cells = "".join('<div class="mini"><h4>%s</h4><p class="small">%s Starting point: %s.</p>%s<p class="small">%s.</p></div>'
                    % (e(m["label"]), e(("%s to %s: %s." % (recent["from"], recent["to"], value(m["key"], recent["values"][m["key"]]))) if recent["from"] else "No finished month since the plan yet."),
                       e(value(m["key"], base["values"][m["key"]])), month_bars(months, m["key"], base["values"][m["key"]], base["from"], base["to"], recent["from"]), e(m["what"]))
                    for m in shown)
    table = _table(["Month", "Your messages"] + [m["label"] for m in shown],
                   [["<strong>Starting point</strong>", ""] + ["<strong>%s</strong>" % e(value(m["key"], base["values"][m["key"]])) for m in shown]]
                   + [[e(row["month"]), _fmt(row["my_messages"])] + [e(value(m["key"], row[m["key"]])) for m in shown] for row in reversed(months)],
                   numeric=tuple(range(1, len(shown) + 2)))
    readings = []
    for entry in plans.months_read():
        g = entry["body"]
        readings.append("<h3>Read: %s</h3><p>%s</p>" % (e(entry["month"]), e(g.get("in_brief"))))
        if g.get("bids"):
            readings.append(_table(["Date", "They reached out", "Your reply", ""], [[e(b["date"]), e(b["what_they_said"]),
                                   e(b["my_reply"]) + ("" if b.get("found") or not b["my_reply"] else " " + _tag("not found word for word", "warn")), _tag(b["response"])] for b in g["bids"]]))
        readings.append("".join('<p><strong>%s:</strong> %s %s%s</p>' % (e(l["date"]), e(l["what_was_shared"]), _tag(l["what_i_did"]),
                                                                       ' <span class="small">Better: %s</span>' % e(l["better"]) if l.get("better") else "") for l in g.get("listening", [])))
        readings.append("".join('<p><strong>%s</strong> %s <span class="small">%s</span></p>' % (e(a["action"]), _tag(a["seen"]), e(a["what_shows_it"])) for a in g.get("actions", [])))
        readings.append("<p><strong>One thing for next month:</strong> %s</p>" % e(g.get("one_thing")))
    phrases = next((m["what"] for m in d["measures"] if m["key"] == "_phrases"), "")
    return "".join([
        _note("You and %s. Counted from the messages, at no cost. Only finished months count. The starting point (%s to %s) is the dashed line, with those months lighter; "
              "the months compared with it are darkest. A single month is noisy. %s" % (d["partner"], base["from"], base["to"], phrases)),
        '<div class="minis">%s</div>' % cells, "<h3>The same numbers as a table</h3>", table, "".join(readings)])


def _decade(_conn: sqlite3.Connection, _ev: Evidence) -> str:
    found = plans.read("decade")
    g = found.get("body")
    if not g:
        return _note("The reality check has not been written yet.")
    kinds = {"supports": "good", "contradicts": "warn", "mixed": "", "cannot see": ""}
    return "".join([
        '<p class="lead">%s</p>' % e(g.get("in_brief")),
        _note("Written by %s on %s from your goals, your portrait, the notes on each year and the relationship histories. Health, money and most of work are invisible in texts."
              % (found["written_by"], found["written_at"])),
        "<h3>The first ninety days</h3>" + "".join('<div class="action"><h4>%d. %s</h4><p><strong>When:</strong> %s</p><p><strong>Why:</strong> %s</p></div>'
                                                   % (i, e(a["move"]), e(a["when"]), e(a["why"])) for i, a in enumerate(g.get("first_90_days", []), start=1)),
        "<h3>Your goals, against the evidence</h3>" + _table(["Goal", "Area", "The messages", "What they show"],
                                                              [[e(goal["goal"]) + (" " + _tag("at risk", "warn") if goal.get("at_risk") else ""), e(goal["area"]),
                                                                _tag(goal["evidence"], kinds.get(goal["evidence"], "")), e(goal["what_messages_show"])] for goal in g.get("goals", [])]),
        "<h3>Most at risk</h3>" + "".join("<h4>%s</h4><p>%s</p>" % (e(r["goal"]), e(r["why"])) for r in g.get("most_at_risk", [])) if g.get("most_at_risk") else "",
        "<h3>Where goals pull against each other</h3>" + "".join("<h4>%s</h4><p>%s</p>" % (e(t["between"]), e(t["how_to_hold"])) for t in g.get("tensions", [])) if g.get("tensions") else "",
        "<h3>Patterns to watch</h3>" + "".join('<h4>%s</h4><p>%s</p><p class="small"><strong>Early warning:</strong> %s</p>' % (e(p["pattern"]), e(p["how_it_shows"]), e(p["early_warning"]))
                                               for p in g.get("patterns_to_watch", [])) if g.get("patterns_to_watch") else "",
        "<h3>Milestones</h3><ol>%s</ol>" % "".join("<li><strong>%s</strong> %s</li>" % (e(m["when"]), e(m["what"])) for m in g.get("milestones", [])) if g.get("milestones") else "",
        _para("What the messages cannot show", g.get("what_messages_cannot_show")),
    ])


def _history(name: str, is_group: bool, ev: Evidence) -> str:
    found = histories.read(name, is_group)
    g = found["history"]
    refs = lambda item: ev.refs(name, is_group, item.get("refs", []))
    blocks = lambda title, key, render: ("<h4>%s</h4>" % e(title) + "".join('<div class="claim">%s%s</div>' % (render(item), refs(item)) for item in g.get(key, []))) if g.get(key) else ""
    pair = lambda a, b: '<div class="pair"><div>%s</div><div>%s</div></div>' % (_para(a, g.get(_KEYS[a])), _para(b, g.get(_KEYS[b])))
    return "".join([
        '<h2 class="person">%s%s</h2><p class="lead">%s</p>' % (e(name), " (group)" if is_group else "", e(g.get("headline"))),
        _note("Written by %s on %s from %s digests, %s to %s." % (found["written_by"], found["written_at"], _fmt(found["digests"]), found["first_day"], found["last_day"])),
        _para("How it began", g.get("how_it_began", {}).get("text", "")), refs(g.get("how_it_began", {})),
        blocks("Eras", "eras", lambda x: "<p><strong>%s</strong>%s</p><p>%s</p>" % (e(x["title"]), e(" (%s)" % x["period"]) if x.get("period") else "", e(x["text"]))),
        blocks("Themes", "themes", lambda x: "<p><strong>%s</strong>: %s</p>" % (e(x["theme"]), e(x["text"]))),
        blocks("Friction and repair", "friction_and_repair", lambda x: "<p><strong>%s</strong>: %s</p>" % (e(x["what"]), e(x["how_it_went"]))),
        pair("What I give", "What I ask for"), pair("What they give", "What they ask for"),
        blocks("Turning points", "turning_points", lambda x: "<p><strong>%s</strong> %s</p>" % (e(x["date"]), e(x["what"]))),
        blocks("Strengths", "strengths", lambda x: "<p>%s</p>" % e(x["what"])),
        blocks("Hard truths", "hard_truths", lambda x: "<p>%s</p>" % e(x["what"])),
        blocks("Unresolved", "unresolved", lambda x: "<p>%s</p>" % e(x["what"])),
        _para("What the messages cannot show", g.get("what_messages_cannot_show")),
    ])


_KEYS = {"What I give": "what_i_give", "What I ask for": "what_i_ask_for", "What they give": "what_they_give", "What they ask for": "what_they_ask_for"}


def _histories(_conn: sqlite3.Connection, ev: Evidence) -> str:
    rows = histories.overview()["histories"]
    if not rows:
        return _note("No relationship histories have been written yet.")
    rows.sort(key=lambda r: (r["is_group"], -r["digests"]))
    contents = "<ol>%s</ol>" % "".join("<li>%s%s: %s</li>" % (e(r["name"]), " (group)" if r["is_group"] else "", e(r["headline"])) for r in rows)
    return contents + "".join('<div class="history">%s</div>' % _history(r["name"], r["is_group"], ev) for r in rows)


def _accuracy(_conn: sqlite3.Connection, _ev: Evidence) -> str:
    d = check.summary()
    if not d["checked"]:
        return _note("The accuracy check has not been run.")
    rows = [[e(m["name"]), _fmt(m["checked"]), "%s (%d%%)" % (_fmt(m["clean"]), round(100 * m["clean"] / m["checked"])) if m["checked"] else "–",
             _fmt(m["with_unsupported"]), _fmt(m["unsupported_claims"]), _fmt(m["with_missed"]),
             e(" / ".join("%s %s" % (grade, n) for grade, n in m["grades"].items()))] for m in d["models"]]
    flagged = "".join('<div class="claim"><h4>%s%s, %s %s</h4>%s%s</div>'
                      % (e(f["name"]), " (group)" if f["is_group"] else "", e(f["first_day"]), _tag("read by " + f["read_by"]),
                         "".join('<p><strong>The digest said:</strong> %s<br><strong>The conversation shows:</strong> %s</p>' % (e(u.get("claim", "")), e(u.get("actually", "")))
                                 for u in f["unsupported"]),
                         '<p class="small">Missed: %s</p>' % e("; ".join(f["missed"])) if f["missed"] else "")
                      for f in d["flagged"][:FLAGGED_SHOWN])
    return "".join([
        _note(d["definition"]),
        _table(["Model", "Checked", "Clean", "With unsupported statements", "Unsupported statements", "With misses", "Grades"], rows, numeric=(1, 2, 3, 4, 5)),
        "<h3>Flagged digests</h3>%s" % flagged if flagged else "",
        _note("Showing %d of %d flagged." % (min(FLAGGED_SHOWN, d["flagged_total"]), d["flagged_total"])) if d["flagged_total"] > FLAGGED_SHOWN else "",
    ])


BUILDERS: Dict[str, Callable[[sqlite3.Connection, Evidence], str]] = {
    "numbers": _numbers, "portrait": _portrait, "traits": _traits, "overtime": _overtime, "partner": _partner,
    "monthly": _monthly, "decade": _decade, "histories": _histories, "accuracy": _accuracy,
}

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #ffffff; color: #1c1b19; font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
main { max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 26px; margin: 0 0 6px; }
h2 { font-size: 20px; margin: 28px 0 8px; }
h3 { font-size: 16px; margin: 22px 0 6px; }
h4 { font-size: 14px; margin: 12px 0 4px; }
p { margin: 0 0 8px; }
a { color: #1f6feb; }
.lead { font-size: 18px; }
.note, .small { color: #6b6860; font-size: 13px; }
.cover { border-bottom: 1px solid #e2dfd8; padding-bottom: 20px; margin-bottom: 8px; }
.warning { background: #fff4d6; color: #6b4a00; border-radius: 8px; padding: 10px 12px; font-size: 14px; }
section { border-top: 1px solid #e2dfd8; padding-top: 18px; margin-top: 28px; }
section > h1 { margin-bottom: 12px; }
.claim { border-top: 1px solid #eeece6; padding: 8px 0; }
.action { border: 1px solid #e2dfd8; border-radius: 8px; padding: 10px 12px; margin: 8px 0; break-inside: avoid; }
blockquote { margin: 6px 0; padding: 4px 12px; border-left: 3px solid #2a78d6; }
blockquote.unverified { border-left-color: #b5b3ab; color: #6b6860; }
.tag { display: inline-block; white-space: nowrap; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; background: #dbe8fd; border-radius: 999px; padding: 0 7px; margin: 0 2px; }
.tag.good { background: #d4efe1; }
.tag.warn { background: #fff4d6; color: #6b4a00; }
.refs { font-size: 12px; color: #6b6860; }
.refs a { text-decoration: none; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 6px 0 12px; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid #eeece6; vertical-align: top; }
th { color: #6b6860; font-weight: 600; }
.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.card { border: 1px solid #e2dfd8; border-radius: 8px; padding: 8px 14px; min-width: 130px; }
.card b { display: block; font-size: 22px; }
.card span { color: #6b6860; font-size: 13px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.radar-pair { display: grid; grid-template-columns: 540px 1fr; gap: 16px; align-items: start; }
.chart { break-inside: avoid; }
.scroll { overflow-x: auto; }
.radar-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.radar-cell { break-inside: avoid; }
svg { max-width: 100%; height: auto; display: block; }
.legend { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #6b6860; margin: 4px 0; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.bars { font-size: 13px; }
.bar-row { display: grid; grid-template-columns: 140px 1fr 50px; gap: 8px; align-items: center; margin: 3px 0; }
.bar-track { background: #f1f0ec; height: 10px; border-radius: 2px; overflow: hidden; }
.bar-track i { display: block; height: 100%; background: #2a78d6; }
.minis { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 18px; }
.mini { break-inside: avoid; }
.heat { display: inline-block; min-width: 24px; text-align: center; border-radius: 3px; padding: 0 4px; }
.h0 { background: #f1f5fb; } .h1 { background: #cde2fb; } .h2 { background: #9ec5f4; } .h3 { background: #6da7ec; }
.h4 { background: #3987e5; color: #fff; } .h5 { background: #1c5cab; color: #fff; }
.history { border-top: 2px solid #e2dfd8; margin-top: 24px; }
h2.person { margin-top: 18px; }
.ev { border-top: 1px solid #eeece6; padding: 6px 0; font-size: 13px; }
.ev:target { background: #fff4d6; }
nav ol { columns: 2; font-size: 14px; }
@media (max-width: 900px) { .radar-pair { grid-template-columns: 1fr; } }
@media (max-width: 640px) { .pair, .radar-pair { grid-template-columns: 1fr; } nav ol { columns: 1; } .bar-row { grid-template-columns: 100px 1fr 40px; } }
@page { margin: 16mm 14mm; }
@media print {
  main { max-width: none; padding: 0; }
  section { break-before: page; border-top: none; margin-top: 0; }
  h1, h2, h3, h4 { break-after: avoid; }
  .claim, .ev, table tr, .card, .mini, .radar-cell, blockquote, .chart { break-inside: avoid; }
  .scroll { overflow: visible; }
  .radar-pair { grid-template-columns: 1fr; }
  a { color: inherit; text-decoration: none; }
}
"""


def build(conn: sqlite3.Connection, sections: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """The report as one self-contained page. `sections` picks which to include; all by default."""
    wanted = [key for key in SECTION_KEYS if sections is None or key in sections]
    ev = Evidence("evidence" in wanted)
    totals = stats.people(conn, Filters())["totals"]
    made = datetime.now()
    bodies: List[Tuple[str, str, str]] = []
    problems: List[str] = []
    for key in wanted:
        if key == "evidence":
            continue
        title = dict(SECTIONS)[key]
        try:
            body = BUILDERS[key](conn, ev)
        except Exception as exc:              # one broken section must not cost you the rest of the report
            body = _note("This section could not be made: %s" % exc)
            problems.append("%s: %s" % (title, exc))
        bodies.append((key, title, body))
    if "evidence" in wanted:
        try:
            bodies.append(("evidence", "Evidence", ev.appendix()))
        except Exception as exc:
            bodies.append(("evidence", "Evidence", _note("The evidence could not be gathered: %s" % exc)))
            problems.append("Evidence: %s" % exc)
    contents = "<nav><ol>%s</ol></nav>" % "".join('<li><a href="#%s">%s</a></li>' % (key, e(title)) for key, title, _body in bodies)
    cover = "".join([
        '<header class="cover"><h1>Your messages: what they show</h1>',
        "<p>Made on %s by iMessage Explorer %s from %s messages with %s people and %s groups, %s to %s.</p>"
        % (made.strftime("%d %B %Y"), e(__version__), _fmt(totals.get("messages")), _fmt(totals.get("people")), _fmt(totals.get("groups")),
           e(totals.get("first_day", "")), e(totals.get("last_day", ""))),
        "<p>Most of what follows was written by AI models reading other models' summaries of your conversations. It can be wrong, it is "
        "three steps from what was said, and it can only describe what was texted. The numbers are counted directly from the messages.</p>",
        '<p class="warning">This file is private and not encrypted. It holds the most personal things the app makes. Anyone who opens it can read it: '
        "keep it somewhere only you can reach, and think twice before putting it in a shared or synced folder.</p>",
        "<h3>Contents</h3>", contents, "</header>"])
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex"><title>Your messages: a report</title><style>%s</style></head><body><main>%s%s</main></body></html>'
            % (STYLE, cover, "".join('<section id="%s"><h1>%s</h1>%s</section>' % (key, e(title), body) for key, title, body in bodies)))
    return {"html": page, "filename": "iMessage Explorer report %s.html" % made.strftime("%Y-%m-%d"), "bytes": len(page.encode("utf-8")),
            "sections": [key for key, _t, _b in bodies], "problems": problems,
            "evidence": sum(len(numbers) for numbers in ev.cited.values())}


def contents() -> List[Dict[str, object]]:
    """What each section would hold right now, so the screen can say what is ready."""
    found = portrait.read()
    ready = {
        "numbers": True, "portrait": bool(found.get("portrait")), "traits": bool(found.get("portrait") or found.get("per_person")),
        "overtime": bool(portrait.charts()["digests"]), "partner": bool(plans.read("partner").get("body")),
        "monthly": bool(plans.partner()), "decade": bool(plans.read("decade").get("body")),
        "histories": bool(histories.overview()["histories"]), "accuracy": bool(check.summary()["checked"]), "evidence": True,
    }
    return [{"key": key, "title": title, "ready": ready[key]} for key, title in SECTIONS]
