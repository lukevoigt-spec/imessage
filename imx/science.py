"""Three measures taken from relationship and social-network research.

Each one is a rough lens with real limits, stated in its definition. None of
them measures how much you care about someone. The research behind them is
summarised, with its weaknesses, in docs/NEXT_PHASE.md.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List

from . import insights, labels
from .features import URL_RE
from .query import Filters
from .stats import _cached

MIN_WORDS_EACH_SIDE = 500      # both people need this many words before style matching is reported
MIN_WORDS_WE_TALK = 300
CIRCLE_SIZES = (("Closest", 5), ("Close", 15), ("Friends", 50), ("Wider circle", 150))

DEFINITIONS = {
    "we_talk": "The share of 'we, us, our' among all first-person words ('I, me, my' and 'we, us, our'). A higher "
               "share means more talk as a pair. Research pooling 30 studies found couples who use more 'we' "
               "tend to report better relationships, but some studies found the opposite and nobody has shown "
               "which causes which. Shown only with at least %d words from a person." % MIN_WORDS_WE_TALK,
    "style_matching": "How closely two people match each other's use of small words: pronouns, articles, "
                      "prepositions, helping verbs, common adverbs, joining words, negations and amount words. "
                      "1.00 is identical, lower is less alike. In one study of dating couples, closer matching "
                      "went with staying together three months later. It is one study, of young couples, and "
                      "shows a link, not a cause. This tool uses its own word lists, not the ones in that study. "
                      "Shown only when both people have written at least %d words." % MIN_WORDS_EACH_SIDE,
    "circles": "Research on phone records finds people keep rings of roughly 5, 15, 50 and 150 contacts, each "
               "ring less close than the one inside it. Your pinned inner circle fills the rings first, then "
               "everyone else in order of importance score. Only people you have written to at least once are "
               "included, and anyone you labelled as a service is left out.",
}

WE_WORDS = frozenset("we us our ours ourselves we're we've we'll we'd let's".split())
I_WORDS = frozenset("i me my mine myself i'm i've i'll i'd im ive ill id".split()) - {"ill", "id"}

# Small "function" words, in the eight groups style matching compares.
STYLE_GROUPS: Dict[str, frozenset] = {
    "personal pronouns": frozenset("i me my mine myself you your yours yourself he him his himself she her hers herself "
                                   "we us our ours ourselves they them their theirs themselves i'm you're he's she's "
                                   "we're they're i've you've we've they've i'll you'll he'll she'll we'll they'll "
                                   "i'd you'd he'd she'd we'd they'd".split()),
    "impersonal pronouns": frozenset("it its it's itself this that these those something anything nothing everything "
                                     "someone anyone everyone somebody anybody everybody nobody what which who whom "
                                     "whose that's what's".split()),
    "articles": frozenset("a an the".split()),
    "prepositions": frozenset("of in to for with on at from by about as into like through after over between out "
                              "against during without before under around among up down off near until".split()),
    "helping verbs": frozenset("am is are was were be been being have has had having do does did will would shall "
                               "should can could may might must isn't aren't wasn't weren't don't doesn't didn't "
                               "won't wouldn't can't couldn't shouldn't haven't hasn't hadn't".split()),
    "common adverbs": frozenset("very really just so too quite rather also even still already always never often "
                                "sometimes maybe perhaps probably actually basically totally definitely here there "
                                "now then again".split()),
    "joining words": frozenset("and but or nor yet because although though while if unless since whereas when "
                               "whether".split()),
    "negations": frozenset("no not never none nothing neither nobody nowhere n't cannot".split()),
}
GROUP_NAMES = list(STYLE_GROUPS)
_WORD_RE = re.compile(r"[a-z][a-z']*")
_GROUP_OF: Dict[str, List[int]] = {}
for _index, _name in enumerate(GROUP_NAMES):
    for _word in STYLE_GROUPS[_name]:
        _GROUP_OF.setdefault(_word, []).append(_index)


def _tally():
    return {"words": 0, "we": 0, "i": 0, "groups": [0] * len(GROUP_NAMES)}


def _count(entry: dict, text: str) -> None:
    for token in _WORD_RE.findall(URL_RE.sub(" ", text).lower().replace("’", "'")):
        entry["words"] += 1
        if token in WE_WORDS:
            entry["we"] += 1
        elif token in I_WORDS:
            entry["i"] += 1
        for group in _GROUP_OF.get(token, ()):
            entry["groups"][group] += 1


def _we_share(entry: dict):
    first_person = entry["we"] + entry["i"]
    return round(100.0 * entry["we"] / first_person, 1) if first_person else None


def _matching(mine: dict, theirs: dict):
    """Average, over the word groups, of how close the two people's usage rates are."""
    if mine["words"] < MIN_WORDS_EACH_SIDE or theirs["words"] < MIN_WORDS_EACH_SIDE:
        return None
    scores = []
    for group in range(len(GROUP_NAMES)):
        a = 100.0 * mine["groups"][group] / mine["words"]
        b = 100.0 * theirs["groups"][group] / theirs["words"]
        scores.append(1.0 - abs(a - b) / (a + b + 0.0001))
    return round(sum(scores) / len(scores), 2)


