"""Labels and pins you give to people.

Texting cannot see time spent in person. Someone you live with or see every day
may text you far less than a colleague, so no formula can rank the people in
your life correctly from messages alone. Labels and pins let you tell the tool
what it cannot see:

- A **pin** marks your inner circle. Pinned people are always shown first.
- A **label** says what kind of relationship it is. "Service" (businesses,
  delivery updates, verification senders) removes someone from the rankings.

Stored in a small file in the data folder, separate from the message database,
so re-importing never loses them.
"""

from __future__ import annotations

import json
from typing import Dict

from . import paths
from .query import QueryError

LABELS = ("family", "close friend", "friend", "work", "service")


def _file():
    return paths.data_dir() / "people.json"


def load() -> Dict[str, dict]:
    path = _file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(name): {"label": entry.get("label") or "", "pinned": bool(entry.get("pinned")),
                            "keep": bool(entry.get("keep"))}
                for name, entry in data.items() if isinstance(entry, dict)}
    except (ValueError, OSError):
        return {}


def version() -> str:
    """Changes whenever labels change, so cached rankings are rebuilt."""
    return json.dumps(load(), sort_keys=True)


def save(name: str, label: str = "", pinned: bool = False) -> Dict[str, dict]:
    name = (name or "").strip()
    label = (label or "").strip().lower()
    if not name:
        raise QueryError("Choose a person first.")
    if label and label not in LABELS:
        raise QueryError("The label must be one of: %s." % ", ".join(LABELS))
    people = load()
    # "Keep" (never hide this sender as automated) survives relabelling, unless you now call it a service.
    keep = bool(people.get(name, {}).get("keep")) and label != "service"
    return _write(people, name, label, pinned, keep)


def _write(people: Dict[str, dict], name: str, label: str, pinned: bool, keep: bool) -> Dict[str, dict]:
    if label or pinned or keep:
        people[name] = {"label": label, "pinned": bool(pinned), "keep": bool(keep)}
    else:
        people.pop(name, None)                 # nothing to remember
    _file().write_text(json.dumps(people, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return people


def keep(name: str) -> Dict[str, dict]:
    """Never hide this sender as automated. Undoes a "service" label too."""
    name = (name or "").strip()
    if not name:
        raise QueryError("Choose a sender first.")
    people = load()
    current = people.get(name, {"label": "", "pinned": False})
    label = "" if current["label"] == "service" else current["label"]
    return _write(people, name, label, current["pinned"], True)


def hide(name: str) -> Dict[str, dict]:
    """Treat this sender as a service: hidden everywhere."""
    return save(name, "service", False)


def services() -> set:
    return {name for name, entry in load().items() if entry["label"] == "service"}


def pinned() -> set:
    return {name for name, entry in load().items() if entry["pinned"]}
