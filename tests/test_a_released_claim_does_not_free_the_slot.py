"""SCRUM-70 — a Worker's SLOT outlives its claims, and nothing said so.

§1's must-fix row: *"`release --force` reports success but the slot stays
held."*

🔴 **Two things, freed by two different commands, reported as one.** A Worker
holds claims on paths (`rite release` clears them) and a slot against
`sandbox.max_concurrent_workers` — and only a `destroy` frees that, which
`count_active_sandboxes` states itself: *"a stopped-but-not destroyed sandbox
counts; only `destroy` removes one."*

So `rite release --force` printed "force-released 2 claim(s)" and the next
Worker still could not start. And `rite status` printed "**no active
claims**" — the most reassuring thing it can say — about a project whose every
slot was occupied. Both sentences were true about claims and were read as
sentences about capacity.
"""

from __future__ import annotations

import pytest

from rite_ai.reporting.held_slots import HeldSlot, held_slots, idle_only
from rite_ai.sandbox import SandboxListing, sandbox_name


def _project(tmp_path, workers=("alpha", "beta")):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    for w in workers:
        d = tmp_path / "workers" / w
        d.mkdir(parents=True, exist_ok=True)
        (d / "worker.yml").write_text(f"worker:\n  name: {w}\n  modules: []\n")
    return tmp_path


def _listed(root, **statuses):
    return SandboxListing({sandbox_name(w, root): s for w, s in statuses.items()})


def _claim(root, worker, path, ticket="RT-1"):
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    assert ledger.claim([path], worker, ticket).ok
    return ledger


# --- 1. what holds a slot --------------------------------------------------------


@pytest.mark.parametrize("status", ["active", "idle", "stopped", "unknown"])
def test_any_sandbox_that_EXISTS_holds_a_slot(tmp_path, status):
    """⚠ Including `stopped`. Only a `destroy` frees a slot, so a stopped
    sandbox is the exact case somebody hits: they stopped the Worker, released
    its claims, and the cap is still full."""
    root = _project(tmp_path, ("alpha",))
    got = held_slots(root, ["alpha"], listing=_listed(root, alpha=status))
    assert [(s.worker, s.status) for s in got] == [("alpha", status)]


def test_a_sandbox_that_is_gone_holds_nothing(tmp_path):
    root = _project(tmp_path, ("alpha",))
    assert held_slots(root, ["alpha"], listing=SandboxListing({})) == []


def test_only_the_workers_asked_about_are_reported(tmp_path):
    root = _project(tmp_path)
    got = held_slots(
        root, ["alpha"], listing=_listed(root, alpha="active", beta="active")
    )
    assert [s.worker for s in got] == ["alpha"]


def test_they_come_back_in_a_stable_order(tmp_path):
    root = _project(tmp_path)
    got = held_slots(
        root, ["beta", "alpha"], listing=_listed(root, alpha="idle", beta="active")
    )
    assert [s.worker for s in got] == ["alpha", "beta"]


# --- 2. the shape the ticket is named for ---------------------------------------


def test_a_slot_with_no_claims_behind_it_is_the_IDLE_one(tmp_path):
    """The work is done or taken away, the paths are free, and the slot is
    still gone. That is SCRUM-70."""
    root = _project(tmp_path, ("alpha",))
    got = held_slots(root, ["alpha"], listing=_listed(root, alpha="idle"), claims=[])
    assert len(got) == 1 and got[0].idle is True
    assert "holds NO claims" in got[0].line()
    assert "releasing claims does not free it" in got[0].line()
    assert idle_only(got) == got


def test_a_slot_WITH_claims_is_ordinary_and_not_a_fault(tmp_path):
    """The control. A Worker that is still claiming paths and occupying a slot
    is a Worker doing its job; reporting that as a problem would make the line
    noise and teach a reader to skim it."""
    root = _project(tmp_path, ("alpha",))
    ledger = _claim(root, "alpha", "src/a.py")
    got = held_slots(
        root,
        ["alpha"],
        listing=_listed(root, alpha="active"),
        claims=ledger.list_claims(),
    )
    assert len(got) == 1 and got[0].idle is False
    assert got[0].claims == 1
    assert "holding a slot and 1 claim(s)" in got[0].line()
    assert idle_only(got) == []


# --- 3. "could not ask" is not "nothing is held" --------------------------------


