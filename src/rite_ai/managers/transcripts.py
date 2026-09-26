"""Which provider session was this project's most recent one.

**Without this there is nothing to resume.** `--resume <id>` needs an id,
and a draft of the supervisor took the id from an injectable whose default
returned `""` — so every "resumption" ran a bare `claude` with no flag and
no prior context, and looked identical from outside to one that worked.
That is the week's recurring shape: a parameter whose default silently
means "do nothing" is an uncalled function wearing a different hat.

⚠ **Claude Code specific, and named so.** The transcript layout — a
directory per project under `~/.claude/projects/`, named by the project's
absolute path with separators replaced, holding one `<session-id>.jsonl`
per session — is one provider's private arrangement, not an interface. It
lives in its own module so the day a second provider exists, the thing that
has to be replaced is a file rather than a line inside the supervisor
(§9.14.11: there is no adapter yet, and this is part of why).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from rite_ai.budget import default_transcripts_dir

_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def session_id_problem(value: str) -> str:
    """Why `value` cannot be a session id, or "" if it can.

    ⚠ **This is what keeps a file's contents out of a shell.** The id is
    read from a transcript's `sessionId` field and reaches
    `tmux new-session` as part of a command string, which tmux runs through
    `sh -c`. Measured before this existed: a `sessionId` of
    `abc$(touch FILE)` created the file, and so did the backtick spelling —
    the substitution ran before `claude` was even reached. Nothing between
    the file and the shell looked at the value.

    **A positive shape, not an escape routine.** Escaping is a claim about
    every metacharacter of a shell nobody here chose; this says what a
    session id is MADE OF and refuses everything else, so a spelling nobody
    thought of is excluded by default rather than by enumeration.

    ⚠ **The leading character is a rule of its own, and not about quoting.**
    An id starting with `-` is read by the engine as a FLAG, so
    `--resume -rf` is a different failure with the same cause: it never
    reaches a shell and is still not an id.

    **Observed rather than declared, and deliberately wider than the
    observation.** Every one of 140 transcripts on the machine this was
    written on carried a canonical 36-character lowercase UUID. The rule
    admits any identifier token to that length instead, because pinning it
    to a UUID would make rite stop resuming the day the provider changes
    its id format — and the property actually needed is "cannot act in a
    shell", which this delivers in full while surviving that change. The
    measurement is recorded so a later reader can tighten it knowingly.
    """
    if not value:
        return "it is empty"
    if not _SESSION_ID.fullmatch(value):
        return (
            f"{value!r} is not shaped like a session id — letters, digits, "
            "'-' and '_' only, starting with a letter or digit, at most 128 "
            "characters"
        )
    return ""


def project_transcript_dir(root: Path, base: Path | None = None) -> Path:
    """Claude Code's directory for a project root.

    The name is the absolute path with every character that is not an ASCII
    letter or digit replaced by `-`, which is observable rather than
    documented — derived by reading this machine's own layout, and the
    reason this function exists instead of the rule being inlined somewhere
    it would be mistaken for a spec.

    ⚠ **It replaced only `/`, and was wrong for most real paths.** Read off
    directories Claude Code itself created on this machine:

        /private/var/folders/4p/fvll8_4s5…/T/permchk-7js6aj5r
          -> -private-var-folders-4p-fvll8-4s5…-T-permchk-7js6aj5r
        /Users/…/deployment/.claude/worktrees/infallible-easley-86b93f
          -> -Users-…-deployment--claude-worktrees-infallible-easley-86b93f

    `_` and `.` become `-` too. With only `/` replaced, rite looked in a
    directory that did not exist for any project whose path held an
    underscore, a dot or a space, so `latest_session_id` found nothing:
    mid-run resumption refused ("no transcript was found to resume from")
    and nothing was ever designated. Found while building the designation
    membership check (C8), which would have rejected every such project.

    Non-ASCII characters are assumed to follow the same rule and that is NOT
    measured — no directory on this machine had one.
    """
    where = base if base is not None else default_transcripts_dir()
    resolved = str(Path(root).resolve())
    return where / re.sub(r"[^A-Za-z0-9]", "-", resolved)


def latest_session_id(root: Path, since: float = 0.0, base: Path | None = None) -> str:
    """The most recent session for this project, or "".

    `since` filters to transcripts touched after a moment — so a supervisor
    resuming picks up the session IT started rather than one from last week
    that happens to be the newest on disk. Returns "" rather than guessing:
    a wrong id passed to `--resume` looks identical from outside to a right
    one, which is exactly the failure this module exists to stop.
    """
    directory = project_transcript_dir(root, base)
    try:
        candidates = [p for p in directory.glob("*.jsonl") if p.is_file()]
    except OSError:
        return ""
    if since > 0:
        candidates = [p for p in candidates if _mtime(p) >= since]
    if not candidates:
        return ""
    # ⚠ **A TIE IS THE COMMON CASE ON LINUX, NOT THE EXOTIC ONE.** File
    # timestamps come from the kernel's coarse clock, so two writes in one
    # tick share an mtime to the nanosecond — measured in a container, 197 of
    # 200 back-to-back writes did. `max()` then kept whichever file the
    # directory listing happened to return first, so a Manager could resume
    # the WRONG conversation, and the test for this passed on CI by luck while
    # failing 24 of 30 runs in the container. Ties are compared in integer
    # nanoseconds and broken by `_break_tie`, from inside the files — never by
    # a finer clock, which would only move the same bug to a faster machine.
    stamps = {p: _mtime_ns(p) for p in candidates}
    newest_ns = max(stamps.values())
    tied = [p for p in candidates if stamps[p] == newest_ns]
    if len(tied) > 1:
        return _break_tie(tied)
    newest = tied[0]
    # The filename IS the session id in this layout, but it is confirmed
    # against the file's own contents rather than trusted: a stray `.jsonl`
    # in that directory would otherwise become an id nobody can resume.
    stated = _stated_session_id(newest)
    # ⚠ A stated id that is not shaped like one is NOT an id. Returning it
    # would put a transcript's contents into the shell `tmux new-session`
    # runs (see `session_id_problem`); returning "" means the supervisor
    # refuses to continue, which is this module's existing answer to "no id"
    # and the safe one — a fresh context started silently is the failure
    # this file exists to prevent.
    if stated and not session_id_problem(stated):
        return stated
    return ""


def belongs_to_project(root: Path, session_id: str, base: Path | None = None) -> bool:
    """Is `session_id` one of THIS project's conversations?

    ⚠ **Why a designation is checked against this (C8).** `supervise` read
    the designated id and passed it to `--resume` unexamined, so a file
    naming another project's session was launched as a continuation and
    announced as one. Measured through `rite start` with a stub engine: a
    designation holding a session of a different project reached the engine
    as `--resume <that id>`.

    Answered from the same place the designation came from: an id is
    designated only after `latest_session_id` read it out of a transcript in
    this project's directory, so it is this project's exactly when one of
    those transcripts states it. Read the same way, so the writer and this
    check cannot disagree about what a file says.

    ⚠ **Project, not Manager.** Every Manager in a project writes into the
    same directory, so which Manager a conversation belongs to is not in the
    transcripts and this cannot answer it. A designation naming a sibling
    Manager's session still passes — stated, not solved.
    """
    if session_id_problem(session_id):
        return False
    directory = project_transcript_dir(root, base)
    try:
        candidates = [p for p in directory.glob("*.jsonl") if p.is_file()]
    except OSError:
        return False
    return any(_stated_session_id(p) == session_id for p in candidates)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _mtime_ns(path: Path) -> int:
    """An exact mtime, for deciding a tie. `st_mtime` is a float, and two
    nanosecond stamps that differ can round to one float and read as a tie."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _latest_event(path: Path) -> datetime | None:
    """The latest `timestamp` any line of a transcript records, or None.

    ⚠ **THE LATEST, NOT THE LAST.** Measured across 213 Claude Code
    transcripts on the machine this was written on: every one carried an
    ISO-8601 UTC `timestamp` with milliseconds on most lines, and in 176 of
    them the timestamps go BACKWARDS somewhere in the file (a resumed session
    carries earlier history, for one). So the last line is not the file's
    newest moment; the maximum is. It is Claude Code's own record of when an
    event happened, written by the provider rather than by the filesystem, and
    it is the only ordering fact inside the file.
    """
    latest: datetime | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                raw = data.get("timestamp")
                if not isinstance(raw, str):
                    continue
                try:
                    when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when.tzinfo is None:
                    continue
                if latest is None or when > latest:
                    latest = when
    except OSError:
        return None
    return latest


