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

🔴 **And the safe form is now a FILE, not a heredoc (SCRUM-69, absorbing
SCRUM-45 and SCRUM-22's transport residual).** Two things broke the heredoc
in the dogfood, and neither is rite's to fix inside the heredoc:

- the shell creates a temp file for every here-document, and mid-session it
  could not ("can't create temp file for here document: operation not
  permitted", a8, 2026-10-03). The Manager's only taught way to reach the
  person stopped working, and nothing told it another;
- a model that writes the end line twice makes the engine refuse the whole
  call before rite runs (1 refused call in 42, SCRUM-45).

So the Manager writes its text with its own Write tool into its OWN drafts
directory (`drafts_dir`) and names it: `--from-file <path>`. No shell
creates anything, nothing in the file is expanded, and there is no end line
to double. `read_draft` is the only way in. It accepts a regular file directly
inside this Manager's drafts directory, opened without following a link, and
nothing else, because a draft is sent in the Manager's name and must be one
only that Manager could have written (`manager_dir` is granted to its own
profile alone, MM8). A draft is consumed once its message is queued, so a
retry with the same path cannot send it twice. The heredoc on stdin is still
ACCEPTED; it is no longer TAUGHT.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import shlex
import stat
import sys
from pathlib import Path

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


FROM_A_FILE = (
    "⚠ Put the text in a FILE and name it, never in double quotes on the "
    "command line: there the shell runs anything in backticks or $( ) before "
    "rite sees it, and text you quote from a ticket, an issue or another "
    "Manager can contain them. This is the same reason your commit message "
    "goes in a file (`git commit -F`)."
)
"""Said beside a `--…-file` option in a GENERATED file.

⚠ **A generated file cannot carry a heredoc, and the first attempt at this
tried.** `heredoc` mints its delimiter with `secrets` precisely because the
quoted text is attacker-influenced, and that is right for an instruction
composed for one turn. A `CLAUDE.md` is written once and then regenerated by
`rite update` and compared against what is on disk, so a fresh delimiter
would differ on every comparison. Review measured what that actually costs,
which is not what the first draft of this paragraph guessed: the section is
not reported as hand-edited (its recorded hash matches its own content), it
is reported as REFRESHED on every single `rite update` and rewritten with a
new delimiter each time — and it drops out of `section_history`'s static set
for good, so no future release can show it to be rite's own.

The attempted way out was a placeholder delimiter plus a line telling the
reader to replace it. Review killed it, correctly: the placeholder ships in
the repository, so a reader that copies the command as printed — which is
what the instruction around it says to do — runs a heredoc whose delimiter
is a published string, and a ticket containing that line on its own ends the
heredoc and the rest is shell. That turns a mechanical guarantee into
compliance with prose, which is not a guarantee at all.

A file has neither problem: the path is an argument, nothing in the text is
expanded, and the instruction is the same bytes every time."""


def read(value: str) -> str:
    """The text a command was given: stdin when `value` is `-`.

    Raises `OnTheCommandLine` for anything else. The heredoc's own final
    newline is not part of the text."""
    if value != STDIN:
        raise OnTheCommandLine()
    return sys.stdin.read().removesuffix("\n")


class OnTheCommandLine(Exception):
    """Text given as an argument rather than on stdin."""


DRAFTS_DIRNAME = "drafts"
"""Under the Manager's own directory (`managers.manager_dir`)."""

DRAFT_LIMIT = 256 * 1024
"""The largest draft read, in bytes. A message to a person, a round or a
route is text a person reads; anything larger is a mistake, refused."""


def drafts_dir(root: Path, manager: str) -> Path:
    """Where Manager `manager` writes the text it sends (SCRUM-69). Its own
    directory is outside the project and granted only to its own boundary, so
    a draft here is one only this Manager could have written."""
    from rite_ai.managers import manager_dir

    return manager_dir(Path(root), manager) / DRAFTS_DIRNAME


class DraftRefused(Exception):
    """A `--from-file` path that is not a draft of this Manager's."""


def read_draft(root: Path, manager: str, given: str) -> tuple[str, Path]:
    """`(text, path)` for the draft `given` names: an absolute path, or a bare
    file name in this Manager's drafts directory.

    Refuses (`DraftRefused`), reading nothing, anything that is not a regular
    file DIRECTLY inside this Manager's own resolved drafts directory: a path
    elsewhere, `..`, a subdirectory, a link at the leaf (opened with
    `O_NOFOLLOW`), a directory, a fifo, or a file over `DRAFT_LIMIT`. The final
    newline an editor adds is not part of the text."""
    own = drafts_dir(root, manager)
    try:
        home = own.resolve(strict=True)
    except OSError:
        raise DraftRefused(
            f"there are no drafts yet: write your text to a file in {own} first"
        ) from None
    path = Path(given).expanduser()
    if not path.is_absolute():
        path = own / path
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        raise DraftRefused(f"{given} is not in your drafts directory, {own}") from None
    if parent != home or path.name in ("", ".", ".."):
        raise DraftRefused(
            f"{given} is not in your drafts directory, {own}: rite sends only "
            "what you wrote there, because it goes out in your name"
        )
    leaf = parent / path.name
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise DraftRefused(
            f"{leaf} does not exist. If you sent it already, it was removed "
            "once queued: write a new file for a new message"
        ) from None
    except OSError as e:
        # ELOOP: the leaf is a link. Said as what it is.
        why = "it is a link" if e.errno == errno.ELOOP else str(e)
        raise DraftRefused(f"{leaf} cannot be read as a draft: {why}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DraftRefused(f"{leaf} is not a regular file")
        if info.st_size > DRAFT_LIMIT:
            raise DraftRefused(
                f"{leaf} is {info.st_size} bytes; a message is at most {DRAFT_LIMIT}"
            )
        raw = os.read(fd, DRAFT_LIMIT + 1)
    finally:
        os.close(fd)
    if len(raw) > DRAFT_LIMIT:
        raise DraftRefused(f"{leaf} is over {DRAFT_LIMIT} bytes")
    return raw.decode("utf-8", errors="replace").removesuffix("\n"), leaf


def consume_draft(path: Path | None) -> str:
    """Remove a draft whose message is queued; "" or what went wrong.

    ⚠ Only after the message is queued: a send that failed keeps its draft, so
    the retry is the same command. Removed after, so the same command cannot
    send the same text twice (SCRUM-45's deliver-exactly-once)."""
    if path is None:
        return ""
    try:
        path.unlink()
    except FileNotFoundError:
        return ""
    except OSError as e:
        return (
            f"⚠ sent, but the draft {path} could not be removed ({e}): delete "
            "it, or do not run the same command again, or it is sent twice"
        )
    return ""


def file_form(
    root: Path, manager: str, command: str, name: str, placeholder: str
) -> str:
    """How a Manager sends a text: write the draft, then name it.

    `command` is the full command without its text argument. The path is
    quoted for the shell: a Manager's directory is under `Application
    Support` on macOS, which has a space."""
    path = drafts_dir(root, manager) / name
    return (
        f"1. Write {placeholder} to `{path}` with your Write tool: the whole "
        "text, exactly as it is to be read, and nothing else.\n"
        f"2. Run: {command} --from-file {shlex.quote(str(path))}"
    )


FILE_RULE = (
    "rite sends the file exactly as written: nothing in it is expanded, so "
    "backticks, $( ) and quotes are safe in it. Only a file in your drafts "
    "directory is accepted. Once sent it is removed, so write a new file for "
    "each message. ⚠ Never put the text on the command line: there the shell "
    "runs anything in backticks or $( ) before rite sees it, and text you "
    "quote from a ticket, an issue or another Manager can contain them."
)
"""Said once beside the file forms in a Manager's instructions."""


def split_first_line(text: str) -> tuple[str, str]:
    """`(first line, the rest)` of a text read from stdin, for a command that
    takes TWO texts through its one stdin (`rite ask --defer --while -`).

    🔴 **SCRUM-33.** `--while "<what you will do meanwhile>"` was free text in
    double quotes on the command line, the shape `reply`, `ask` and `route`
    were moved off (F14): the meanwhile names tickets, and tickets are written
    by other people. A process has one stdin, so the second text is its first
    line. One line on purpose: a meanwhile is a phrase, and a fixed boundary
    is one nobody writing a question can move."""
    first, _, rest = text.partition("\n")
    return first.strip(), rest


def refusal(
    command: str,
    placeholder: str,
    root: Path | None = None,
    manager: str = "",
    name: str = "message.md",
) -> str:
    """What a command says when its text came on the command line. With a
    Manager known, it teaches the file form (SCRUM-69); otherwise the
    heredoc, which a person at the host can still use."""
    expansion = (
        "In double quotes the shell runs anything in backticks or $( ) BEFORE "
        "rite sees the text — and if yours had any, it already ran. "
    )
    if root is not None and manager:
        return (
            "refusing: this command reads its text from a file or stdin, never "
            "the command line. "
            + expansion
            + "Put it in a file instead:\n"
            + file_form(root, manager, command, name, placeholder)
        )
    return (
        "refusing: this command reads its text from stdin, never the command "
        "line. "
        + expansion
        + "Send it through a quoted heredoc, where nothing is expanded:\n"
        + heredoc(f"{command} -", placeholder)
    )
