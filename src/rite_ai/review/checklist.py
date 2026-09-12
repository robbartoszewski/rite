"""Parse review checklists (SPEC §7).

A checklist is a Markdown file with checkbox items under category headings.
Each item is a `- [ ] ...` line. The heading it falls under becomes its category.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChecklistItem:
    category: str
    text: str
    line: int


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
_ITEM_RE = re.compile(r"^-\s+\[[ x]\]\s+(.+)", re.IGNORECASE)


def load_checklist(path: Path) -> list[ChecklistItem]:
    """A checklist item's text may wrap onto indented continuation lines —
    every item in this project's own default template does, for readability
    at 88-ish columns. A continuation line is indented, non-blank text
    immediately following a bullet, before the next bullet, heading, or
    blank line. Fixed 2026-09-09: the previous version matched only the
    bullet's own line and silently dropped every continuation line — a
    quiet, byte-for-byte instance of the "generated content asserting
    unverified facts" failure this same checklist file now has a line
    for, since `format_checklist_prompt` was handing review agents a
    truncated item with no indication anything was missing."""
    if not path.is_file():
        return []
    items: list[ChecklistItem] = []
    current_heading = "General"
    current_item: ChecklistItem | None = None
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_heading = heading_match.group(2).strip()
            current_item = None
            continue
        item_match = _ITEM_RE.match(line)
        if item_match:
            current_item = ChecklistItem(
                category=current_heading,
                text=item_match.group(1).strip(),
                line=lineno,
            )
            items.append(current_item)
            continue
        if current_item is not None and line.strip() and line[:1].isspace():
            current_item.text = f"{current_item.text} {line.strip()}"
        else:
            current_item = None
    return items


# Roughly this project's own source width. Items are authored wrapped at
# about this in the markdown; `load_checklist` joins the continuation lines
# back into one string, so without re-wrapping here the join is all anyone
# ever sees.
_WRAP_WIDTH = 88


def format_checklist_prompt(items: list[ChecklistItem]) -> str:
    """Format checklist items as a prompt section for review agents.

    Re-wrapped, because this is also what a human reads. `rite review` is
    the only caller (it prints a `# checklist:` provenance line of its own
    above this output) and both review templates now make it mandatory, so
    its output is the checklist as far as every reviewer is concerned —
    and `load_checklist` deliberately joins each item's continuation lines
    into one string, which turned the longest line of rite's own checklist
    into over 500 unbroken characters. Found by running the command in the repo
    that ships it, which nobody had done, which is the same reason the
    checklist itself did not exist here.
    """
    if not items:
        return "No checklist items found."
    by_category: dict[str, list[str]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item.text)
    parts = []
    for cat, texts in by_category.items():
        parts.append(f"### {cat}")
        for text in texts:
            parts.append(
                textwrap.fill(
                    text,
                    width=_WRAP_WIDTH,
                    initial_indent="- ",
                    subsequent_indent="  ",
                    break_long_words=False,  # never split a `path/like/this`
                    break_on_hyphens=False,
                )
            )
        parts.append("")
    return "\n".join(parts)
