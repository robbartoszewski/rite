"""A Manager's words reach rite on stdin, never on the command line (F14).

**What happened** (the v0.6.0 dogfood, 2026-09-27, a Claude Owner): the
Manager ran `rite reply --manager lead "… \\`rite update --files-only\\` …"`.
The shell ran the command in backticks before rite saw the text — it changes
files — and spliced its output into the reply, which went to the person. The
engine did not ask: Claude Code allows a command substitution whose inner
command is on the allowlist, and `python`, `rm`, `gh`, `git` and `env` are.

**Why this is command injection, not a quoting slip.** A Manager's text is
often not its own: it quotes a ticket, a maintainer's issue, a PR body, a
routed message. Put inside double quotes, anyone who wrote that text can run
a command on this machine. So the text goes in on stdin, through a QUOTED
heredoc (`<<'…'`), where the shell expands nothing, and the text on the
command line is refused.

**The delimiter is rite's, and unguessable.** A quoted heredoc ends at the
first line that is exactly its delimiter, and everything after that line is
shell again. A fixed delimiter (`EOF`) is one a ticket can contain on a line
of its own. So each set of instructions names a fresh one, which nobody who
writes a ticket can know.

**What this does not do.** rite cannot stop a model from putting a
substitution into some other command (`gh issue comment --body "…"`). That is
the engine's Bash tool, which rite does not own. What rite owns is the
channel to the person and between Managers: those commands take no text on
the command line, and their instructions teach the one safe form.
"""

from __future__ import annotations

import re
import secrets
import sys

STDIN = "-"
"""The only value a text argument takes: read the text from stdin."""

RULE = (
    "Your text replaces the <…> line, exactly as written, on as many lines "
    "as it needs, and the last line ends it: copy it exactly, at the start of "
    "its line with nothing before it, ONCE, with nothing after it. A second "
    "copy, or anything after it, is a new shell command, and it is refused. "
    "⚠ Never put "
    "the text in double quotes on the command line instead: the shell runs "
    "anything in backticks or $( ) there before rite sees it, and text you "
    "quote from a ticket, an issue or another Manager can contain them."
)
"""Said once beside the heredocs in a Manager's instructions."""


def _delimiter() -> str:
    """A heredoc delimiter no ticket can contain by accident or design."""
    return f"RITE_TEXT_{secrets.token_hex(6)}"


# Any hex length, not only `_delimiter`'s twelve: `--help` shows six
# (`RITE_TEXT_1f2e3d`), and a Manager may copy the example.
_END_LINE = re.compile(r"RITE_TEXT_[0-9a-f]+")


def stray_end(command: str) -> str:
    """The delimiter, when `command` is one of rite's heredoc end lines run
    as a command of its own; otherwise "".

    🔴 SCRUM-22. A Manager wrote the end line twice. The heredoc closes at
    the first, so the shell saw the second as a separate command named
    `RITE_TEXT_…`, and the engine refused that part (and with it the whole
    call, so nothing was sent). rite's own form was permitted; the stray line
    was not. This lets the report say so, instead of naming the delimiter as
    a program to allowlist."""
    words = command.split()
    return words[0] if words and _END_LINE.fullmatch(words[0]) else ""


def heredoc(command: str, placeholder: str, end: str | None = None) -> str:
    """`command` reading `placeholder` from a quoted heredoc. `command` ends
    with the `-` argument.

    ⚠ Not indented, unlike the rest of an instruction block: the heredoc ends
    only at a line that is exactly its delimiter, so an indented end line
    copied as shown would never end it."""
    end = end or _delimiter()
    return f"{command} <<'{end}'\n{placeholder}\n{end}"


def read(value: str) -> str:
    """The text a command was given: stdin when `value` is `-`.

    Raises `OnTheCommandLine` for anything else. The heredoc's own final
    newline is not part of the text."""
    if value != STDIN:
        raise OnTheCommandLine()
    return sys.stdin.read().removesuffix("\n")


class OnTheCommandLine(Exception):
    """Text given as an argument rather than on stdin."""


def refusal(command: str, placeholder: str) -> str:
    """What a command says when its text came on the command line."""
    return (
        "refusing: this command reads its text from stdin, never the command "
        "line. In double quotes the shell runs anything in backticks or $( ) "
        "BEFORE rite sees the text — and if yours had any, it already ran. "
        "Send it through a quoted heredoc, where nothing is expanded:\n"
        + heredoc(f"{command} -", placeholder)
    )
