"""Project labelling — every message says which project it is about.

rite is designed for a machine running several projects at once (§8.9),
so a message that arrives on its own — a scheduler tick, a watchdog
verdict, a handover comment landing on a board — has no surrounding
context to identify it. Four decisions arriving with no project name is
four decisions the reader cannot act on; this module is what puts the
name on them.

**The name carries the meaning; the colour is decoration.** Every label
renders as a dot followed by the project name, and the name is always
present. Colour is added only when it can be seen and only ever to the
dot, so the label reads identically when piped to a file, appended to
`scheduler.log` by cron, quoted in a ticket comment, or read by someone
who cannot distinguish the colours at all.

**Stored state gets the bare name, not a rendered label.** Anything
persisted — an outbox payload, a handover snapshot — records `project`
as a plain string. Rendering is done where the message is displayed, so
no escape sequence or decorative character is ever written into a file
another program has to parse.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import yaml

# Named colours rather than 256-colour indexes: every terminal worth
# supporting has these, and with the name always present the palette only
# has to make neighbouring projects look different, not encode anything.
#
# Red is deliberately absent. It reads as "error" everywhere else in this
# tool, and a project that happened to hash onto it would look like a
# failing one every time it spoke.
PALETTE: tuple[str, ...] = (
    "cyan",
    "green",
    "yellow",
    "magenta",
    "blue",
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_blue",
)

# U+25CF BLACK CIRCLE — the large dot. `*` is the fallback for an output
# encoding that cannot represent it (a stray `?` or a UnicodeEncodeError in
# a cron log is a worse outcome than a plain asterisk).
DOT = "●"
ASCII_DOT = "*"


def project_name(root: Path) -> str:
    """The project's name, by the cheapest route that always answers.

    Deliberately NOT `load_project` — that returns errors when any of the
    three config files is missing or malformed, and a config error is
    exactly the kind of message that reaches someone with no context. A
    label that disappears whenever the project is broken is missing
    precisely when it is needed, so this reads `brief.yaml` directly,
    tolerates every failure, and falls back to the directory name, which
    exists by definition.
    """
    brief = root / ".rite" / "brief.yaml"
    try:
        raw = yaml.safe_load(brief.read_text())
        name = raw["project"]["name"]
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        pass
    resolved = root.resolve()
    return resolved.name or str(resolved)


# Bounded so a long project name cannot push the meaningful part of a
# tmux or sandbox name off the end of a terminal listing.
_SLUG_MAX = 24


def project_slug(root: Path) -> str:
    """A project's name, shaped to sit inside a session or sandbox name.

    `<readable-name>-<6 hex of the resolved path>`, e.g. `acme-3f9a2c`.

    **Both halves earn their place.** The name is what a human reads in
    `tmux ls` or `yoloai ls` — the whole point of the convention is that a
    name reaching a person says which project it belongs to. The path hash
    is what keeps it unique: two projects legitimately share a name (two
    checkouts, two clients' `backend`), and a listing that shows the same
    name twice is worse than one showing a path, because the reader
    believes it is unambiguous.

    Derived from the PATH rather than from registration order or a stored
    id, so it is stable across runs without anything needing to persist
    it — the property `pool.slot_name` depends on to find its own sessions
    again rather than accumulating orphans.

    ⚠ Renaming a project in `brief.yaml` changes the readable half and so
    changes the slug. Sessions and sandboxes created under the old name
    are orphaned, visibly, under a name that still says which project they
    came from. That is the accepted cost of putting a human-meaningful
    name first; the alternative is a listing nobody can read.
    """
    import re

    name = project_name(root)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return f"{slug or 'project'}-{project_digest(root)}"


def project_digest(root: Path) -> str:
    """The path half of `project_slug`, on its own.

    The readable half changes when somebody renames a project in
    `brief.yaml`; this does not, because it is a digest of the resolved path.
    Anything that needs to recognise a project's OWN sessions or sandboxes
    across a rename matches on this rather than on the whole slug — otherwise
    a rename orphans them silently, and a cap that counts them stops counting
    the things it exists to bound.
    """
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:6]


def colour_for(name: str) -> str:
    """A project's colour, derived from its name.

    Stable across sessions, machines and checkouts, which registration
    order could never be: two machines that added the same projects in a
    different order would disagree about every colour, and a project
    removed and re-added would silently change.

    SHA-256 rather than `hash()` on purpose — Python randomises string
    hashing per process unless PYTHONHASHSEED is pinned, so `hash()` would
    give a project a different colour on every single invocation.
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return PALETTE[int.from_bytes(digest[:8], "big") % len(PALETTE)]


def _dot_for(stream) -> str:
    """`●` unless the stream's encoding cannot carry it."""
    encoding = getattr(stream, "encoding", None) or ""
    try:
        DOT.encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return ASCII_DOT
    return DOT


def colour_enabled(stream=None) -> bool:
    """Whether to emit colour at all.

    Three ways to say no, all of them honoured: not a terminal (a pipe, a
    file, cron's redirect into `scheduler.log`), NO_COLOR set to anything
    at all (the cross-tool convention), or TERM=dumb.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


@dataclass(frozen=True)
class ProjectLabel:
    name: str
    colour: str

    def render(self, stream=None) -> str:
        """`● name`, coloured only if this stream can show colour."""
        dot = _dot_for(stream if stream is not None else sys.stdout)
        if colour_enabled(stream):
            dot = click.style(dot, fg=self.colour, bold=True)
        return f"{dot} {self.name}"

    def plain(self) -> str:
        """`● name` with no escape sequences, whatever the terminal is —
        for text that will be stored or sent somewhere else."""
        return f"{DOT} {self.name}"


def label(root: Path) -> ProjectLabel:
    name = project_name(root)
    return ProjectLabel(name=name, colour=colour_for(name))


def prefix(root: Path, stream=None) -> str:
    """The label plus its separator, ready to sit in front of a message."""
    return f"{label(root).render(stream)}  "


def decorate(root: Path, message: str, stream=None) -> str:
    """One message, labelled. Multi-line messages get the label on every
    line: a block whose second line scrolled into view on its own would
    otherwise be exactly the unattributed text this exists to prevent."""
    pre = prefix(root, stream)
    return "\n".join(f"{pre}{line}" for line in message.split("\n"))
