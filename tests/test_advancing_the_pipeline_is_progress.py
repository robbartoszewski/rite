"""Advancing the staged pipeline is a Manager's progress (SCRUM-72).

🔴 **Without this the pipeline starves itself, silently.** The local tier runs
once per CYCLE, and `_session_was_idle` judged a cycle by the footprint's
COORDINATION parts — claims, routes, Worker requests, deliveries, lifecycle.
Advancing a stage was none of them, so a cycle whose only accomplishment was
the pipeline counted as no progress, the no-progress guard parked the
supervisor, and the parked supervisor never opened the cycle the pipeline
needed.

Measured 2026-10-09 on the v0.7.0 gate fleet: the cycle that accepted subtask
s1 was itself judged idle, `lead` parked with "session 8 made no progress", and
the ticket stopped at `stepping` with s2 planned and nothing left that could
ever move it. Not a hang anyone could see — every component was healthy.

It is the failure `progress.py`'s own docstring warns about: "a false 'it did
nothing' is a stalled Manager."
"""

from __future__ import annotations

from rite_ai.managers.progress import Footprint, _pipeline_state, footprint
from rite_ai.managers.supervise import COORDINATION, _session_was_idle


def test_the_pipeline_counts_as_coordination():
    assert "pipeline" in COORDINATION


def test_a_cycle_that_only_advanced_a_stage_is_NOT_idle():
    """🔴 The defect, in one line: this returned True and parked the fleet."""
    assert not _session_was_idle(["pipeline"])


def test_a_cycle_that_only_replied_is_still_idle():
    """The control. Robert's 2026-10-02 rule is untouched — a reply is not
    progress — so this must not have quietly widened."""
    assert _session_was_idle(["outbox"])
    assert _session_was_idle(["project"])


def test_a_stage_advance_moves_the_footprint(tmp_path, monkeypatch):
    """End to end over the real plan-state home: a stage written changes the
    footprint, and the changed part is named `pipeline`."""
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    root = (tmp_path / "proj").resolve()

    from rite_ai.local import plan_state, stage

    before = footprint(root, "lead")
    state = plan_state.layer(root)
    moved = stage.advance(
        state, "T-1", stage.DEFINED, why="defined", definition="rec-1"
    )
    assert type(moved).__name__ == "Advanced", moved
    after = footprint(root, "lead")

    assert "pipeline" in after.differs_from(before), after.differs_from(before)
    assert not _session_was_idle(after.differs_from(before))


def test_an_unchanged_pipeline_is_not_a_difference(tmp_path, monkeypatch):
    """🔴 The control that stops this making EVERY session look productive —
    which is the opposite failure and costs a session each cycle."""
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    root = (tmp_path / "proj").resolve()

    from rite_ai.local import plan_state, stage

    stage.advance(
        plan_state.layer(root), "T-1", stage.DEFINED, why="defined", definition="rec-1"
    )
    first = footprint(root, "lead")
    second = footprint(root, "lead")
    assert "pipeline" not in second.differs_from(first)


def test_no_plan_state_is_empty_rather_than_a_change(tmp_path):
    """A project with no pipeline must read "" — not something that differs
    from itself on every cycle."""
    assert _pipeline_state(tmp_path) == ""


def test_two_tickets_at_different_stages_hash_differently(tmp_path, monkeypatch):
    """The hash must cover WHICH ticket moved, not merely how many records
    exist — otherwise one ticket advancing while another retreats reads as no
    change."""
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    root = (tmp_path / "proj").resolve()

    from rite_ai.local import plan_state, stage

    state = plan_state.layer(root)
    stage.advance(state, "T-1", stage.DEFINED, why="defined", definition="rec-1")
    one = _pipeline_state(root)
    stage.advance(state, "T-2", stage.DEFINED, why="defined", definition="rec-2")
    assert _pipeline_state(root) != one


def test_the_footprint_field_exists_on_the_dataclass():
    assert "pipeline" in Footprint().__dataclass_fields__


def test_every_footprint_part_is_actually_compared():
    """🔴 `differs_from` held a hardcoded list of parts, so a new field could be
    computed, stored and counted as coordination while never being compared —
    which is exactly what happened to `pipeline`. A fix that does nothing is
    worse than no fix, because the test that should catch it is passing.
    """
    a = Footprint()
    for name in Footprint().__dataclass_fields__:
        other = Footprint(**{name: "different-from-the-default"})
        assert name in a.differs_from(other), (
            f"{name} is part of the footprint and is never compared"
        )