def test_an_unaskable_yoloai_is_reported_as_itself(tmp_path):
    """🔴 Told "no slots held" when rite could not look, a reader is told
    their capacity is free at the one moment nobody knows. The listing
    carries that distinction and this must not flatten it."""
    root = _project(tmp_path, ("alpha",))
    got = held_slots(
        root,
        ["alpha"],
        listing=SandboxListing({}, known=False, unknown="yoloai not found"),
    )
    assert isinstance(got, str)
    assert "could not ask yoloAI" in got and "yoloai not found" in got
    # And a caller that acts on idle slots acts on nothing.
    assert idle_only(got) == []


def test_an_unreadable_claims_ledger_is_reported_not_guessed(tmp_path):
    root = _project(tmp_path, ("alpha",))

    class _Explodes:
        def list_claims(self):
            raise OSError("torn file")

    import rite_ai.reporting.held_slots as module

    original = module.ClaimsLedger if hasattr(module, "ClaimsLedger") else None
    (root / ".rite" / "claims.json").write_text("{not json")
    got = held_slots(root, ["alpha"], listing=_listed(root, alpha="idle"))
    assert isinstance(got, str), got
    assert "could not be read" in got
    assert original is None  # the import is local, which is what keeps it pure


# --- 4. how to free it ----------------------------------------------------------


def test_the_owner_is_told_destroy_and_that_stop_is_not_enough():
    slot = HeldSlot("alpha", "stopped", 0)
    said = slot.how_to_free_it()
    assert "rite sandbox destroy alpha" in said
    assert "stop alpha` does not" in said


def test_a_manager_is_told_to_ASK_because_it_cannot_destroy_one(tmp_path):
    """B9: a Manager's sandbox cannot reach a Worker's, so it asks (SCRUM-59).
    Telling it to run `rite sandbox destroy` would be telling it to run a
    command the kernel refuses."""
    slot = HeldSlot("alpha", "idle", 0)
    said = slot.how_to_free_it("lead")
    assert "rite request destroy alpha" in said
    assert "rite sandbox destroy" not in said


# --- 5. the three surfaces that were lying --------------------------------------


def test_rite_status_says_the_slots_it_holds(tmp_path, monkeypatch):
    """🔴 "no active claims" beside every slot occupied."""
    from rite_ai.reporting.status import collect_status, format_status

    root = _project(tmp_path)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes",
        lambda *a, **kw: _listed(root, alpha="idle", beta="active"),
    )
    text = format_status(collect_status(root, board=False))
    assert "no active claims" in text, "the claims line is unchanged"
    assert "slots held (2):" in text
    assert "held by alpha: its sandbox is idle and it holds NO claims" in text
    # ⚠ **Not the bare `  alpha:` the workers section uses.** Two lines about
    # one Worker in the same shape is ambiguous output, and the full suite
    # caught it: nine cases in
    # `test_every_view_says_what_the_sandbox_said` select the workers line by
    # `startswith("  alpha:")` and got two.
    assert "\n  alpha: its sandbox" not in text
    assert "counts against sandbox.max_concurrent_workers" in text


def test_rite_status_says_when_it_could_not_ask(tmp_path, monkeypatch):
    from rite_ai.reporting.status import collect_status, format_status

    root = _project(tmp_path)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes",
        lambda *a, **kw: SandboxListing({}, known=False, unknown="yoloai not found"),
    )
    text = format_status(collect_status(root, board=False))
    assert "slots held: rite could not ask yoloAI" in text


def test_rite_status_says_nothing_when_no_slot_is_held(tmp_path, monkeypatch):
    """The control: a project with nothing running gains no line."""
    from rite_ai.reporting.status import collect_status, format_status

    root = _project(tmp_path)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes", lambda *a, **kw: SandboxListing({})
    )
    text = format_status(collect_status(root, board=False))
    assert "slots held" not in text


def test_release_says_the_slot_is_still_held(tmp_path, monkeypatch):
    """🔴 The headline: "released 1 claim(s) for alpha" and the next Worker
    still cannot start."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _project(tmp_path, ("alpha",))
    _claim(root, "alpha", "src/a.py")
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes", lambda *a, **kw: _listed(root, alpha="idle")
    )
    got = CliRunner().invoke(cli, ["release", "--worker", "alpha"])
    assert got.exit_code == 0, got.output
    assert "released 1 claim(s) for alpha" in got.output
    assert "SLOT STILL HELD" in got.output
    assert "rite sandbox destroy alpha" in got.output


def test_release_says_nothing_when_the_sandbox_is_gone(tmp_path, monkeypatch):
    """The control. A release that genuinely freed everything gains no line."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _project(tmp_path, ("alpha",))
    _claim(root, "alpha", "src/a.py")
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes", lambda *a, **kw: SandboxListing({})
    )
    got = CliRunner().invoke(cli, ["release", "--worker", "alpha"])
    assert got.exit_code == 0, got.output
    assert "SLOT STILL HELD" not in got.output


