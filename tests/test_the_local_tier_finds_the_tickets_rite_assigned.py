"""The local tier must ask the board the question rite's own assignment answers.

🔴 **Measured against a real private GitHub board on 2026-10-08**, with a
ticket assigned to Manager `lead` the way rite assigns one (the label):

    no filter          (CONTROL) -> ['1']    the board can see the ticket
    label='lead'                 -> ['1']    rite's assignment mechanism
    assignee='lead'              -> []       what `_local_tier_tickets` asks

`gh issue list --assignee lead` matches GitHub's own assignee field, which is
a GitHub *login*. Nothing in rite's automated flow ever puts a Manager's name
there: `backend.assign` has exactly one caller, the operator-run `rite board
assign`. What rite writes when it assigns a ticket to a Manager is the LABEL —
`rite board label` is documented as "the assignment mechanism", and
`coordination.distribution` reads it back with `TicketFilter(label=manager)`.

So on a GitHub board the staged pipeline's driver finds nothing, every cycle,
and the headline mixed fleet of SCRUM-72 — a Claude Manager driving a GPU
Worker — is wired correctly and still never runs.

⚠ **Why the suite could not see it.** Every existing test of
`_local_tier_tickets` uses a board whose `list_tickets(self, filters=None)`
ignores `filters` and returns everything. A board that answers every question
the same way cannot tell `assignee=` from `label=`, so the tests pass under
either spelling and passed under the wrong one. The board below answers the
filter, and `test_the_board_here_can_tell_the_two_apart` is its control —
without that control this file would be another board that always says yes.
"""

from __future__ import annotations

from unittest.mock import patch

from rite_ai.cli.main import _local_tier_tickets
from rite_ai.refinement.status import REFINED, Status
from rite_ai.tickets.interface import Ticket, TicketFilter, TicketPage

TICKET = "T-1"
MANAGER = "lead"

from rite_ai.coordination.ticket_labels import SCHEDULED  # noqa: E402


class _BoardThatAnswersTheFilter:
    """A board with GitHub's semantics: `assignee` is the backend's own
    assignee field, `label` is what rite writes."""

    def __init__(self, rows):
        # rows: (ident, labels, assignee)
        self.rows = list(rows)
        self.asked = []

    def list_tickets(self, filters=None):
        f = filters or TicketFilter()
        self.asked.append(f)
        out = []
        for ident, labels, assignee in self.rows:
            if f.assignee and f.assignee != assignee:
                continue
            if f.label and f.label not in labels:
                continue
            if f.labels and not all(lbl in labels for lbl in f.labels):
                continue
            out.append(Ticket(id=ident, title=ident))
        return TicketPage(out)


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
        f"  - name: {MANAGER}\n    engine: claude\n"
        "    duties: [plan-review, decide, board, route, integrate]\n"
    )
    return tmp_path


def _local_worker(root, name="gpu1", ticket=TICKET):
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record

    d = root / "workers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "worker.yml").write_text(
        f"worker:\n  name: {name}\n  manager: {MANAGER}\n  modules: []\n"
        "  engine: local:small\n  model: qwen3:8b\n"
        "  endpoint: http://localhost:11434\n  agent: goose\n"
        "  context_window: 32768\n"
    )
    record.write(
        root,
        name,
        ticket,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(root / ".rite" / "modules.yaml"),
    )
    return name


def test_the_board_here_can_tell_the_two_apart():
    """The CONTROL. Without it, a board that always returned the ticket would
    make the test below pass for the wrong reason — which is the exact way
    this defect survived its own tests."""
    board = _BoardThatAnswersTheFilter([(TICKET, [SCHEDULED], "")])
    assert [t.id for t in board.list_tickets(TicketFilter())] == [TICKET]
    assert [t.id for t in board.list_tickets(TicketFilter(label=SCHEDULED))] == [TICKET]
    assert [t.id for t in board.list_tickets(TicketFilter(assignee=MANAGER))] == []
    # ...and it says yes to a real assignee, so it is not simply refusing.
    other = _BoardThatAnswersTheFilter([(TICKET, [], MANAGER)])
    assert [t.id for t in other.list_tickets(TicketFilter(assignee=MANAGER))] == [
        TICKET
    ]


def test_the_driver_finds_a_ticket_assigned_the_way_rite_assigns_it(tmp_path):
    """🔴 The defect. A refined ticket, labelled for this Manager, held by its
    LOCAL Worker — everything the pipeline needs — and the driver sees none."""
    root = _project(tmp_path)
    _local_worker(root)
    board = _BoardThatAnswersTheFilter([(TICKET, [SCHEDULED], "")])
    with _refined():
        found, why = _local_tier_tickets(root, board, MANAGER)
    assert why == "", why
    assert found == [TICKET], (
        "the local tier found no ticket for a refined, SCHEDULED ticket held by "
        "this Manager's local Worker. The board read must ask the BACKLOG's "
        "question (`label=SCHEDULED`, as `loop._ready` asks it); the "
        "per-Manager scoping is `_local_worker_holds`'s job and is exact. "
        "Asking the board to scope produced two wrong spellings (SCRUM-79): "
        "`assignee=<manager>`, which no rite flow ever sets, and "
        "`label=<manager>`, which is how work is ROUTED to another Manager and "
        "is on nothing in a single-Owner project."
    )
