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
from pathlib import Path

from rite_ai.budget import default_transcripts_dir


def project_transcript_dir(root: Path, base: Path | None = None) -> Path:
    """Claude Code's directory for a project root.

    The name is the absolute path with `/` replaced by `-`, which is
    observable rather than documented — derived by reading this machine's
    own layout, and the reason this function exists instead of the rule
    being inlined somewhere it would be mistaken for a spec.
    """
    where = base if base is not None else default_transcripts_dir()
    resolved = str(Path(root).resolve())
    return where / resolved.replace("/", "-")


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
    if stated:
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
