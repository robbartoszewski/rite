"""SCRUM-72 §3.3 — the driver keys off WORKERS, not the Manager's engine.

🔴 **This is SCRUM-72's root cause, in one line of wiring.** `cli.main` wired
the local-tier driver as:

    local_tier=(... if role.is_local else None)

so the staged pipeline ran only under a LOCAL Manager. In the headline mixed
fleet — a Claude `lead` with one Claude Worker and one GPU Worker, which is
§4.2's acceptance fleet — no Manager drove the GPU Worker. It was started, it
claimed its paths, and it sat there: "A local/GPU Worker is never driven under
a Claude Manager."

**The filter moves to the Worker.** The driver is always wired, and
`_local_tier_tickets` keeps only the tickets a LOCAL Worker of this Manager is
recorded as started on — so a Claude Manager's own Claude Workers are never
put through a decomposer, and its GPU Worker is driven. Keying off the
Manager's engine there and off nothing here would have swapped one wrong
answer for another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rite_ai.cli.main import _local_tier_tickets, _local_worker_holds
from rite_ai.refinement.status import REFINED, Status
from rite_ai.tickets.interface import Ticket

TICKET = "T-1"


class _Board:
    def __init__(self, ids):
        self.ids = list(ids)

    def list_tickets(self, filters=None):
        return type(
            "Page", (), {"tickets": [Ticket(id=i, title=i) for i in self.ids]}
        )()


def _refined():
    return patch(
        "rite_ai.refinement.status.status",
        lambda board, ident: Status(state=REFINED, record=None, detail="", ticket=None),
    )


def _project(tmp_path):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n"
        "  - name: lead\n    engine: claude\n"
        "    duties: [plan-review, decide, board, route, integrate]\n"
        "  - name: planner\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose, execute]\n"
    )
    return tmp_path


def _worker(root, name, *, engine, manager="lead", ticket=TICKET):
    """A Worker of `manager`, recorded as started on `ticket`."""
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record

    d = root / "workers" / name
    d.mkdir(parents=True, exist_ok=True)
    body = f"worker:\n  name: {name}\n  manager: {manager}\n  modules: []\n"
    if engine != "claude":
        body += (
            f"  engine: {engine}\n  model: qwen3:8b\n"
            "  endpoint: http://localhost:11434\n  agent: goose\n"
            "  context_window: 32768\n"
        )
    (d / "worker.yml").write_text(body)
    if ticket:
        record.write(
            root,
            name,
            ticket,
            parse_config(root / ".rite" / "config.yaml"),
            parse_modules(root / ".rite" / "modules.yaml"),
        )
    return name


# --- 1. the wiring itself --------------------------------------------------------


def test_the_driver_is_wired_for_every_manager_engine():
    """🔴 `if role.is_local` is gone, by AST. A comment recording what the
    line used to say names `is_local`, so this walks the tree rather than the
    text — the lesson from §3.3b's own control."""
    import ast
    import inspect

    from rite_ai.cli import main

    src = inspect.getsource(main)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "local_tier":
            continue
        # The value must be a plain lambda, not a conditional on anything.
        assert isinstance(node.value, ast.Lambda), (
            "the local-tier driver is wired conditionally again: SCRUM-72's "
            "root cause was exactly that"
        )
        reads = {n.attr for n in ast.walk(node.value) if isinstance(n, ast.Attribute)}
        assert "is_local" not in reads, reads
        return
    raise AssertionError("nothing wires local_tier any more")


# --- 2. the filter is the WORKER ------------------------------------------------


def test_a_claude_managers_GPU_worker_is_driven(tmp_path):
    """The headline mixed fleet, and the thing that did not happen before."""
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="lead")
    with _refined():
        tickets, why = _local_tier_tickets(root, _Board([TICKET]), "lead")
    assert why == ""
    assert tickets == [TICKET]


def test_a_claude_managers_CLAUDE_worker_is_NOT_driven(tmp_path):
    """⚠ The other half, and it matters as much. Removing the Manager-engine
    gate without putting the Worker filter in its place would have put a
    Claude Manager's own Claude Workers through a decomposer — the staged
    pipeline is for a local Worker's ticket and for no other."""
    root = _project(tmp_path)
    _worker(root, "claude1", engine="claude", manager="lead")
    with _refined():
        tickets, why = _local_tier_tickets(root, _Board([TICKET]), "lead")
    assert why == ""
    assert tickets == []


def test_a_local_managers_gpu_worker_is_still_driven(tmp_path):
    """The behaviour that existed before is unchanged: a local Manager with a
    local Worker still drives it."""
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="planner")
    with _refined():
        tickets, _ = _local_tier_tickets(root, _Board([TICKET]), "planner")
    assert tickets == [TICKET]


def test_a_mixed_fleet_drives_only_the_local_workers_ticket(tmp_path):
    root = _project(tmp_path)
    _worker(root, "claude1", engine="claude", manager="lead", ticket="T-CLAUDE")
    _worker(root, "gpu1", engine="local:small", manager="lead", ticket="T-GPU")
    with _refined():
        tickets, _ = _local_tier_tickets(root, _Board(["T-CLAUDE", "T-GPU"]), "lead")
    assert tickets == ["T-GPU"]


def test_another_managers_local_worker_is_not_this_managers_to_drive(tmp_path):
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="planner")
    with _refined():
        tickets, _ = _local_tier_tickets(root, _Board([TICKET]), "lead")
    assert tickets == []