def language(conn: sqlite3.Connection, filters: Filters, limit: int = 60) -> Dict[str, object]:
    """'We' against 'I', and style matching, per person and over the years. One-to-one chats only."""

    def compute():
        direct = Filters(contact=filters.contact, date_from=filters.date_from, date_to=filters.date_to, scope="direct")
        where, params = direct.where("m")
        people: Dict[str, Dict[int, dict]] = {}
        years: Dict[str, Dict[int, dict]] = {}
        for name, year, from_me, text in conn.execute(
            "SELECT counterpart, year, from_me, text FROM messages m WHERE %s AND m.kind = 'text'" % where, params
        ):
            _count(people.setdefault(name, {0: _tally(), 1: _tally()})[from_me], text)
            _count(years.setdefault(year, {0: _tally(), 1: _tally()})[from_me], text)
        skip = labels.services()
        rows = []
        mine_total, theirs_total = _tally(), _tally()
        for name, sides in people.items():
            mine, theirs = sides[1], sides[0]
            for key in ("words", "we", "i"):
                mine_total[key] += mine[key]
                theirs_total[key] += theirs[key]
            if name in skip:
                continue
            rows.append({
                "name": name, "my_words": mine["words"], "their_words": theirs["words"],
                "my_we_share": _we_share(mine) if mine["words"] >= MIN_WORDS_WE_TALK else None,
                "their_we_share": _we_share(theirs) if theirs["words"] >= MIN_WORDS_WE_TALK else None,
                "style_matching": _matching(mine, theirs),
            })
        rows.sort(key=lambda r: r["my_words"] + r["their_words"], reverse=True)
        return {
            "we_talk_definition": DEFINITIONS["we_talk"], "style_definition": DEFINITIONS["style_matching"],
            "overall": {"my_we_share": _we_share(mine_total), "their_we_share": _we_share(theirs_total)},
            "by_year": [{"period": year, "my_we_share": _we_share(sides[1]), "their_we_share": _we_share(sides[0]),
                         "style_matching": _matching(sides[1], sides[0])} for year, sides in sorted(years.items())],
            "rows": rows,
        }

    data = _cached(conn, "language", filters.key() + (labels.version(),), compute)
    return dict(data, rows=data["rows"][:int(limit)])


def circles(conn: sqlite3.Connection) -> Dict[str, object]:
    """Your people arranged in rings of 5, 15, 50 and 150."""
    ranked = insights.importance(conn)["rows"]
    rings = []
    start = 0
    for name, size in CIRCLE_SIZES:
        members = ranked[start:size]
        rings.append({"name": name, "holds": size - start, "up_to": size,
                      "people": [{"name": r["name"], "score": r["score"], "pinned": r["pinned"], "label": r["label"]}
                                 for r in members]})
        start = size
    return {"definition": DEFINITIONS["circles"], "people_ranked": len(ranked), "rings": rings,
            "beyond": max(0, len(ranked) - CIRCLE_SIZES[-1][1])}
