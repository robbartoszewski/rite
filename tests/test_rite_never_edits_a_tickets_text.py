"""G2: rite never edits a ticket's title or description (the refinement note's
part 3.13).

The User's words are the ticket's text, and rite's agreed definitions of done
are comments beside it, never edits to it: `update()` replaces a body
wholesale with no compare-and-swap on either backend, and an agent overwriting
the User's text is exactly the guardrail Robert asked for (TRQ6). So nothing
outside `tickets/`, where the backends implement `update()` and the DF4
wrapper forwards it, may call a board's `update()` with the text fields, or
forward arbitrary fields to it.

Checked by the syntax tree, so a comment or a string that merely mentions
`update(` does not count. **What it cannot see:** a call built by `getattr`,
or `gh`/Jira reached directly by a model in a person's own unsandboxed
session (TRQ10's class). If a restore-on-the-User's-word path is ever built,
it is the one caller to add below, with its reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "rite_ai"
TEXT_FIELDS = {"title", "description", "body"}
ALLOWED: dict[str, str] = {}
"""Callers allowed to edit a ticket's text, each with its reason. None today."""


def _offending_calls(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            continue
        named = {k.arg for k in node.keywords if k.arg is not None}
        forwards = any(k.arg is None for k in node.keywords) and bool(node.args)
        if named & TEXT_FIELDS or forwards:
            lines.append(node.lineno)
    return lines


def test_nothing_outside_the_backends_edits_a_tickets_text():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tickets/") or rel in ALLOWED:
            continue
        for line in _offending_calls(ast.parse(path.read_text(), filename=rel)):
            offenders.append(f"{rel}:{line}")
    assert not offenders, (
        f"these call a board's update() with a ticket's text: {offenders}. rite "
        "never edits the User's words; an agreed definition of done is a comment "
        "(refinement/record.py). If this is the restore-on-the-User's-word path, "
        "add it to ALLOWED with its reason."
    )


def test_the_check_would_see_an_edit():
    """A check whose match found nothing would pass everything."""
    planted = ast.parse(
        "board.update('KAN-7', description='agent text')\n"
        "board.update('KAN-7', **fields)\n"
        "settings.update({'a': 1})\n"
        "cache.update(title_count=3)\n"
    )
    assert _offending_calls(planted) == [1, 2]