@pytest.mark.parametrize("why", ["no worker record", "no project at all"])
def test_a_ticket_rite_cannot_place_a_worker_on_is_not_driven(tmp_path, why):
    """False for anything it cannot read: a ticket rite cannot place a Worker
    on is not one to put through a decomposer."""
    root = _project(tmp_path) if why == "no worker record" else tmp_path
    assert _local_worker_holds(root, "lead", TICKET) is False


def test_the_selector_is_the_one_the_executor_uses(tmp_path):
    """⚠ `loop._worker_for`, the SAME selector the executor and the delivery
    request use. The Worker whose ticket is driven, the Worker that runs a
    subtask and the Worker named in the delivery request are one by
    construction rather than by three functions agreeing."""
    import ast
    import inspect

    from rite_ai.cli import main

    src = inspect.getsource(main._local_worker_holds)
    assert "_worker_for" in {
        alias.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


# --- 3. a local Worker gets no prompt --------------------------------------------


def test_a_local_worker_is_started_with_no_prompt():
    """🔴 A prompt starts a FREE-FORM turn, and a local Worker's turns are not
    free-form: the pipeline hands it one approved subtask at a time, with that
    subtask's spec slice and its Level-2 approach. Pasting "work this ticket"
    into its idle sandbox started a second, ungated worker on the same ticket
    — inside the very sandbox the pipeline then places turns into."""
    import inspect

    from rite_ai.cli import main

    src = inspect.getsource(main)
    assert 'if getattr(manifest, "is_local", False):' in src
    assert "            prompt = None" in src
    assert "it is started IDLE and no " in src
    assert "the staged pipeline drives it one approved " in src
    # And it happens AFTER the prompt is built and BEFORE the sandbox starts,
    # or it would be suppressing a prompt that was already sent.
    built = src.index("Work ticket {ticket} to its agreed definition of done")
    suppressed = src.index(
        'if getattr(manifest, "is_local", False):\n            prompt = None'
    )
    started = src.index("    result = start_worker(")
    assert built < suppressed < started


# --- 4. an idle local Worker is not stalled --------------------------------------


def _stage_at(root, ticket, stage):
    from rite_ai.local import plan_state
    from rite_ai.local import stage as st

    state = plan_state.layer(root)
    state.write_state(
        st.key_for(ticket),
        st.render(
            st.Record(
                ticket=ticket,
                stage=stage,
                definition="rec-pinned",
                log=(st.Transition(frm=st.UNSTARTED, to=stage, at=1.0),),
            )
        ),
        state.read_state(st.key_for(ticket)).version,
    )


def _claimed(root, worker, paths=("src/a.py",)):
    """A claim, which is what makes silence a STALL rather than "not started
    yet" (`heartbeat.not_started`: "A claim is evidence a session did start").
    A local Worker holding its paths and saying nothing is the dogfood case."""
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    assert ledger.claim(list(paths), worker, TICKET).ok


def _watchdog(root):
    from rite_ai.watchdog import run_watchdog_check

    return run_watchdog_check(root)


@pytest.mark.parametrize(
    "stage",
    [
        "defined",
        "decomposed",
        "approved",
        "stepping",
        "recomposed",
        "delivery requested",
    ],
)
def test_an_idle_local_worker_is_not_reported_stalled(tmp_path, stage):
    """🔴 A local Worker NEVER beats: it is started idle and the pipeline
    places one subtask at a time, so it is silent while a plan is authored,
    while a reviewer is waited on, between subtasks and during the
    recomposition verify. The stall check can only ever produce a FALSE
    positive for it — and the dogfood already showed what a Manager does with
    a line that says "stalled"."""
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="lead")
    _claimed(root, "gpu1")
    _stage_at(root, TICKET, stage)

    got = _watchdog(root)
    assert [s.worker for s in got.stalled] == [], got.reasons
    assert any("does NOT send heartbeats" in r for r in got.reasons), got.reasons
    assert any(f"at stage {stage}" in r for r in got.reasons), got.reasons
    assert any("Do not restart it" in r for r in got.reasons), got.reasons


def test_a_CLAUDE_worker_with_no_heartbeat_is_still_stalled(tmp_path):
    """🔴 **The control, and the one that matters.** The subtraction must
    apply to a local Worker and to nothing else: a Claude Worker that stops
    beating IS stalled, and suppressing that would hide the failure the
    watchdog exists for."""
    root = _project(tmp_path)
    _worker(root, "claude1", engine="claude", manager="lead")
    _claimed(root, "claude1")
    _stage_at(root, TICKET, "stepping")  # irrelevant to a Claude Worker

    got = _watchdog(root)
    assert [s.worker for s in got.stalled] == ["claude1"], got.reasons
    assert any("claude1" in r and "stalled" in r for r in got.reasons), got.reasons


def test_a_local_worker_with_no_pipeline_stage_is_still_stalled(tmp_path):
    """The other half of the control. Silence is expected while the pipeline
    is driving it; a local Worker with no pipeline at all is just silent, and
    rite says so rather than assuming something is driving it."""
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="lead")
    _claimed(root, "gpu1")
    # No stage record written.
    got = _watchdog(root)
    assert [s.worker for s in got.stalled] == ["gpu1"], got.reasons


def test_a_local_worker_with_no_recorded_ticket_is_still_stalled(tmp_path):
    root = _project(tmp_path)
    _worker(root, "gpu1", engine="local:small", manager="lead", ticket="")
    _claimed(root, "gpu1")
    _stage_at(root, TICKET, "stepping")  # a stage for a ticket it is not on
    got = _watchdog(root)
    assert [s.worker for s in got.stalled] == ["gpu1"], got.reasons
