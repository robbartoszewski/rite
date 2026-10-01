"""CU3's core: whether a Cursor turn continued the chat rite meant.

Cursor creates a chat for an unknown UUID and reports success, so every
path here is one where trusting the exit would have been wrong. The chat
layout is the one measured in CU1b:
`<CURSOR_CONFIG_DIR>/chats/<hash>/<uuid>/meta.json` with `createdAtMs`.
"""

from __future__ import annotations

import json

from rite_ai.managers import cursor_chat as cc

HANDLE = "0b6f0c1e-1234-4abc-8def-0123456789ab"


def _chat(config, created, workspace_hash="983511930fa1d78174dce7f9f449bce8"):
    d = config / "chats" / workspace_hash / HANDLE
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps({"schemaVersion": 1, "createdAtMs": created, "cwd": "/w"})
    )
    return d


class TestObserve:
    def test_no_chat_is_absence_with_the_pattern_named(self, tmp_path):
        seen = cc.observe(tmp_path, HANDLE)
        assert seen.created_at_ms is None and not seen.problem
        assert HANDLE in seen.where

    def test_a_chat_is_found_under_any_workspace_hash(self, tmp_path):
        _chat(tmp_path, 1790539800070, workspace_hash="anything")
        assert cc.observe(tmp_path, HANDLE).created_at_ms == 1790539800070

    def test_two_chats_with_one_id_are_refused_not_guessed_between(self, tmp_path):
        _chat(tmp_path, 1, workspace_hash="a")
        _chat(tmp_path, 2, workspace_hash="b")
        seen = cc.observe(tmp_path, HANDLE)
        assert seen.problem and "refusing to guess" in seen.problem

    def test_an_unreadable_record_is_a_problem_not_an_absence(self, tmp_path):
        """⚠ Absence would read as "first turn" and relaunch."""
        d = _chat(tmp_path, 1)
        (d / "meta.json").write_text("{not json")
        assert cc.observe(tmp_path, HANDLE).problem

    def test_a_missing_meta_file_is_a_problem_not_an_absence(self, tmp_path):
        d = _chat(tmp_path, 1)
        (d / "meta.json").unlink()
        assert cc.observe(tmp_path, HANDLE).problem

    def test_a_creation_time_that_is_not_an_integer_is_a_problem(self, tmp_path):
        d = _chat(tmp_path, 1)
        (d / "meta.json").write_text(json.dumps({"createdAtMs": True}))
        assert cc.observe(tmp_path, HANDLE).problem


class TestBeforeATurn:
    def test_never_confirmed_and_no_chat_is_a_first_turn(self, tmp_path):
        v = cc.before_turn(None, cc.observe(tmp_path, HANDLE))
        assert v.outcome == cc.FIRST_TURN

    def test_a_chat_rite_did_not_get_to_record_is_adopted(self, tmp_path):
        """The first turn ran and rite stopped before recording it. The UUID
        is rite's, so the chat is this handle's."""
        _chat(tmp_path, 5)
        v = cc.before_turn(None, cc.observe(tmp_path, HANDLE))
        assert (v.outcome, v.created_at_ms) == (cc.CONTINUE, 5)

    def test_the_recorded_chat_as_recorded_continues(self, tmp_path):
        _chat(tmp_path, 5)
        assert cc.before_turn(5, cc.observe(tmp_path, HANDLE)).outcome == cc.CONTINUE

    def test_a_removed_chat_is_refused_with_the_path_named(self, tmp_path):
        """CU3's acceptance: refused, not run as a fresh chat."""
        v = cc.before_turn(5, cc.observe(tmp_path, HANDLE))
        assert v.outcome == cc.REFUSE
        assert HANDLE in v.reason and "--fresh" in v.reason

    def test_a_different_chat_under_the_same_id_is_refused(self, tmp_path):
        _chat(tmp_path, 6)
        v = cc.before_turn(5, cc.observe(tmp_path, HANDLE))
        assert v.outcome == cc.REFUSE and "not the conversation" in v.reason

    def test_an_unclear_disk_is_refused(self, tmp_path):
        _chat(tmp_path, 1, workspace_hash="a")
        _chat(tmp_path, 1, workspace_hash="b")
        assert cc.before_turn(1, cc.observe(tmp_path, HANDLE)).outcome == cc.REFUSE


class TestAfterATurn:
    def test_a_first_turn_that_made_a_chat_is_confirmed_with_its_time(self, tmp_path):
        _chat(tmp_path, 7)
        v = cc.after_turn(None, cc.observe(tmp_path, HANDLE))
        assert (v.outcome, v.created_at_ms) == (cc.CONFIRMED, 7)

    def test_a_first_turn_that_made_no_chat_breaks_nothing(self, tmp_path):
        v = cc.after_turn(None, cc.observe(tmp_path, HANDLE))
        assert v.outcome == cc.NO_CHAT
        # ...and the next launch is a first turn again, same UUID.
        again = cc.before_turn(None, cc.observe(tmp_path, HANDLE))
        assert again.outcome == cc.FIRST_TURN

    def test_the_same_chat_continued(self, tmp_path):
        _chat(tmp_path, 7)
        assert cc.after_turn(7, cc.observe(tmp_path, HANDLE)).outcome == cc.CONTINUED

    def test_a_chat_replaced_during_the_turn_fails_closed(self, tmp_path):
        """⚠ THE WINDOW rite cannot close: the chat went between the check
        before and Cursor opening it, and Cursor recreated it empty. The
        check after is what stops that passing as a continuation."""
        _chat(tmp_path, 7)
        before = cc.before_turn(7, cc.observe(tmp_path, HANDLE))
        assert before.outcome == cc.CONTINUE
        # The window: removed, and recreated by Cursor with a new time.
        import shutil

        shutil.rmtree(tmp_path / "chats")
        _chat(tmp_path, 9)
        after = cc.after_turn(before.created_at_ms, cc.observe(tmp_path, HANDLE))
        assert after.outcome == cc.REPLACED
        assert "WITHOUT its history" in after.reason

    def test_a_chat_gone_after_a_continuation_fails_closed(self, tmp_path):
        v = cc.after_turn(7, cc.observe(tmp_path, HANDLE))
        assert v.outcome == cc.REPLACED
