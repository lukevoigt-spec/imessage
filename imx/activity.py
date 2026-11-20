"""A local record of every time message text was handed to an AI.

Each line records when, which feature, how many messages and which
conversations were involved. It never records the message text itself.
Shown on the Setup screen so you can always see what was shared.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List

from . import paths


def record(kind: str, **details: object) -> None:
    entry: Dict[str, object] = {"when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    entry.update(details)
    try:
        with open(str(paths.activity_log()), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent(limit: int = 50) -> List[Dict[str, object]]:
    path = paths.activity_log()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    entries = []
    for line in reversed(lines):
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries
