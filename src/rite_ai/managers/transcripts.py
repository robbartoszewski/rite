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
    newest = max(candidates, key=_mtime)
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


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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
