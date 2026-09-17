"""Sections of a generated `CLAUDE.md`, and the marker that records what rite
wrote in each.

`rite init` and `rite add worker` write `CLAUDE.md` once, and the file is meant
to be edited — so a later rite cannot simply regenerate it without destroying
those edits, and without regenerating it an existing project never receives
anything a newer rite ships.

The marker makes that decidable per section. Every generated `## ` section is
preceded by `<!-- rite:sha256=<16 hex> -->`, the hash of the section as rite
wrote it. A section whose current text still hashes to its marker has not been
touched since, and may be replaced; one that does not has been edited, and is
the user's. The marker sits on the line ABOVE the heading, so the heading and
its body read exactly as before.

`parse` is lossless: joining the blocks' `raw` text reproduces the input byte
for byte, which is what lets a refresh that changes nothing change nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MARKER_RE = re.compile(r"^<!-- rite:sha256=([0-9a-f]{16}) -->$")
SPEC_START = "<!-- rite:spec -->"
SPEC_END = "<!-- /rite:spec -->"


def section_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]


@dataclass
class Block:
    kind: str
    """`preamble`, `section`, `spec` (a `<!-- rite:spec -->` block, which
    `rite spec add` owns) or `text` (anything else between them)."""

    heading: str
    raw: str
    recorded: str | None = None
    """The hash in this section's marker, or None for an unmarked section."""

    @property
    def content(self) -> str:
        lines = self.raw.splitlines(keepends=True)
        if self.recorded is not None:
            lines = lines[1:]
        return "".join(lines).strip()


def parse(text: str) -> list[Block]:
    lines = text.splitlines(keepends=True)
    blocks: list[Block] = []
    current = Block("preamble", "", "")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == SPEC_START:
            if current.raw:
                blocks.append(current)
            raw = []
            while i < len(lines):
                raw.append(lines[i])
                i += 1
                if raw[-1].strip() == SPEC_END:
                    break
            while i < len(lines) and not lines[i].strip():
                raw.append(lines[i])
                i += 1
            blocks.append(Block("spec", "", "".join(raw)))
            current = Block("text", "", "")
            continue
        marker = MARKER_RE.match(stripped)
        if marker and i + 1 < len(lines) and lines[i + 1].startswith("## "):
            if current.raw:
                blocks.append(current)
            heading = lines[i + 1].rstrip("\n")[3:].strip()
            current = Block("section", heading, line + lines[i + 1], marker.group(1))
            i += 2
            continue
        if line.startswith("## "):
            if current.raw:
                blocks.append(current)
            current = Block("section", line.rstrip("\n")[3:].strip(), line)
            i += 1
            continue
        current.raw += line
        i += 1
    if current.raw:
        blocks.append(current)
    return blocks


def mark_sections(text: str) -> str:
    """`text` with a marker above every `## ` section, recording its hash."""
    out = []
    for block in parse(text):
        if block.kind == "section":
            lines = block.raw.splitlines(keepends=True)
            if block.recorded is not None:
                lines = lines[1:]
            body = "".join(lines)
            out.append(f"<!-- rite:sha256={section_hash(body)} -->\n{body}")
        else:
            out.append(block.raw)
    return "".join(out)


def back_over_marker(text: str, newline_index: int) -> int:
    """For the `\\n` that precedes a heading, the `\\n` that precedes that
    heading's marker, if it has one — so an insertion placed "before this
    heading" does not separate the heading from its marker."""
    if newline_index <= 0:
        return newline_index
    prev = text.rfind("\n", 0, newline_index)
    if MARKER_RE.match(text[prev + 1 : newline_index].strip()):
        return prev
    return newline_index