def _break_tie(tied: list[Path]) -> str:
    """The session among files tied at the newest mtime, or "".

    Decided from the CONTENTS, as the id itself is: a file that states no
    usable session id is not a session and drops out. One left is the answer.
    Several are ordered by the latest moment each records (`_latest_event`).

    ⚠ **IF THEY CANNOT BE ORDERED, NOTHING IS RETURNED.** Two sessions whose
    latest events are the same millisecond, or one with no timestamp at all,
    have no defined order — and a guess passed to `--resume` looks identical
    from outside to a right answer, which is the failure this module exists
    to stop. "" makes the supervisor refuse to continue, and say so.
    """
    sessions = {}
    for path in tied:
        stated = _stated_session_id(path)
        if stated and not session_id_problem(stated):
            sessions[path] = stated
    if len(sessions) == 1:
        return next(iter(sessions.values()))
    if not sessions:
        return ""
    when = {path: _latest_event(path) for path in sessions}
    if any(moment is None for moment in when.values()):
        return ""
    latest = max(when.values())
    winners = [path for path in sessions if when[path] == latest]
    return sessions[winners[0]] if len(winners) == 1 else ""


def _stated_session_id(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    found = str(data.get("sessionId", "") or "")
                    if found:
                        return found
                break
    except OSError:
        return ""
    return ""


_DENIAL = re.compile(
    r"requires approval|haven't granted it yet|permission to use|"
    r"permission for this tool use was denied|"
    r"permission denied by|not allowed to (?:run|use)",
    re.I,
)
_NAMED_PART = re.compile(r"following part requires approval:\s*(.+)", re.I | re.S)


def refused_commands(
    root: Path, since: float = 0.0, base: Path | None = None
) -> list[str]:
    """Commands this project's recent sessions were REFUSED, most recent last.

    ⚠ **Without this, a refusal is invisible to rite.** The engine tells the
    MODEL that a command needed approval; the model may then say so, work
    around it, or quietly do neither — and the v0.5.1 acceptance run is all
    three, across three cycles that each still exited 0. Reading the denial
    out of the transcript is how rite learns what its own allowlist missed
    instead of waiting for somebody to notice a Manager achieved nothing.

    ⚠ **Claude Code specific, like everything else in this module.** The
    three recorded shapes, measured from transcripts on this machine:

        This command requires approval
        This Bash command contains multiple operations. The following part
        requires approval: which rite 2>&1; rite --help 2>&1
        Claude requested permissions to write to <path>, but you haven't
        granted it yet.

    The second names the offending part, so it is preferred over the whole
    command when present — otherwise the refusal rite prints would quote a
    compound line and name the wrong executable.
    """
    directory = project_transcript_dir(root, base)
    try:
        candidates = [p for p in directory.glob("*.jsonl") if p.is_file()]
    except OSError:
        return []
    if since > 0:
        candidates = [p for p in candidates if _mtime(p) >= since]
    refused: list[str] = []
    for path in sorted(candidates, key=_mtime):
        refused.extend(_refused_in(path))
    return refused


def _refused_in(path: Path) -> list[str]:
    commands: dict[str, str] = {}
    refused: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        if '"tool_use"' not in line and '"tool_result"' not in line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                # ⚠ **EVERY tool, not just Bash.** A list of only shell
                # patterns produced an agent that could run anything and
                # change nothing, and the denial that stopped it was an
                # `Edit` — which this function, keyed on Bash alone, could
                # not see. The refusal that actually bit was the one rite
                # was blind to.
                if block.get("name") == "Bash":
                    command = (block.get("input") or {}).get("command", "")
                else:
                    # The TOOL NAME is what a permission rule names, so it
                    # is what the refusal has to tell the user to add.
                    command = block.get("name", "")
                if command:
                    commands[block["id"]] = command
            elif block.get("type") == "tool_result" and block.get("is_error"):
                body = block.get("content")
                text = body if isinstance(body, str) else json.dumps(body)
                if not _DENIAL.search(text):
                    continue
                named = _NAMED_PART.search(text)
                # The named part wins; otherwise fall back to the command the
                # result belongs to; a result with neither is skipped rather
                # than reported as an empty refusal.
                found = (
                    named.group(1).strip()
                    if named
                    else commands.get(block.get("tool_use_id", ""), "")
                )
                if found:
                    refused.append(found)
    return refused
