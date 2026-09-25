"""What rite itself saw happen in a project, kept so a standup can cite it.

**Why this exists (plan § K4).** A check-in opens with a standup, and a
standup is otherwise the most efficient channel there is for unverified
claims: three things were reported done in the last release that had never
run. So the standup is composed from RECORDS, and until now rite recorded
almost nothing it saw. A Worker's sandbox start, a stop, a board move each
printed a line to a terminal and was gone.

Each record here is written at the one point where rite itself observed the
event (the command succeeded), and carries the identifier a reader would
check: the sandbox name, the ticket id. Nothing here is a model's account of
what happened.

**Project-level, not under a Manager's directory**, because these are facts
about the project: a Worker started from a terminal is as real as one a
Manager asked for. `.rite/` is gitignored, so this stays on the machine that
saw it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

EVENTS_FILENAME = "events.jsonl"


def events_path(root: Path) -> Path:
    return Path(root) / ".rite" / EVENTS_FILENAME


def record(root: Path, event: str, **fields: object) -> None:
    """Append one observed event. Never raises.

    ⚠ **A failure to RECORD must not fail what was recorded.** A Worker that
    started is running whether or not this line reached the disk, and
    refusing to report the start because the log could not be written would
    hide a running Worker. What is lost is one standup line.
    """
    line = json.dumps({"event": event, "at": time.time(), **fields}, sort_keys=True)
    try:
        path = events_path(root)
        if not path.parent.is_dir():
            return  # not a rite project: nothing to record into
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


def since(root: Path, t: float) -> list[dict]:
    """Every recorded event after `t`, oldest first. Unreadable lines are
    skipped, because one torn line must not hide the rest."""
    try:
        lines = events_path(root).read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and float(item.get("at") or 0) > t:
            out.append(item)
    return out
