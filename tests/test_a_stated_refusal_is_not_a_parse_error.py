"""SCRUM-98 — when the decomposer DECLINES, rite says what it said.

🔴 **Measured on the v0.7.0 gate run, from the bytes SCRUM-87 captured.** The
project registered no spec units, so RL-63 made every possible plan invalid —
and the local author said exactly that, in valid JSON:

    {"format_version": 1, "ticket": "2", "decomposed": false,
     "reason": "This project registers no spec units, so no cite can resolve:
                every subtask rule requires citing at least one existing spec
                unit, and the ticket explicitly forbids inventing a cite."}

`plan_document` filters candidates on `"subtasks"`, which a refusal has none
of, so it returned the raw transcript and `parse` reported `Expecting value:
line 2 column 5 (char 5)` — the offset of goose's ASCII banner. The one
actionable sentence was replaced by a message about the wrong thing; the
operator spent 25 minutes finding it in the capture; and DD-3.3 fed the parse
error back, so the retry was told nothing it could act on.

Two halves, and the second is the one that cost the time:

* a stated refusal is reported as the refusal;
* and the decomposer's own `lines` — "the refused candidate was kept at
  <path>" (SCRUM-87), the RL-69 convergence note — reach the log, which the
  loop dropped for this branch exactly as it dropped the step branch's
  (SCRUM-96 defect 3).

⚠ The prose case is deliberately NOT guessed at. The gate run's first attempt
refused in English with no JSON anywhere, and inventing heuristics for free
text would trade a precise message for a plausible one. What it gets instead
is the path to its own bytes.
"""

from __future__ import annotations

import json

from rite_ai.local import decomposition as dec
from rite_ai.local.decompose import plan_document, stated_refusal

BANNER = "__( O)>  ● new session · ollama qwen3.8:latest\n   \\____)  goose is ready\n"

REFUSAL = {
    "format_version": 1,
    "ticket": "2",
    "decomposed": False,
    "decomposed_by": "",
    "reason": (
        "This project registers no spec units, so no cite can resolve: every "
        "subtask rule requires citing at least one existing spec unit, and the "
        "ticket explicitly forbids inventing a cite."
    ),
}

PLAN = {
    "format_version": 1,
    "ticket": "2",
    "decomposed_by": "planner",
    "subtasks": [
        {
            "id": "s1",
            "intent": "do it",
            "scope": ["a.py"],
            "verify": "uv run pytest -q",
            "cites": ["u1"],
        },
        {
            "id": "s2",
            "intent": "check it",
            "scope": ["b.py"],
            "verify": "uv run pytest -q",
            "cites": ["u1"],
        },
    ],
}


def test_a_structured_refusal_is_read_out_of_the_transcript():
    raw = (BANNER + "some tool output\n" + json.dumps(REFUSAL)).encode()
    said = stated_refusal(raw)
    assert "registers no spec units" in said
    # 🔴 And the transcript is still not a plan: the refusal does not become one.
    assert isinstance(dec.parse(plan_document(raw)), str)


def test_a_transcript_carrying_a_real_plan_is_not_read_as_a_refusal():
    """CONTROL. Without it, returning a refusal for everything would pass."""
    raw = (BANNER + "```json\n" + json.dumps(PLAN) + "\n```\n").encode()
    assert stated_refusal(raw) == ""
    assert not isinstance(dec.parse(plan_document(raw)), str), "the plan still parses"


def test_other_json_in_a_transcript_is_not_mistaken_for_a_refusal():
    """A transcript legitimately carries other objects — an events.jsonl line
    that `cat` put there is the real example `plan_document` guards against."""
    event = {"at": 1.0, "event": "sandbox-started", "ticket": "1", "worker": "gpu1"}
    raw = (BANNER + json.dumps(event) + "\n").encode()
    assert stated_refusal(raw) == ""


def test_a_refusal_with_no_reason_says_nothing_rather_than_something_empty():
    raw = (BANNER + json.dumps({"ticket": "2", "decomposed": False})).encode()
    assert stated_refusal(raw) == ""


def test_the_last_refusal_wins_when_an_agent_revised_itself():
    first = dict(REFUSAL, reason="an earlier thought")
    raw = (BANNER + json.dumps(first) + "\nthen:\n" + json.dumps(REFUSAL)).encode()
    assert "registers no spec units" in stated_refusal(raw)


