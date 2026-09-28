"""Cursor's spelling (CU2), as measured in CU1 and CU1b.

What this pins: the turn and its continuation rite builds for Cursor, that
the handle must be a canonical UUID, and that each part not yet built
REFUSES rather than launching something that half-works: the permission
(CU8), the recorded handle (CU3) and the config gate (CU4).

Claude's and Goose's command lines are pinned separately, all 96
combinations, in `test_launch_command_is_pinned.py`; adding Cursor changed
none of them.
"""

from __future__ import annotations

import uuid

import pytest

from rite_ai.config.managers import parse_managers
from rite_ai.managers.engines import (
    CURSOR,
    handle_problem,
    new_handle,
    spelling_for,
)
from rite_ai.managers.supervise import _resume_id_source, launch_command

HANDLE = "0b6f0c1e-1234-4abc-8def-0123456789ab"


def test_the_engine_named_cursor_speaks_cursor():
    assert spelling_for("cursor") is CURSOR


def test_a_first_turn_names_its_chat_and_reads_the_prompt_from_stdin():
    built = launch_command("cursor", "", "/p", start_handle=HANDLE)
    assert built == (
        "agent -p --trust --disable-project-configs --output-format json "
        f"--resume {HANDLE} < /p"
    )


def test_a_continuation_is_spelled_exactly_like_a_first_turn():
    """⚠ Cursor's shape: it cannot say which one happened. That is why CU3
    checks the chat's `createdAtMs` after every continuation instead of
    trusting the exit."""
    first = launch_command("cursor", "", "/p", start_handle=HANDLE)
    again = launch_command("cursor", HANDLE, "/p")
    assert first == again


@pytest.mark.parametrize(
    "handle",
    [
        "rite-lead-3f2a",  # what a name-derived handle looks like
        HANDLE.upper(),  # a UUID, not in the form Cursor stores
        "0b6f0c1e12344abc8def0123456789ab",  # likewise, without dashes
    ],
)
def test_a_handle_that_is_not_a_canonical_uuid_is_refused(handle):
    with pytest.raises(ValueError, match="refusing to start"):
        launch_command("cursor", "", "/p", start_handle=handle)
    with pytest.raises(ValueError, match="refusing to resume"):
        launch_command("cursor", handle, "/p")


def test_a_hostile_handle_is_refused_before_the_uuid_rule_is_asked():
    with pytest.raises(ValueError, match="run by a shell"):
        launch_command("cursor", "abc$(touch PWNED)", "/p")


def test_a_permission_is_refused_not_written_as_a_claude_flag():
    """CU8: Cursor reads its allowlist from a config file the supervisor
    writes. A permission flag would be ignored, so it is refused."""
    with pytest.raises(ValueError, match="cli-config.json"):
        launch_command("cursor", "", "/p", "--settings /s.json", start_handle=HANDLE)


def test_the_supervisor_continues_cursor_by_its_recorded_uuid(tmp_path):
    """Goose's handle is the session's name. Cursor's cannot be, or `--fresh`
    would continue the old chat: it is the UUID recorded before launch."""
    from rite_ai.managers import record_chat

    (tmp_path / ".rite").mkdir()
    source = _resume_id_source("cursor")
    assert source(tmp_path, "lead") == ""
    record_chat(tmp_path, "lead", HANDLE)
    assert source(tmp_path, "lead") == HANDLE


def test_a_new_handle_is_a_fresh_canonical_uuid():
    a, b = new_handle(CURSOR), new_handle(CURSOR)
    assert a != b and handle_problem(CURSOR, a) == ""


def test_a_canonical_uuid_passes_the_handle_rule():
    handle = str(uuid.uuid4())
    assert handle_problem(CURSOR, handle) == ""


def test_cursor_is_not_a_local_tier():
    with pytest.raises(ValueError, match="needs an agent rite can launch"):
        spelling_for("local:small", "cursor")


def test_a_project_cannot_configure_a_cursor_manager_yet():
    """Production cannot reach the spelling until a Cursor Manager can
    actually run: its key's route and its grants are CU4's."""
    parsed = parse_managers([{"name": "lead", "engine": "cursor"}])
    assert "is not one rite knows" in parsed.error
