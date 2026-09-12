"""Context directory operations (SPEC §8.6).

The context directory holds `.md` files navigated by `INDEX.md`. Each entry
has a filename, a one-line description, and a "when to consult" trigger.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic


@dataclass
class ContextEntry:
    file: str
    trigger: str
    description: str


@dataclass
class IntegrityIssue:
    kind: str  # "orphan_file" | "missing_file" | "oversize"
    detail: str


_ROW_RE = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")

SIZE_LIMIT = 4096
COUNT_LIMIT = 30

# The single canonical INDEX.md template — `rite init`'s scaffold used to
# define its OWN copy of this (different prose, different column-divider
# width) for the empty-project case, while `_append_index_row` below wrote
# a second, shorter version whenever `add_context` had to create the file
# from scratch (e.g. after `rite init` never ran, or the file was deleted).
# The two had already drifted apart. One template now, owned by this
# domain module; `cli/init/scaffold.py` imports it rather than restating it.
CONTEXT_INDEX_TEMPLATE = """\
# Context Index

Project-specific knowledge — conventions, architecture decisions, domain
rules. Each entry needs a filename, a one-line description, and **when to
consult it** (a trigger, not a topic — see SPEC.md §8.6).

| File | When to consult | Description |
|------|-----------------|-------------|

This index grows as the project does — add a row here whenever you add a
file under `.rite/context/` (`rite context add` does this for you). A file
without an index entry, or an entry without a file, is a defect worth
fixing by hand until `rite doctor` checks for it automatically.
"""


def _context_dir(root: Path) -> Path:
    return root / ".rite" / "context"


def _index_path(root: Path) -> Path:
    return _context_dir(root) / "INDEX.md"


def list_context(root: Path) -> list[ContextEntry]:
    idx = _index_path(root)
    if not idx.is_file():
        return []
    entries: list[ContextEntry] = []
    for line in idx.read_text().splitlines():
        if line.lstrip("|").lstrip().startswith("-"):
            continue
        m = _ROW_RE.match(line)
        if m and m.group(1).strip() not in ("File", "---", ""):
            entries.append(
                ContextEntry(
                    file=m.group(1).strip(),
                    trigger=m.group(2).strip(),
                    description=m.group(3).strip(),
                )
            )
    return entries


def add_context(
    root: Path,
    filename: str,
    trigger: str,
    description: str,
    content: str = "",
    source_path: Path | None = None,
) -> str | None:
    """Add a context entry. Returns None on success, error string on failure."""
    ctx_dir = _context_dir(root)
    ctx_dir.mkdir(parents=True, exist_ok=True)
    idx = _index_path(root)

    existing = list_context(root)
    if any(e.file == filename for e in existing):
        return f"'{filename}' already in index"

    dest = ctx_dir / filename
    if source_path and source_path.is_file():
        shutil.copyfile(source_path, dest)
    elif content:
        write_atomic(dest, content)
    elif not dest.exists():
        heading = filename.replace(".md", "").replace("-", " ").title()
        write_atomic(dest, f"# {heading}\n\n")

    _append_index_row(idx, filename, trigger, description)
    return None


def remove_context(root: Path, filename: str) -> str | None:
    """Remove a context entry and its file. Returns None on success."""
    ctx_dir = _context_dir(root)
    idx = _index_path(root)

    existing = list_context(root)
    if not any(e.file == filename for e in existing):
        return f"'{filename}' not in index"

    file_path = ctx_dir / filename
    if file_path.is_file():
        file_path.unlink()

    _rewrite_index_without(idx, filename)
    return None


def check_integrity(root: Path) -> list[IntegrityIssue]:
    """Check for orphaned files, missing files, and oversize entries."""
    ctx_dir = _context_dir(root)
    if not ctx_dir.is_dir():
        return []

    issues: list[IntegrityIssue] = []
    entries = list_context(root)
    indexed_files = {e.file for e in entries}

    # Context DOCUMENTS only. The directory also holds `INDEX.md` and the
    # `INDEX.md.lock` sidecar that `_locked_index` flocks — machinery, not
    # knowledge, and reporting the lock file as "exists but has no index
    # entry" would send a user looking for a context file that is not one.
    actual_files = {
        f.name
        for f in ctx_dir.iterdir()
        if f.is_file() and f.name != "INDEX.md" and f.suffix != ".lock"
    }

    for orphan in actual_files - indexed_files:
        issues.append(
            IntegrityIssue("orphan_file", f"'{orphan}' exists but has no index entry")
        )

    for missing in indexed_files - actual_files:
        issues.append(
            IntegrityIssue("missing_file", f"'{missing}' in index but file not found")
        )

    for f in actual_files & indexed_files:
        size = (ctx_dir / f).stat().st_size
        if size > SIZE_LIMIT:
            issues.append(
                IntegrityIssue(
                    "oversize",
                    f"'{f}' is {size} bytes (limit {SIZE_LIMIT})",
                )
            )

    if len(actual_files) > COUNT_LIMIT:
        issues.append(
            IntegrityIssue(
                "oversize",
                f"{len(actual_files)} context files (limit {COUNT_LIMIT})",
            )
        )

    return issues


def _is_divider_row(line: str) -> bool:
    """Same heuristic `list_context` already uses to skip the header
    divider — kept in one place so insertion and listing can never
    disagree about what counts as a table row."""
    return line.lstrip("|").lstrip().startswith("-")


def _locked_index(idx: Path):
    """Exclusion around `context/INDEX.md`'s read-modify-write.

    Both writers below read the whole file, change one row and write it
    all back, and neither was locked. Measured through the public
    `add_context`, six concurrent additions over five rounds: every round
    lost entries and the worst kept one of six. The index is how a
    session finds out what project knowledge exists, so a lost row is
    knowledge that is on disk and invisible."""
    from rite_ai.state import locked

    return locked(idx)


def _append_index_row(idx: Path, filename: str, trigger: str, desc: str) -> None:
    """Insert the new row directly after the table (its last existing data
    row, or its header divider if the table is still empty) — NEVER at the
    end of the file. `rite init`'s template (§8.6) puts explanatory prose
    after the table; a naive end-of-file append used to land new rows
    after that prose instead of in the table — a file that LOOKS like a
    valid index right up until a reader notices the new row isn't actually
    in it, which is exactly the "generated content asserting unverified
    facts" class this project's own review checklist now has a line for."""
    new_row = f"| `{filename}` | {trigger} | {desc} |\n"

    with _locked_index(idx):
        if not idx.is_file():
            write_atomic(idx, CONTEXT_INDEX_TEMPLATE)

        lines = idx.read_text().splitlines(keepends=True)
        insert_at = None
        for i, line in enumerate(lines):
            stripped = line.rstrip("\n")
            if _is_divider_row(stripped):
                insert_at = i + 1
            elif _ROW_RE.match(stripped):
                insert_at = i + 1

        if insert_at is None:
            # No recognisable table at all (a hand-edited or foreign
            # file) — EOF append is the least-wrong fallback, not the
            # common case.
            lines.append(new_row)
        else:
            lines.insert(insert_at, new_row)

        write_atomic(idx, "".join(lines))


def _rewrite_index_without(idx: Path, filename: str) -> None:
    if not idx.is_file():
        return
    with _locked_index(idx):
        lines = idx.read_text().splitlines(keepends=True)
        kept = []
        for line in lines:
            m = _ROW_RE.match(line)
            if m and m.group(1).strip() == filename:
                continue
            kept.append(line)
        write_atomic(idx, "".join(kept))
