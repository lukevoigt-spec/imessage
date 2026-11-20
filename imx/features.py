"""Per-message measurements computed once, at import: tone, length and expression markers.

Tone (sentiment) is scored on your Mac with a word list, not by an AI. It is a
simplified form of the published VADER method: each word and emoji that carries
feeling has a score; "not", "never" and similar flip the words after them;
"very", "so" and similar strengthen them; the total is squeezed into a range
from -1 (very negative) to +1 (very positive).

What this can and cannot do: it is good at broad patterns across many
messages. It does not understand sarcasm, inside jokes or context, and most
short messages ("ok", "on my way") score as neutral. For nuance about one
conversation, use the AI read on the Ask AI screen.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, Tuple

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMOJI_BASE = (
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF\U00002300-\U000023FF\U000025A0-\U000025FF\U00003030\U0000303D\U00003297\U00003299]"
)
_EMOJI_MOD = "[️\U0001F3FB-\U0001F3FF]*"
# Flags (two regional letters), or an emoji with optional skin tone, joined by zero-width joiners.
EMOJI_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF]{2}|%s%s(?:‍%s%s)*" % (_EMOJI_BASE, _EMOJI_MOD, _EMOJI_BASE, _EMOJI_MOD)
)
COUNT_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9']*")

# ---- expression and style markers, stored together as one number per message ----

AFFECTION, GRATITUDE, APOLOGY, LAUGHTER, QUESTION, PRAYER, ENCOURAGEMENT = 1, 2, 4, 8, 16, 32, 64
EMOJI, EXCLAIM, SHOUT, LOWER_START, LINK = 128, 256, 512, 1024, 2048
EDITED, UNSENT, VOICE = 4096, 8192, 16384      # set from Apple's own fields at import, when present

# Kinds of attachment, stored together as one number per message.
PHOTO, VIDEO, AUDIO, OTHER_FILE = 1, 2, 4, 8

# What each expression counter looks for. Shown on the Expressions screen.
EXPRESSIONS = [
    ("affection", AFFECTION, "love you, miss you, ily"),
    ("gratitude", GRATITUDE, "thank you, thanks, appreciate, grateful"),
    ("apology", APOLOGY, "sorry, apologize, my bad"),
    ("laughter", LAUGHTER, "lol, haha, lmao, laughing emoji"),
    ("question", QUESTION, "any message containing a question mark"),
    ("prayer", PRAYER, "pray, praying, prayer"),
    ("encouragement", ENCOURAGEMENT, "proud of you, congrats, great job, you got this"),
]

_PATTERNS = [
    (AFFECTION, re.compile(r"\b(?:love|luv)\s+(?:you|u|ya|y'?all)\b|\bily\b|\bmiss\s+(?:you|u|ya)\b", re.I)),
    (GRATITUDE, re.compile(r"\bthank(?:s| you| u)?\b|\bthx\b|\bappreciate\b|\bgrateful\b", re.I)),
    (APOLOGY, re.compile(r"\bsorry\b|\bapologi[sz]e\b|\bapologies\b|\bmy bad\b|\bforgive me\b", re.I)),
    (LAUGHTER, re.compile("\\b(?:lol+|lmao+|lmfao|rofl|ha(?:ha)+h?|he(?:he)+)\\b|[\U0001F602\U0001F923\U0001F606\U0001F639]", re.I)),
    (PRAYER, re.compile(r"\bpray(?:ing|ed|er|ers|s)?\b", re.I)),
    (ENCOURAGEMENT, re.compile(
        r"\bproud of (?:you|u)\b|\bcongrat(?:s|ulations)\b|\b(?:great|good|nice) (?:job|work)\b|\bwell done\b"
        r"|\byou(?:'ve)? got this\b|\bway to go\b", re.I)),
]

# ---- tone ----

NEGATIONS = frozenset("""
not no never none nobody nothing neither nor nowhere cannot cant can't dont don't doesnt doesn't didnt didn't
isnt isn't wasnt wasn't arent aren't werent weren't wont won't wouldnt wouldn't shouldnt shouldn't couldnt
couldn't aint ain't without hardly rarely seldom
""".split())
BOOSTERS = {w: 0.293 for w in
            "very really so extremely super totally absolutely incredibly especially truly completely too such".split()}
BOOSTERS.update({w: -0.293 for w in "kinda sorta slightly somewhat barely hardly little bit".split()})

# Scores for common emoji, on the same -4 to +4 scale as the word list.
EMOJI_SCORES = {
    "❤": 3.0, "\U0001F60D": 2.8, "\U0001F970": 2.8, "\U0001F618": 2.5, "\U0001F60A": 2.2, "\U0001F642": 1.2,
    "\U0001F600": 2.0, "\U0001F603": 2.2, "\U0001F604": 2.2, "\U0001F601": 2.2, "\U0001F602": 1.8, "\U0001F923": 1.8,
    "\U0001F606": 1.8, "\U0001F44D": 1.6, "\U0001F64F": 1.5, "\U0001F389": 2.4, "\U0001F973": 2.4, "\U0001F60E": 1.6,
    "\U0001F917": 2.0, "\U0001F495": 2.6, "\U0001F496": 2.6, "\U0001F497": 2.6, "\U0001F499": 2.6, "\U0001F49A": 2.6,
    "\U0001F49B": 2.6, "\U0001F49C": 2.6, "\U0001F9E1": 2.6, "\U0001F525": 1.5, "\U0001F4AF": 1.8, "\U0001F44F": 2.0,
    "\U0001F64C": 2.2, "✨": 1.4, "\U0001F605": 0.8, "\U0001F609": 1.4, "\U0001F60B": 1.6, "\U0001F929": 2.6,
    "\U0001F607": 1.8, "\U0001F382": 1.5, "\U0001F91E": 1.0, "\U0001F4AA": 1.6,
    "\U0001F622": -1.8, "\U0001F62D": -1.2, "\U0001F61E": -1.8, "\U0001F614": -1.6, "\U0001F621": -2.6,
    "\U0001F620": -2.3, "\U0001F92C": -2.8, "\U0001F624": -1.5, "\U0001F629": -1.6, "\U0001F62B": -1.6,
    "\U0001F494": -2.6, "\U0001F612": -1.6, "\U0001F644": -1.3, "\U0001F44E": -1.8, "\U0001F62C": -0.8,
    "\U0001F631": -1.2, "\U0001F922": -2.2, "\U0001F92E": -2.2, "\U0001F637": -1.0, "\U0001F912": -1.5,
}

NEUTRAL_ACKNOWLEDGEMENTS = frozenset("ok okay k kk yes yeah yep yup sure alright".split())

POSITIVE_AT = 0.05    # a message scoring at or above this counts as positive
NEGATIVE_AT = -0.05   # at or below this counts as negative

_TONE_TOKEN_RE = re.compile(
    "[A-Za-z][A-Za-z']*|[:;=][-']?[)(DPp/\\\\|\\]\\[]|" + _EMOJI_BASE
)
_lexicon: Dict[str, float] = {}


def lexicon() -> Dict[str, float]:
    """The word list, loaded from disk the first time it is needed."""
    if not _lexicon:
        path = Path(__file__).resolve().parent / "data" / "vader_lexicon.txt"
        with open(str(path), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                word, _tab, score = line.rstrip("\n").partition("\t")
                if word and score:
                    _lexicon[word] = float(score)
        _lexicon.update(EMOJI_SCORES)
        # Plain acknowledgements are so common in texting that scoring them as positive would
        # make every conversation look warm. They are treated as neutral.
        for word in NEUTRAL_ACKNOWLEDGEMENTS:
            _lexicon.pop(word, None)
    return _lexicon


def tone(text: str) -> float:
    """Score one message from -1 (very negative) to +1 (very positive). 0 is neutral."""
    if not text:
        return 0.0
    words = lexicon()
    tokens = _TONE_TOKEN_RE.findall(URL_RE.sub(" ", text))
    if not tokens:
        return 0.0
    lowered = [t.lower() for t in tokens]
    # Capitals add emphasis only when the message is not written entirely in capitals.
    mixed_case = any(t.isupper() and len(t) > 1 for t in tokens) and any(t.islower() for t in tokens)
    total = 0.0
    for i, token in enumerate(lowered):
        value = words.get(token)
        if value is None:
            value = words.get(tokens[i])      # emoticons such as :D are case-sensitive
        if value is None:
            continue
        if mixed_case and tokens[i].isupper() and len(token) > 1:
            value += 0.733 if value > 0 else -0.733
        # Look back up to three words for strengtheners ("very") and reversals ("not").
        for distance in (1, 2, 3):
            if i - distance < 0:
                break
            previous = lowered[i - distance]
            boost = BOOSTERS.get(previous)
            if boost:
                boost *= (1.0, 0.95, 0.9)[distance - 1]
                value += boost if value > 0 else -boost
            if previous in NEGATIONS:
                value *= -0.74
                break
        total += value
    if total:
        emphasis = min(text.count("!"), 4) * 0.292
        total += emphasis if total > 0 else -emphasis
    return round(total / math.sqrt(total * total + 15.0), 4)


def measure(text: str) -> Tuple[float, int, int]:
    """Everything stored per message: (tone score, word count, expression and style markers)."""
    if not text:
        return 0.0, 0, 0
    flags = 0
    for bit, pattern in _PATTERNS:
        if pattern.search(text):
            flags |= bit
    if "?" in text:
        flags |= QUESTION
    if "!" in text:
        flags |= EXCLAIM
    if EMOJI_RE.search(text):
        flags |= EMOJI
    if URL_RE.search(text):
        flags |= LINK
    stripped = URL_RE.sub(" ", text)
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 6 and sum(1 for c in letters if c.isupper()) >= 0.8 * len(letters):
        flags |= SHOUT
    if letters and letters[0].islower():
        flags |= LOWER_START
    return tone(text), len(COUNT_WORD_RE.findall(stripped)), flags
