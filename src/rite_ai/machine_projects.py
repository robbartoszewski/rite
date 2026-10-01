"""Every rite project this machine has seen, by root: `~/.rite/projects.json`.

For one question: does another project on this machine read the same board
(`tickets.scope.sharing_problems`)? The Dispatch registry (`rite projects
add`) could not answer it: it is opt-in, and on the machine where two
projects collided on one board (v0.7.0 dogfood S1) neither was in it. So
`rite init`, `rite start` and `rite doctor` each note the project they run in.

**Paths only, read live.** Nothing about a project is recorded but where it
is; its board and scope label are read from its own config each time, so an
edit there is never answered with what it said last week. A root that is
gone, or no longer a rite project, is skipped, and dropped at the next write.

**It sees only this machine,** and only projects that ran one of those three
commands since this existed. A project on another machine sharing the board
is invisible here, and every message built on this says so.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.state import CorruptStateError, locked, read_json_state, write_atomic

FILENAME = "projects.json"


def _path() -> Path:
    from rite_ai.credentials.store import default_rite_home

    return default_rite_home() / FILENAME


def _is_project(root: Path) -> bool:
    return (root / ".rite" / "config.yaml").is_file()


def note(root: Path) -> None:
    """Record `root`. Never raises: a machine record that cannot be written
    must not stop the command that tried to write it."""
    here = str(Path(root).resolve())
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path):
            data = read_json_state(path, {})
            roots = data.get("roots") if isinstance(data, dict) else None
            roots = [r for r in (roots or []) if isinstance(r, str)]
            kept = [r for r in roots if r != here and _is_project(Path(r))]
            write_atomic(
                path, json.dumps({"roots": sorted([*kept, here])}, indent=1) + "\n"
            )
    except (OSError, CorruptStateError):
        return


def known_projects() -> list[Path]:
    """Every recorded root that is still a rite project. An unreadable record
    is read as none recorded: this feeds a refusal, and a refusal on a file
    no one can see would be worse than the check it serves."""
    try:
        data = read_json_state(_path(), {})
    except CorruptStateError:
        return []
    roots = data.get("roots") if isinstance(data, dict) else None
    return [
        Path(r) for r in (roots or []) if isinstance(r, str) and _is_project(Path(r))
    ]