def test_the_loop_reports_the_decomposers_own_lines():
    """🔴 The half that cost the 25 minutes: `lines` carries the path SCRUM-87
    wrote the bytes to, and this branch dropped it."""
    import tempfile
    from pathlib import Path

    from rite_ai.local import loop, plan_state
    from rite_ai.local import stage as st

    root = Path(tempfile.mkdtemp())
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    state = plan_state.layer(root)

    class _Result:
        wrote = False
        problem = ""
        reasons = ("the bytes are not a decomposition: nope",)
        lines = ["the refused candidate was kept at /tmp/rejected/2-attempt1.txt"]
        attempts = 1
        escalated = False

    # ⚠ The decompose branch is only reached FROM `defined`, and a ticket only
    # enters `defined` when a Worker is recorded on it. Seeded directly, the
    # way `test_a_stage_cannot_be_skipped` does, so this test is about the
    # reporting and not about the stage table.
    got = state.read_state(st.key_for("T-9"))
    state.write_state(
        st.key_for("T-9"),
        st.render(
            st.Record(
                ticket="T-9",
                stage=st.DEFINED,
                log=(st.Transition(frm=st.UNSTARTED, to=st.DEFINED, at=1.0, why="x"),),
            )
        ),
        got.version,
    )
    advance = loop.advance_ticket(
        root, "planner", "T-9", state=state, author_plan=lambda *a, **k: _Result()
    )
    line = advance.line()
    assert "not decomposed" in line
    assert "2-attempt1.txt" in line, line


def test_the_log_names_the_AUTHOR_not_the_driver():
    """🔴 Seen in the v0.7.0 gate run's own log: "decomposed by lead" on the
    line above "plan review asked of lead", while the record said `planner`.
    A person auditing RL-6 from the log would read a Manager reviewing its own
    plan — the one thing RL-6 forbids."""
    import tempfile
    from pathlib import Path as _P

    from rite_ai.local import loop, plan_state
    from rite_ai.local import stage as st

    def _plan(ticket: str, author: str):
        return dec.Decomposition(
            ticket=ticket,
            subtasks=(
                dec.Subtask(
                    id="s1",
                    intent="do it",
                    scope=("a.py",),
                    verify="uv run pytest -q",
                    cites=("u1",),
                ),
                dec.Subtask(
                    id="s2",
                    intent="check it",
                    scope=("b.py",),
                    verify="uv run pytest -q",
                    cites=("u1",),
                ),
            ),
            decomposed_by=author,
        )

    class _Wrote:
        """Writes a REAL decomposition, because the stage gate refuses a stage
        its own artifact does not back — so a stub that only claims `wrote`
        never reaches the line under test."""

        wrote = True
        problem = ""
        reasons = ()
        lines: list = []
        attempts = 1
        escalated = False
        author = "planner"

        def __init__(self, state, ticket, author):
            dec.write(state, _plan(ticket, author), dec.read(state, ticket).version)
            self.author = author

    root = _P(tempfile.mkdtemp())
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    state = plan_state.layer(root)
    got = state.read_state(st.key_for("T-7"))
    state.write_state(
        st.key_for("T-7"),
        st.render(
            st.Record(
                ticket="T-7",
                stage=st.DEFINED,
                log=(st.Transition(frm=st.UNSTARTED, to=st.DEFINED, at=1.0, why="x"),),
            )
        ),
        got.version,
    )
    line = loop.advance_ticket(
        root,
        "lead",
        "T-7",
        state=state,
        author_plan=lambda *a, **k: _Wrote(state, "T-7", "planner"),
    ).line()
    assert "planner" in line, line
    assert "driven by lead" in line, line

    # CONTROL: no delegation, so no parenthetical to confuse a reader.
    root2 = _P(tempfile.mkdtemp())
    (root2 / ".rite").mkdir(parents=True, exist_ok=True)
    state2 = plan_state.layer(root2)
    got2 = state2.read_state(st.key_for("T-7"))
    state2.write_state(
        st.key_for("T-7"),
        st.render(
            st.Record(
                ticket="T-7",
                stage=st.DEFINED,
                log=(st.Transition(frm=st.UNSTARTED, to=st.DEFINED, at=1.0, why="x"),),
            )
        ),
        got2.version,
    )
    line2 = loop.advance_ticket(
        root2,
        "lead",
        "T-7",
        state=state2,
        author_plan=lambda *a, **k: _Wrote(state2, "T-7", "lead"),
    ).line()
    assert "by lead" in line2 and "driven by" not in line2, line2
