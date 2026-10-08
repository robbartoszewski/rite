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

So the Manager writes its text with its own file-writing tool (not the
shell) into its OWN drafts directory (`drafts_dir`) and names it:
`--from-file <path>`. No shell creates anything, nothing in the file is
expanded, and there is no end line to double. `read_draft` is the only way
in. It accepts a regular file directly inside this Manager's drafts
directory, reached without following a link, and nothing else, because a
draft is sent in the Manager's name: no other Manager's profile grants that
directory (`manager_dir`, MM8), so of the Managers only this one can have
written it. (The person and rite's supervisor, outside every sandbox, can
too.) A draft is locked while it is sent and consumed once its message is
queued, so neither a retry nor a second send at the same moment sends it
twice. The heredoc on stdin is still
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


class Draft:
    """A draft read for sending, held LOCKED until it is consumed or the
    process ends (`read_draft`).

    `path` is for messages only: nothing is ever reopened by it. Every later
    step goes through `_dir`, the drafts directory opened once without
    following a link."""

    def __init__(self, path: Path, dir_fd: int, fd: int, name: str, info) -> None:
        self.path = path
        self._dir = dir_fd
        self._fd = fd
        self._name = name
        self._id = (info.st_dev, info.st_ino)

    def __str__(self) -> str:
        return str(self.path)

    def close(self) -> None:
        """Release the lock and the directory, consumed or not."""
        for fd in (self._fd, self._dir):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._fd = self._dir = -1

    def consume(self) -> str:
        """Remove the draft, still under its lock; "" or what went wrong.

        Only the file that was READ is removed, and never a newer one:
        the name is first RENAMED, atomically, to a name of rite's own
        that nothing else knows, and only that is checked and unlinked. A
        stat-then-unlink of the name left a window in which a draft the
        Manager wrote meanwhile was the one removed, unsent (review N1).
        Through the drafts directory's own descriptor throughout."""
        taken = f".sent-{secrets.token_hex(8)}"
        try:
            os.rename(self._name, taken, src_dir_fd=self._dir, dst_dir_fd=self._dir)
            now = os.stat(taken, dir_fd=self._dir, follow_symlinks=False)
            if (now.st_dev, now.st_ino) == self._id:
                os.unlink(taken, dir_fd=self._dir)
            else:
                kept = self.path.parent / taken
                return (
                    f"⚠ sent; but {self.path} had been replaced by a new file "
                    f"meanwhile, which was NOT sent. It is kept, as {kept}: "
                    "send it with that path if it is meant to go"
                )
        except FileNotFoundError:
            pass
        except OSError as e:
            return (
                f"⚠ sent, but the draft {self.path} could not be removed ({e}): "
                "delete it, or do not run the same command again, or it is "
                "sent twice"
            )
        finally:
            self.close()
        return ""


def _refuse_open(what: Path, e: OSError) -> DraftRefused:
    # ELOOP (or ENOTDIR for a directory link on Linux): a link, said as what
    # it is.
    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        return DraftRefused(f"{what} cannot be read as a draft: it is a link")
    return DraftRefused(f"{what} cannot be read as a draft: {e.strerror or e}")


def _open_drafts(root: Path, manager: str) -> int:
    """The drafts directory, opened with no link followed at the Manager's
    own directory or at `drafts`.

    🔴 **Links here were trusted (SCRUM-69 reviews).** The Manager can write
    inside its own directory, so it could replace `drafts` with a link to
    anywhere (`~/.ssh`); and, measured under seatbelt, it could rename its
    whole directory away and link another Manager's in its place. Inside its
    profile neither reaches anything; but a person at the host running `rite
    reply --manager <m> --from-file <name>` runs rite unconfined, and
    following either link read, sent and removed a file that was not this
    Manager's. The profile now refuses the rename (`enclosure`'s deny on the
    directory itself), and this refuses to follow one anyway.

    `O_NOFOLLOW` applies to a path's LAST component, which is exactly the
    Manager's directory: everything above it is rite's own (a rite home
    through a link) and the Manager can write none of it. Not opened from
    the parent, which a Manager's profile does not let it read."""
    from rite_ai.managers import manager_dir

    own = drafts_dir(root, manager)
    home = manager_dir(Path(root), manager)

    def opened(name, shown: Path, dir_fd: int | None = None) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            raise DraftRefused(
                f"there are no drafts yet: write your text to a file in {own} first"
            ) from None
        except OSError as e:
            raise _refuse_open(shown, e) from None

    base = opened(home, home)
    try:
        return opened(DRAFTS_DIRNAME, own, dir_fd=base)
    finally:
        os.close(base)


