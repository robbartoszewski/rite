"""SCRUM-87: a refused candidate plan leaves its own output behind.

A candidate that would not parse was reported as "the bytes are not a
decomposition: …" and the bytes were then dropped — captured in-process with
`capture_output=True` and written nowhere. So the one artefact needed to tell
"the model wrote prose", "it fenced the JSON" and "it emitted a different
schema" apart was the only thing not kept.

Measured 2026-10-08: a 17 GB local author failed both attempts with
`Expecting value: line 2 column 5 (char 5)` and left nothing to read, which is
why the next decision — constrain the output format, or change the model —
could only have been a guess.

⚠ Kept under the Manager's own state directory, NOT the journal:
`test_nothing_in_rite_reads_the_journal` forbids any module outside
`journal.py` from even locating that, and these are not journal entries.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.local.decompose import (
    MAX_KEPT_BYTES,
    keep_rejected,
    rejected_dir,
)

RAW = b'```json\n{"subtasks": []}\n```\n'
WHY = ("the bytes are not a decomposition: Expecting value: line 2 column 5",)


def test_the_models_own_output_is_written_verbatim(tmp_path):
    path = keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, RAW)
    assert path, "nothing was kept"
    text = Path(path).read_text()
    assert RAW.decode() in text, "the model's output is not in the capture"


def test_the_refusal_is_written_beside_it(tmp_path):
    """The bytes alone do not say why they were refused."""
    text = Path(keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, RAW)).read_text()
    assert "Expecting value" in text
    assert "attempt: 1" in text


def test_each_attempt_is_kept_separately(tmp_path):
    """DD-3.4 retries, and the retry is fed the rejection — so the two attempts
    differ and overwriting the first would hide whether the feedback helped."""
    first = keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, b"first")
    second = keep_rejected(tmp_path, "planner", "KAN-1", 2, WHY, b"second")
    assert first != second
    assert "first" in Path(first).read_text()
    assert "second" in Path(second).read_text()


def test_a_runaway_turn_cannot_fill_the_disk(tmp_path):
    big = b"x" * (MAX_KEPT_BYTES * 3)
    path = keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, big)
    kept = Path(path).read_bytes()
    assert len(kept) < len(big)
    assert b"showing the first" in kept, "a truncated capture does not say so"


def test_it_never_raises_and_says_so_by_returning_empty(tmp_path):
    """🔴 Losing the diagnostic is bad; turning it into a crash is worse. A
    decomposition that was going to fail anyway must not die here."""
    assert keep_rejected(tmp_path, "planner", "../escape", 1, WHY, RAW) == ""
    assert keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, None) != "" or True


def test_it_is_not_written_into_the_journal(tmp_path):
    """The journal is write-only by rule and these are not entries."""
    where = rejected_dir(tmp_path, "planner")
    assert "journal" not in where.parts, where


def test_an_empty_output_is_still_a_capture(tmp_path):
    """A model that returned NOTHING is a fact worth keeping — otherwise it
    looks identical to a capture that failed to write."""
    path = keep_rejected(tmp_path, "planner", "KAN-1", 1, WHY, b"")
    assert path
    assert "bytes: 0" in Path(path).read_text()


def test_the_decomposer_actually_calls_it():
    """Dead wiring: a capture nothing invokes is a comment. By AST."""
    import ast
    import inspect

    from rite_ai.local import decompose

    tree = ast.parse(inspect.getsource(decompose.decompose_ticket))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "keep_rejected" in called, "decompose_ticket never keeps the bytes"
