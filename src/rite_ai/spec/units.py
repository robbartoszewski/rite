"""A spec source parsed into addressable units.

Headings are the units, because that is the structure every spec already has
and it needs nothing from the author. Measured on rite's own SPEC.md, a
heading's own body is small (median 23 lines, under 1% of the document), so
cutting finer buys nothing and only makes the reference graph denser.

Three identifier shapes:

* a **numbered heading** is its number: `### 5.3.3. Token scoping` is `5.3.3`;
* an **unnumbered heading** is a slug path: `#### Promotion` under `### 2.4.4.`
  is `2.4.4/promotion`, and `### Non-goals` under `## Scope` in a document with
  no numbers is `scope/non-goals`. Path-qualified, so two `Files` headings in
  different sections do not collide;
* a **decision-register row** is its number: `| D-31 | … |` is `D-31`.

Register rows are units by default rather than by configuration. `/spec` writes
every project's spec with a mandatory decision register (D-53) and tickets cite
it as `D-<number>`, so a row is the one target a citation is guaranteed to
resolve to. A `/spec` document has no numbered headings at all: its units are
slug paths plus the register.

**What an id is stable against.** A number or a D-number survives every edit
except renumbering, which the register forbids. A slug changes when its heading,
or an unnumbered heading above it, is renamed — so a rename reads as one unit
removed and one added, and is reported as that rather than hidden.

Not parsed: setext (underlined) headings, HTML headings, and anything inside a
fenced code block or YAML front matter — a `# comment` in a fenced example is
not a section. A document with no headings yields no section units and a problem
saying so: splitting it would mean inventing structure, and the right answer
for such a spec is to point Workers at the whole file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION = "section"
DECISION = "decision"
ITEM = "item"  # matched by a project's own `spec.extra_units` pattern
PREAMBLE_ID = "preamble"
MARKDOWN_SUFFIXES = (".md", ".markdown")

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
# `5.3.3. Title`, `5.3.3 Title` and `1. Title`, but not `2024 roadmap`: a lone
# number is a section number only with its trailing dot.
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)+)\.?[ \t]+(.+)$|^(\d+)\.[ \t]+(.+)$")
_DECISION_ROW = re.compile(r"^\s*\|\s*(D-\d+)\s*\|([^|]*)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str  # SECTION | DECISION
    source: str  # the file it came from, as registered or expanded
    start: int  # first line, 1-based
    end: int  # last line, 1-based, inclusive
    title: str  # heading text without its number; a decision's name
    level: int  # heading level 1-6; 0 for a decision row or the preamble
    parent: str | None  # id of the section it sits in
    sha: str  # sha256 of its lines, line endings normalised

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


@dataclass
class Parsed:
    units: list[Unit] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    total_lines: int = 0
    # Each file's lines, line endings normalised, so the reference graph reads
    # unit bodies from the same text the ranges and hashes came from.
    lines: dict[str, list[str]] = field(default_factory=dict)

    def find(self, ref: str) -> list[Unit]:
        """Units matching `ref`: a bare id, or `source#id` when two files
        share one."""
        return [u for u in self.units if ref in (u.id, f"{u.source}#{u.id}")]


def _slug(text: str) -> str:
    plain = re.sub(r"[`*_]", "", text).lower()
    slug = re.sub(r"[^\w]+", "-", plain).strip("-_")
    return slug or "section"


def _split_number(text: str) -> tuple[str | None, str]:
    m = _NUMBERED.match(text)
    if not m:
        return None, text
    if m.group(1):
        return m.group(1), m.group(2).strip()
    return m.group(3), m.group(4).strip()


def _sha(lines: list[str]) -> str:
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _outside_code(lines: list[str]) -> list[bool]:
    """False for every line inside YAML front matter or a fenced code block,
    fence lines included."""
    outside = [True] * len(lines)
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() in ("---", "..."):
                for k in range(0, j + 1):
                    outside[k] = False
                i = j + 1
                break
    fence: str | None = None
    for j in range(i, len(lines)):
        m = _FENCE.match(lines[j])
        if fence is None:
            if m:
                fence = m.group(1)
                outside[j] = False
        else:
            outside[j] = False
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                if lines[j].strip() == m.group(1):
                    fence = None
    return outside


def parse_text(
    text: str, source: str, extra_units: list[str] | tuple[str, ...] = ()
) -> Parsed:
    """Units of one document. `source` is recorded on each unit as given.

    `extra_units` are `spec.extra_units` patterns, matched at the start of each
    line outside code that is not a heading or a decision row; a match becomes
    an item unit, its id the pattern's first group. Ids should be distinctive
    tokens (`REQ-14`), since they are also how items are cited."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out = Parsed(files=[source], total_lines=len(lines), lines={source: lines})
    outside = _outside_code(lines)

    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        if not outside[i]:
            continue
        m = _HEADING.match(line)
        if m and m.group(2):
            headings.append((i, len(m.group(1)), m.group(2)))

    seen: dict[str, int] = {}

    def claim(uid: str, line_no: int) -> str:
        if uid not in seen:
            seen[uid] = line_no
            return uid
        n = 2
        while f"{uid}~{n}" in seen:
            n += 1
        out.problems.append(
            f"{source}:{line_no}: '{uid}' is already the id of line {seen[uid]} — "
            f"this one is addressed as '{uid}~{n}'"
        )
        seen[f"{uid}~{n}"] = line_no
        return f"{uid}~{n}"

    first_heading = headings[0][0] if headings else len(lines)
    if any(outside[i] and lines[i].strip() for i in range(first_heading)):
        body = lines[:first_heading]
        out.units.append(
            Unit(
                claim(PREAMBLE_ID, 1),
                SECTION,
                source,
                1,
                first_heading,
                "",
                0,
                None,
                _sha(body),
            )
        )

    # The document's own title: its one level-1 heading, when that is the first
    # heading. It names the document, so the sections under it are not
    # qualified by it — renaming a project must not rename every section.
    level_ones = [h for h in headings if h[1] == 1]
    has_title = len(level_ones) == 1 and bool(headings) and headings[0][1] == 1
    title_id: str | None = None

    # (level, id) of the sections enclosing the current heading.
    stack: list[tuple[int, str]] = []
    section_starts: list[tuple[int, str]] = []  # (0-based line, id)
    for k, (i, level, raw) in enumerate(headings):
        end = headings[k + 1][0] - 1 if k + 1 < len(headings) else len(lines) - 1
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        number, title = _split_number(raw)
        if number:
            uid = number
        elif parent is None or parent == title_id:
            uid = _slug(title)
        else:
            uid = f"{parent}/{_slug(title)}"
        uid = claim(uid, i + 1)
        if k == 0 and has_title:
            title_id = uid
        out.units.append(
            Unit(
                uid,
                SECTION,
                source,
                i + 1,
                end + 1,
                title,
                level,
                parent,
                _sha(lines[i : end + 1]),
            )
        )
        stack.append((level, uid))
        section_starts.append((i, uid))

    if not headings:
        out.problems.append(
            f"{source}: no headings — it cannot be split into units without "
            "inventing structure; point Workers at the whole file instead"
        )

    decision_lines: set[int] = set()
    for i, line in enumerate(lines):
        if not outside[i]:
            continue
        m = _DECISION_ROW.match(line)
        if not m:
            continue
        parent = None
        for start, sid in section_starts:
            if start > i:
                break
            parent = sid
        name = re.sub(r"\*\*|__", "", m.group(2)).strip()
        decision_lines.add(i)
        out.units.append(
            Unit(
                claim(m.group(1), i + 1),
                DECISION,
                source,
                i + 1,
                i + 1,
                name,
                0,
                parent,
                _sha([line]),
            )
        )
    # Matched at the start of a line, so a line that merely cites `REQ-1` is not
    # a second `REQ-1`; heading lines and decision rows are already units.
    patterns = [re.compile(p) for p in extra_units]
    heading_lines = {i for i, _, _ in headings}
    taken_ids = {u.id for u in out.units}
    for i, line in enumerate(lines):
        if not patterns or not outside[i] or i in decision_lines or i in heading_lines:
            continue
        for pattern in patterns:
            m = pattern.match(line)
            if not m or not m.group(1):
                continue
            item_id = m.group(1)
            if item_id in taken_ids:
                out.problems.append(
                    f"{source}:{i + 1}: extra_units matched '{item_id}', which is "
                    "already the id of another unit — the item is not addressable; "
                    "change the pattern so its ids are distinct"
                )
                break
            parent = None
            for start, sid in section_starts:
                if start > i:
                    break
                parent = sid
            taken_ids.add(item_id)
            out.units.append(
                Unit(
                    claim(item_id, i + 1),
                    ITEM,
                    source,
                    i + 1,
                    i + 1,
                    line.strip()[:120],
                    0,
                    parent,
                    _sha([line]),
                )
            )
            break
    return out