def read_draft(root: Path, manager: str, given: str) -> tuple[str, Draft]:
    """`(text, draft)` for the draft `given` names: a path, absolute or from
    the current directory, to a file directly in this Manager's drafts
    directory. The final newline an editor adds is not part of the text.

    Refuses (`DraftRefused`), sending nothing, anything that is not a regular
    file DIRECTLY inside this Manager's own drafts directory: a path
    elsewhere, a subdirectory, a link anywhere from the Manager's directory
    down (`_open_drafts`, `O_NOFOLLOW` at the leaf), a directory, a fifo, a
    file with a second hard link or another owner, or one over `DRAFT_LIMIT`.

    🔴 **Exactly once, with no window (SCRUM-69 review).** Two `rite reply
    --from-file <drafts>/reply.md` started together both read the file before either
    removed it, so the text went twice. The draft is now LOCKED (`flock`,
    exclusive, not waiting) from the moment it is read until `Draft.consume`
    has removed it, and after taking the lock rite checks that the name
    still names the file it opened. The second run either fails to take the
    lock (refused: being sent) or takes it after the first has removed the
    name (refused: already sent). Kernel locks die with their process, so a
    crashed rite never leaves a draft unsendable. A lock rather than a
    rename to a claimed name: a refusal after reading then keeps the draft
    exactly where the Manager wrote it, with nothing to put back — and
    putting a renamed draft back is itself a race with a newer one."""
    import fcntl

    path = Path(given).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    name = path.name
    own = drafts_dir(root, manager)
    if name in ("", ".", ".."):
        raise DraftRefused(f"{given} is not a file in your drafts directory, {own}")
    dir_fd = _open_drafts(root, manager)
    fd = -1
    try:
        try:
            given_dir = os.stat(path.parent)
        except OSError:
            given_dir = None
        here = os.fstat(dir_fd)
        if given_dir is None or (given_dir.st_dev, given_dir.st_ino) != (
            here.st_dev,
            here.st_ino,
        ):
            raise DraftRefused(
                f"{given} is not in your drafts directory, {own}: rite sends "
                "only what you wrote there, because it goes out in your name"
            )
        leaf = own / name
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd
            )
        except FileNotFoundError:
            raise DraftRefused(
                f"{leaf} does not exist. If you sent it already, it was removed "
                "once queued: write a new file for a new message"
            ) from None
        except OSError as e:
            raise _refuse_open(leaf, e) from None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DraftRefused(f"{leaf} is not a regular file")
        if info.st_nlink != 1:
            raise DraftRefused(
                f"{leaf} has {info.st_nlink} hard links: a draft is a file you "
                "wrote, not a link to another"
            )
        if info.st_uid != os.getuid():
            raise DraftRefused(f"{leaf} is not owned by you")
        if info.st_size > DRAFT_LIMIT:
            raise DraftRefused(
                f"{leaf} is {info.st_size} bytes; a message is at most {DRAFT_LIMIT}"
            )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DraftRefused(
                f"{leaf} is being sent by another rite right now: it is sent "
                "once, so this one sends nothing"
            ) from None
        try:
            now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            now = None
        if now is None or (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise DraftRefused(
                f"{leaf} was sent and removed while this rite waited to read "
                "it: it is sent once, so this one sends nothing"
            )
        # Read to the end: one `read` may return less than the file.
        chunks: list[bytes] = []
        size = 0
        while size <= DRAFT_LIMIT:
            chunk = os.read(fd, DRAFT_LIMIT + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b"".join(chunks)
        if len(raw) > DRAFT_LIMIT:
            raise DraftRefused(f"{leaf} is over {DRAFT_LIMIT} bytes")
        draft = Draft(leaf, dir_fd, fd, name, info)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        os.close(dir_fd)
        raise
    return raw.decode("utf-8", errors="replace").removesuffix("\n"), draft


def consume_draft(draft: Draft | None) -> str:
    """Remove a draft whose message is queued; "" or what went wrong.

    ⚠ Only after the message is queued: a send that failed keeps its draft, so
    the retry is the same command. Removed after, so the same command cannot
    send the same text twice (SCRUM-45's deliver-exactly-once)."""
    if draft is None:
        return ""
    return draft.consume()


def file_form(
    root: Path, manager: str, command: str, name: str, placeholder: str
) -> str:
    """How a Manager sends a text: write the draft, then name it.

    `command` is the full command without its text argument. The path is
    quoted for the shell: a Manager's directory is under `Application
    Support` on macOS, which has a space."""
    path = drafts_dir(root, manager) / name
    return (
        f"1. Write {placeholder} to `{path}` with your file-writing tool, not the "
        "shell: the whole text, exactly as it is to be read, and nothing else.\n"
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
