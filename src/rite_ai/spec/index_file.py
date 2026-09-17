"""The unit inventory on disk: `.rite/spec/index.json`.

What `rite spec index` records about the source — every unit's id, file, line
range, title and hash — so `rite spec status` can say what changed without
re-deriving anything, and `rite spec verify` can check coverage and freshness
against it.

**Absent and unreadable are different answers.** No index means the spec has
not been indexed yet. An index that exists but cannot be trusted — corrupt
JSON, a missing field, two units with one id, a format this rite does not know —
is unreadable, and it is never returned as an index with no units in it. A
corrupt index read as empty would report every unit as new and every digest as
uncovered, which is the kind of confident wrong answer this module exists to
refuse.

Written atomically, and byte-for-byte the same for the same units, so a
committed index changes in a diff only when the spec did.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from rite_ai.spec.units import Parsed, Unit
from rite_ai.state import write_atomic

INDEX_FILE = Path(".rite") / "spec" / "index.json"
FORMAT_VERSION = 1

PRESENT = "present"
ABSENT = "absent"
UNREADABLE = "unreadable"

_UNIT_FIELDS = {f.name: f.type for f in fields(Unit)}


@dataclass(frozen=True)
class SpecIndex:
    units: tuple[Unit, ...]
    files: tuple[str, ...]
    total_lines: int

    def by_id(self) -> dict[str, Unit]:
        return {u.id: u for u in self.units}


@dataclass(frozen=True)
class IndexRead:
    status: str  # PRESENT | ABSENT | UNREADABLE
    index: SpecIndex | None = None
    error: str = ""


def index_path(root: Path) -> Path:
    return root / INDEX_FILE


def from_parsed(parsed: Parsed) -> SpecIndex:
    return SpecIndex(tuple(parsed.units), tuple(parsed.files), parsed.total_lines)


def render(index: SpecIndex) -> str:
    data = {
        "format": FORMAT_VERSION,
        "files": list(index.files),
        "total_lines": index.total_lines,
        "units": [asdict(u) for u in index.units],
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_index(root: Path, index: SpecIndex) -> Path:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, render(index))
    return path


def _unreadable(path: Path, why: str) -> IndexRead:
    return IndexRead(UNREADABLE, error=f"{path}: {why}")


def read_index(root: Path) -> IndexRead:
    path = index_path(root)
    if not path.exists():
        return IndexRead(ABSENT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return _unreadable(path, f"cannot be read as JSON ({e})")
    if not isinstance(data, dict):
        return _unreadable(path, "is not a JSON object")
    version = data.get("format")
    if not isinstance(version, int):
        return _unreadable(path, "has no format version")
    if version > FORMAT_VERSION:
        return _unreadable(
            path,
            f"was written by a newer rite (format {version}; this one reads up to "
            f"{FORMAT_VERSION}) — upgrade rite rather than re-indexing over it",
        )
    files, total, raw_units = (
        data.get("files"),
        data.get("total_lines"),
        data.get("units"),
    )
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return _unreadable(path, "'files' is not a list of paths")
    if not isinstance(total, int) or total < 0:
        return _unreadable(path, "'total_lines' is not a line count")
    if not isinstance(raw_units, list):
        return _unreadable(path, "'units' is not a list")

    units: list[Unit] = []
    seen: set[str] = set()
    for n, raw in enumerate(raw_units):
        if not isinstance(raw, dict) or set(raw) != set(_UNIT_FIELDS):
            return _unreadable(path, f"unit {n} does not have exactly the unit fields")
        try:
            unit = Unit(**raw)
        except TypeError as e:
            return _unreadable(path, f"unit {n}: {e}")
        if not (isinstance(unit.id, str) and unit.id and isinstance(unit.sha, str)):
            return _unreadable(path, f"unit {n} has no id or hash")
        if not (isinstance(unit.start, int) and isinstance(unit.end, int)):
            return _unreadable(path, f"unit {unit.id!r} has no line range")
        if unit.start < 1 or unit.end < unit.start:
            return _unreadable(path, f"unit {unit.id!r} has an impossible line range")
        if unit.id in seen:
            return _unreadable(path, f"two units share the id {unit.id!r}")
        seen.add(unit.id)
        units.append(unit)
    return IndexRead(PRESENT, SpecIndex(tuple(units), tuple(files), total))
