"""Which board a Manager's conversation began under, and whether it still is.

🔴 **A bare `rite start` continues a conversation, and a conversation keeps
the instructions it began with.** A Manager started while a project had no
board was told so, and was told to help set one up. Once a board was
configured, the next bare `rite start` continued that same conversation: it
never received the instructions for working a board, and it declined work
routed to it — "This is the same routing instruction a third time … I'm not
re-routing a duplicate task. Status unchanged: still waiting on the user's
choice of ticket backend." Measured on the v0.6.0 Linux acceptance run. The
same shape follows from a board being removed, or pointed at another
repository or site.

So the board is recorded beside the conversation when the conversation
BEGINS — at the launch of a fresh cycle, not afterwards, because a session
started with no board may configure one before it ends — and carried
forward while the conversation continues. A bare `rite start` whose recorded
board differs from the one configured now is refused with the command that
fixes it, before anything is started.

⚠ **Told, never forced.** Starting fresh discards the conversation's
context, which a user must choose; `--keep-conversation` continues it
anyway and records the current board, so the question is asked once.
"""

from __future__ import annotations

import json
from pathlib import Path


def board_now(root: Path) -> dict | None:
    """The board this project is configured with, as a comparable record, or
    None when the config cannot be read (other checks report that)."""
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return None
    tb = parsed.ticket_backend
    if tb.type == "none":
        return {"type": "none"}
    record: dict = {"type": tb.type}
    if tb.site:
        record["site"] = tb.site
    if tb.repo:
        record["repo"] = tb.repo
    if tb.projects:
        record["projects"] = {str(k): str(v) for k, v in sorted(tb.projects.items())}
    return record


def _describe(board: dict) -> str:
    """A board as a person would name it."""
    kind = board.get("type", "none")
    if kind == "none":
        return "not configured"
    if kind == "github":
        return f"the GitHub repository {board.get('repo') or '(no repo)'}"
    if kind == "jira":
        keys = ", ".join(sorted(set((board.get("projects") or {}).values())))
        return f"Jira at {board.get('site') or '(no site)'}" + (
            f" ({keys})" if keys else ""
        )
    return f"a {kind} board"


def _began_under(root: Path, manager: str) -> tuple[bool, dict | None]:
    """(recorded, board) for the designated conversation. `recorded` is
    False for a designation written before rite noted the board."""
    from rite_ai.managers import designation_path

    try:
        raw = json.loads(designation_path(root, manager).read_text())
    except (OSError, ValueError):
        return False, None
    board = raw.get("board") if isinstance(raw, dict) else None
    return isinstance(board, dict), board if isinstance(board, dict) else None


def refusal(root: Path, manager: str, *, sessions: int, minutes: float) -> str:
    """Why a bare `rite start` must not continue this Manager's conversation,
    with the commands to choose between — or "" when it may."""
    from rite_ai.managers import designated

    if not designated(root, manager):
        return ""
    now = board_now(root)
    if now is None:
        return ""
    recorded, then = _began_under(root, manager)
    if recorded and then == now:
        return ""
    if not recorded and now.get("type") == "none":
        # Nothing can have been configured since that the conversation
        # missed; a board REMOVED since is the one case this cannot see.
        return ""
    bounds = f"--sessions {sessions} --minutes {minutes:g}"
    fresh = f"rite start {manager} --fresh {bounds}"
    keep = f"rite start {manager} --keep-conversation {bounds}"
    if recorded:
        head = (
            f"refusing to continue Manager {manager!r}'s conversation: it "
            f"began when this project's board was {_describe(then or {})}, "
            f"and the board is now {_describe(now)}."
        )
        choose = (
            f"  To start a new conversation that knows the current board:\n"
            f"    {fresh}\n"
            f"  To continue the old one anyway:\n"
            f"    {keep}"
        )
    else:
        head = (
            f"refusing to continue Manager {manager!r}'s conversation: this "
            f"project's board is now {_describe(now)}, and rite cannot tell "
            f"which board that conversation began with. It was started by an "
            f"earlier build of rite, which did not record it."
        )
        choose = (
            f"  If the board was configured or changed since that "
            f"conversation began, start a new one:\n"
            f"    {fresh}\n"
            f"  If it has not changed, continue, and rite will record the "
            f"board from now on:\n"
            f"    {keep}"
        )
    return (
        f"{head}\n"
        "  A conversation keeps the instructions it began with, and "
        "continuing it does not replace them, so the Manager would go on "
        "working from the old board. Measured: a Manager that began with no "
        "board, continued after one was configured, declined the work routed "
        "to it as a duplicate of something it believed it had already done.\n"
        "  Nothing was started.\n"
        f"{choose}"
    )


def record_board(root: Path, manager: str, board: dict) -> None:
    """Note `board` against the designated conversation, keeping its id.
    Used when the user chose to continue it under the current board."""
    from rite_ai.managers import designate, designated

    session = designated(root, manager)
    if session:
        designate(root, manager, session, board=board)