def parse_file(
    path: Path, source: str | None = None, extra_units: list[str] | tuple[str, ...] = ()
) -> Parsed:
    """Units of one file, or a problem saying why it could not be read."""
    label = source if source is not None else str(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return Parsed(problems=[f"{label}: cannot be read ({e.strerror or e})"])
    return parse_text(text, label, extra_units)


def expand_paths(root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    """Registered spec paths as files, and a problem for each that is missing.

    A registered directory is handed over whole, so it is expanded to the
    Markdown files inside it — hidden directories skipped, sorted, so the
    order is the same on every machine. A registered file is taken as it is,
    whatever its extension: registering it was the decision.
    """
    files: list[str] = []
    problems: list[str] = []
    for rel in paths:
        target = root / rel
        if target.is_dir():
            found = sorted(
                p.relative_to(root).as_posix()
                for p in target.rglob("*")
                if p.is_file()
                and p.suffix.lower() in MARKDOWN_SUFFIXES
                and not any(
                    part.startswith(".") for part in p.relative_to(target).parts
                )
            )
            if not found:
                problems.append(
                    f"{rel}: a registered directory with no Markdown files in it"
                )
            files.extend(f for f in found if f not in files)
        elif target.is_file():
            clean = Path(rel).as_posix()
            if clean not in files:
                files.append(clean)
        else:
            problems.append(f"{rel}: registered, but nothing is there")
    return files, problems


def parse_paths(
    root: Path, paths: list[str], extra_units: list[str] | tuple[str, ...] = ()
) -> Parsed:
    """Units of every registered spec path, in registration order.

    Ids are unique across the whole set. When a later file reuses an id an
    earlier one already has, the later unit is addressed as `source#id` and a
    problem says so — the earlier file's ids never change because another file
    was added.
    """
    files, problems = expand_paths(root, paths)
    merged = Parsed(problems=problems)
    taken: set[str] = set()
    for rel in files:
        parsed = parse_file(root / rel, rel, extra_units)
        merged.files.append(rel)
        merged.total_lines += parsed.total_lines
        merged.lines.update(parsed.lines)
        merged.problems.extend(parsed.problems)
        for unit in parsed.units:
            if unit.id in taken:
                qualified = f"{rel}#{unit.id}"
                merged.problems.append(
                    f"{rel}:{unit.start}: '{unit.id}' is already a unit id in an "
                    f"earlier spec file — this one is addressed as '{qualified}'"
                )
                unit = Unit(
                    qualified,
                    unit.kind,
                    unit.source,
                    unit.start,
                    unit.end,
                    unit.title,
                    unit.level,
                    unit.parent,
                    unit.sha,
                )
            taken.add(unit.id)
            merged.units.append(unit)
    return merged