def test_force_release_says_it_too(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _project(tmp_path, ("alpha",))
    _claim(root, "alpha", "src/a.py")
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes",
        lambda *a, **kw: _listed(root, alpha="stopped"),
    )
    got = CliRunner().invoke(
        cli,
        ["release", "--force", "src/a.py", "--by", "ops", "--reason", "stale"],
    )
    assert got.exit_code == 0, got.output
    assert "force-released 1 claim(s)" in got.output
    assert "SLOT STILL HELD" in got.output
    assert "its sandbox is stopped" in got.output


# --- 6. the journal class it unblocked ------------------------------------------


def test_the_journal_can_record_a_held_slot_now(tmp_path):
    """`recording.NOT_YET` named `idle-slot-held` as waiting on this ticket,
    and an entry LEAVING that dict is the shape to want: the gap closed rather
    than the claim being quietly widened."""
    from rite_ai.managers import recording

    assert recording.IDLE_SLOT_HELD in recording.EVENTS
    assert recording.NOT_YET == {}


def test_the_supervisor_records_it_at_both_boundaries():
    """Wired, not only written. And it SHARES the boundary's one `yoloai ls`
    with reconcile rather than taking a second."""
    import inspect

    from rite_ai.managers import supervise

    src = inspect.getsource(supervise._supervise)
    assert "_record_idle_slots(root, manager, say, recorder, at_start)" in src
    assert "_record_idle_slots(root, manager, say, recorder, per_cycle)" in src
    body = inspect.getsource(supervise._record_idle_slots)
    assert "recording.IDLE_SLOT_HELD" in body
    # Only the IDLE ones: a Worker still claiming paths is not a journal entry.
    assert "idle_only(slots)" in body
    # And "could not ask" is said, never recorded as a failure that happened.
    assert "say(slots)" in body


def _recorded(root, listing):
    """Drive `_record_idle_slots` and collect what it recorded and said."""
    from unittest.mock import patch

    from rite_ai.managers import supervise

    said: list[str] = []
    events: list = []
    with patch("rite_ai.sandbox.list_sandboxes", lambda *a, **kw: listing):
        supervise._record_idle_slots(
            root, "lead", said.append, events.append, lambda: listing
        )
    return events, said


def test_a_cannot_ask_listing_is_SAID_and_never_recorded(tmp_path):
    """🔴 Behaviourally, not by reading the source — the source pin beside
    this survives a mutation that keeps the `say` line and makes it
    unreachable.

    ⚠ **The journal records failures that HAPPENED.** An unreachable yoloAI is
    not one: nothing is known to be holding a slot, so an entry would be the
    journal asserting a state nobody observed — which is the exact defect
    SCRUM-71 is about. It goes to the terminal instead.
    """
    root = _project(tmp_path, ("alpha",))
    events, said = _recorded(
        root, SandboxListing({}, known=False, unknown="yoloai not found")
    )
    assert events == [], "an unanswerable question is not a journal entry"
    assert any("could not ask yoloAI" in line for line in said), said


def test_an_idle_slot_IS_recorded(tmp_path):
    """The control for the test above, and for the whole wiring."""
    root = _project(tmp_path, ("alpha",))
    events, said = _recorded(root, _listed(root, alpha="idle"))
    assert len(events) == 1, events
    assert events[0].kind == "idle-slot-held"
    assert events[0].subject == "alpha"
    assert "holds NO claims" in events[0].observed
    assert "so the next Worker" in events[0].expected
    assert "idle-slot" in events[0].anchor


def test_a_slot_with_claims_is_not_recorded(tmp_path):
    """The other control: a Worker doing its job is not a journal entry."""
    root = _project(tmp_path, ("alpha",))
    _claim(root, "alpha", "src/a.py")
    events, _said = _recorded(root, _listed(root, alpha="active"))
    assert events == [], events


def test_a_project_with_no_workers_records_nothing(tmp_path):
    root = _project(tmp_path, ())
    events, said = _recorded(root, _listed(root))
    assert events == [] and said == []
