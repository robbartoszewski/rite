"""SCRUM-88: the decomposition lives inside the agent's output, not as it.

`_propose` returns the WHOLE of the agent's stdout, and for goose that is an
interactive transcript — an ASCII-art banner, a `▸ tree` / `▸ shell` trace per
tool call, and every command's output. `parse` was handed all of it and said:

    Expecting value: line 2 column 5 (char 5)

which is exactly where `__( O)>` sits on line 2 of goose's banner.

🔴 **It was never a model-capability problem.** Measured 2026-10-08: a 17 GB
local author produced a completely valid two-subtask plan — real verifies it
had itself checked fail today, `decomposed_by` left for rite to fill, no
approval set because a reviewer does that — inside a ```json fence at the end
of 58 KB of transcript. rite threw it away and the ticket escalated. Two
earlier runs were abandoned on the belief that the model could not emit the
schema.
"""

from __future__ import annotations

from rite_ai.local import decomposition as dec
from rite_ai.local.decompose import plan_document

PLAN = (
    '{"format_version": 1, "ticket": "1", "decomposed_by": "", "subtasks": ['
    '{"id": "s1", "intent": "add slugify", "scope": ["src/tally/__init__.py"], '
    '"verify": "python -m pytest -q tests/test_slug.py", "cites": ["abc123"]},'
    '{"id": "s2", "intent": "make the suite pass", "scope": ["tests/test_slug.py"], '
    '"verify": "python -m pytest -q tests/test_slug.py", "cites": ["abc123"]}]}'
)

BANNER = (
    "\n    __( O)>  ● new session · ollama qwen3.8:latest\n"
    "   \\____)    20261008_26 · /tmp/app\n     L L     goose is ready\n\n"
    "  ────────────────────────────────────────\n  ▸ tree\n    path: /tmp/app\n"
)

# A transcript legitimately carries other JSON: these are `.rite/events.jsonl`
# lines that `cat`-ing a file put into the output.
EVENTS = (
    '{"at": 1791474385.6, "event": "sandbox-started", "ticket": "1",'
    ' "worker": "gpu1"}\n'
    '{"at": 1791474386.2, "by": "", "event": "board-move", "ticket": "1"}\n'
)


def _parses(raw: bytes):
    return dec.parse(plan_document(raw))


def test_a_fenced_plan_at_the_end_of_a_transcript_is_found():
    raw = (
        BANNER
        + EVENTS
        + "Emitting the single JSON object:\n\n```json\n"
        + PLAN
        + "\n```\n"
    ).encode()
    got = _parses(raw)
    assert not isinstance(got, str), got
    assert [s.id for s in got.subtasks] == ["s1", "s2"]


def test_the_transcripts_own_json_is_not_mistaken_for_a_plan():
    """🔴 The control that matters most. "The last balanced object" alone would
    pick an events.jsonl line, which parses perfectly and is not a plan."""
    raw = ("```json\n" + PLAN + "\n```\n" + EVENTS).encode()
    got = _parses(raw)
    assert not isinstance(got, str), "an event line was taken for the plan"
    assert got.ticket == "1"
    assert len(got.subtasks) == 2


def test_an_unfenced_plan_is_found_too():
    """An agent that fences nothing must still be read."""
    raw = (BANNER + "here it is:\n" + PLAN + "\n").encode()
    assert not isinstance(_parses(raw), str)


def test_the_last_plan_wins_when_the_agent_revised_itself():
    first = PLAN.replace('"s1"', '"old1"').replace('"s2"', '"old2"')
    raw = (
        "```json\n" + first + "\n```\nOn reflection:\n```json\n" + PLAN + "\n```\n"
    ).encode()
    got = _parses(raw)
    assert [s.id for s in got.subtasks] == ["s1", "s2"], "an earlier draft was taken"


def test_prose_with_no_plan_returns_the_bytes_UNCHANGED():
    """🔴 So the error still describes the real output.

    Returning a tidier slice would make `parse`'s message a lie about what the
    model actually produced — and that message is the only thing a person has
    when a decomposition fails.
    """
    raw = b"I could not work out how to do this. Sorry.\n"
    assert plan_document(raw) == raw
    assert isinstance(dec.parse(plan_document(raw)), str), "a non-plan parsed"


def test_empty_output_is_returned_unchanged():
    assert plan_document(b"") == b""


def test_a_fence_holding_something_that_is_not_a_plan_is_skipped():
    raw = (
        '```json\n{"hello": "world"}\n```\n' + "```json\n" + PLAN + "\n```\n"
    ).encode()
    got = _parses(raw)
    assert not isinstance(got, str)
    assert len(got.subtasks) == 2


def test_the_decomposer_actually_uses_it():
    """Dead wiring, by AST: an extractor nothing calls is a comment."""
    import ast
    import inspect

    from rite_ai.local import decompose

    tree = ast.parse(inspect.getsource(decompose.decompose_ticket))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "plan_document" in called, "decompose_ticket parses the raw stdout"
