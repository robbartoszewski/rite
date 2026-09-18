"""What this machine's last coordination tick concluded (local, no network).

`doctor` reads the fleet live, which costs a round trip and needs the remote
to be reachable. `rite status` is what a human runs by habit, and §2.5.2
keeps it cheap — so it cannot go and ask.

This is the cheap half: the tick writes down what it concluded, and status
reads it back **stamped with when**. Not a live answer and never presented
as one. "alpha was Owner as of four minutes ago" is honest and useful; the
same sentence without the age would be a lie the moment the machine stops
ticking, which is exactly when somebody runs status to find out why.

It is a local file, so it says nothing about the other machines — only what
THIS machine last managed to establish. `doctor` remains the fleet view.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

FILENAME = "coordination-last.json"


@dataclass
class LastTick:
    manager: str = ""
    action: str = ""
    owner: bool = False
    at: float = 0.0
    problems: int = 0

    def age_seconds(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.at)


def path_for(root: Path) -> Path:
    return root / ".rite" / FILENAME


def record(root: Path, manager: str, action: str, owner: bool, problems: int) -> None:
    """Write the conclusion. Never raises: a status convenience must not be
    able to fail the tick that produces it."""
    try:
        path_for(root).write_text(
            json.dumps(
                {
                    "manager": manager,
                    "action": action,
                    "owner": owner,
                    "at": time.time(),
                    "problems": problems,
                },
                sort_keys=True,
            )
            + "\n"
        )
    except OSError:
        pass


def read(root: Path) -> LastTick | None:
    """The last conclusion, or None when there is not one to report.

    None for a missing file, unreadable bytes, or a shape this version does
    not recognise — all of which mean "this machine has not told us", which
    is what status should say rather than inventing a state.
    """
    try:
        raw = json.loads(path_for(root).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("manager"), str):
        return None
    return LastTick(
        manager=raw.get("manager", ""),
        action=str(raw.get("action", "")),
        owner=bool(raw.get("owner", False)),
        at=float(raw.get("at", 0.0) or 0.0),
        problems=int(raw.get("problems", 0) or 0),
    )


def describe(last: LastTick, now: float | None = None) -> str:
    """One line for `rite status`, with the age always present."""
    age = last.age_seconds(now)
    if age < 90:
        when = f"{int(age)}s ago"
    elif age < 5400:
        when = f"{int(age // 60)}m ago"
    else:
        when = f"{int(age // 3600)}h{int(age % 3600 // 60):02d}m ago"
    role = "Owner" if last.owner else "not Owner"
    trailer = f", {last.problems} problem(s)" if last.problems else ""
    return f"{last.manager}: {role} as of {when} ({last.action}{trailer})"
