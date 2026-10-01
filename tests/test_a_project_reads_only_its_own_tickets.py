"""A project reads only its own tickets on a board it shares (v0.7.0 dogfood S1).

Measured before the yoloAI alpha run: three rite projects on this machine read
Jira KAN on `ritetest`. Refinement lists `labels = "scheduled"` on the whole
board, so yoloAI's KAN-28/29 (no label) would never have been refined and
pingr's KAN-6..9 (`scheduled`) would have been refined against yoloAI.

Robert, 2026-09-29: option A (`ticket_backend.scope_label`, ANDed into every
list and stamped on every ticket rite creates), `rite init` defaulting it to
the project's name, a hard refusal to start a Manager when another project on
this machine reads the same board and either is unscoped, and `rite doctor`
flagging the same.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.cli.main as main
import rite_ai.sandbox as sb
from rite_ai.cli.main import cli
from rite_ai.config.models import TicketBackendConfig
from rite_ai.config.parse import ParseError, parse_config
from rite_ai.credentials.store import namespaced, store
from rite_ai.machine_projects import known_projects, note
from rite_ai.tickets import create_backend_from_config
from rite_ai.tickets.interface import BackendError, Ticket, TicketBackend, TicketFilter
from rite_ai.tickets.scope import Scoped, label_for, unwrapped

# --- an in-memory board, labels honoured as both real boards honour them ------


class Board(TicketBackend):
    def __init__(self):
        self.issues: dict[str, Ticket] = {}

    def create(self, title, description="", labels=None):
        n = f"KAN-{len(self.issues) + 1}"
        self.issues[n] = Ticket(
            id=n, title=title, status="To Do", labels=list(labels or [])
        )
        return copy.deepcopy(self.issues[n])

    def read(self, ticket_id):
        if ticket_id not in self.issues:
            return BackendError("no such ticket")
        return copy.deepcopy(self.issues[ticket_id])

    def label(self, ticket_id, labels, remove=None):
        t = self.issues[ticket_id]
        t.labels = [x for x in t.labels if x not in (remove or [])] + [
            x for x in labels if x not in t.labels
        ]

    def matches(self, ticket, filters):
        f = filters or TicketFilter()
        wanted = ([f.label] if f.label else []) + list(f.labels or [])
        return all(w in ticket.labels for w in wanted)

    def list_tickets(self, filters=None):
        return [
            copy.deepcopy(t) for t in self.issues.values() if self.matches(t, filters)
        ]

    def update(self, ticket_id, **fields):
        return None

    def move(self, ticket_id, status):
        return None

    def assign(self, ticket_id, worker):
        return None

    def comment(self, ticket_id, text):
        return None

    def query(self, raw_query):
        return list(self.issues.values())

    def link(self, ticket_id, target_id, link_type):
        return None


def _shared_board() -> Board:
    """KAN as it was: two of yoloAI's tickets unlabelled, one of pingr's
    scheduled."""
    board = Board()
    board.create("pingr retry on fail", labels=["scheduled", "pingr"])
    board.create("--env secrets show up in ps", labels=["scheduled", "yoloai"])
    board.create("sandbox can reach unix sockets", labels=["scheduled", "yoloai"])
    board.create("an unscoped scheduled ticket", labels=["scheduled"])
    return board


# --- 1. the query is scoped ------------------------------------------------------


def test_a_scoped_list_excludes_another_projects_tickets():
    listed = Scoped(_shared_board(), "yoloai").list_tickets(
        TicketFilter(label="scheduled")
    )
    assert sorted(t.id for t in listed) == ["KAN-2", "KAN-3"]


def test_control_the_same_board_unscoped_lists_everything():
    listed = _shared_board().list_tickets(TicketFilter(label="scheduled"))
    assert len(listed) == 4


def test_every_ticket_rite_creates_is_stamped():
    board = Board()
    made = Scoped(board, "yoloai").create("a chore", labels=["chore", "scheduled"])
    assert set(board.issues[made.id].labels) == {"chore", "scheduled", "yoloai"}


def test_a_ticket_rite_wrote_is_not_read_back_into_another_projects_list(
    tmp_path: Path,
):
    """Through the read-your-own-writes layer: rite labels pingr's ticket
    `scheduled` (so it is in the ledger), and the scoped list must still not
    offer it to yoloAI."""
    from rite_ai.tickets.own_writes import ReadsItsOwnWrites

    (tmp_path / ".rite").mkdir()
    raw = _shared_board()
    board = ReadsItsOwnWrites(Scoped(raw, "yoloai"), tmp_path, "mem:KAN")
    board.label("KAN-1", ["scheduled"])
    listed = board.list_tickets(TicketFilter(label="scheduled"))
    assert sorted(t.id for t in listed) == ["KAN-2", "KAN-3"]


def test_the_real_jira_query_carries_the_scope(monkeypatch):
    """The JQL Jira is sent, built through the one builder every board goes
    through, with the scope from config."""
    from rite_ai.tickets import jira as jira_mod
    from rite_ai.tickets.jira import JiraBackend, JiraConfig

    real = JiraBackend(
        JiraConfig(site="t.atlassian.net", email="e", token="t", project_key="KAN")
    )
    monkeypatch.setattr("rite_ai.tickets.create_backend", lambda *a, **k: real)
    sent: list[str] = []
    monkeypatch.setattr(
        jira_mod.JiraBackend,
        "_search_jql",
        lambda self, jql, **k: sent.append(jql) or [],
    )
    tb = TicketBackendConfig(
        type="jira",
        site="t.atlassian.net",
        projects={"workers": "KAN"},
        scope_label="yoloai",
    )
    board = create_backend_from_config(tb)
    board.list_tickets(TicketFilter(label="scheduled"))
    assert sent == ['project = KAN AND labels = "scheduled" AND labels = "yoloai"']
    # And every rite wrapper is seen through to the board itself.
    assert unwrapped(board) is real


# --- 2. init scopes a new project by its name -----------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


def test_init_scopes_a_new_project_by_its_name(tmp_path: Path, monkeypatch):
    root = tmp_path / "My Project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["init", "--yes"])
    assert result.exit_code == 0, result.output

    config = parse_config(root / ".rite" / "config.yaml")
    assert config.ticket_backend.scope_label == "my-project"
    # Seen by this machine from its first command.
    assert root.resolve() in known_projects()


def test_a_name_that_is_one_of_rites_labels_is_not_used_as_it_is():
    assert label_for("Scheduled") == "project-scheduled"
    assert label_for("  ") == "project-rite"
    assert label_for("yoloai") == "yoloai"


def test_an_unusable_scope_label_is_refused_not_dropped(tmp_path: Path):
    """Dropping it would leave the project reading every other project's
    tickets while its owner believes it is scoped."""
    path = tmp_path / "config.yaml"
    path.write_text("ticket_backend:\n  type: none\n  scope_label: two words\n")
    assert isinstance(parse_config(path), ParseError)


# --- 3. rite start refuses a Manager on an unscoped shared board -----------------


def _project(where: Path, *, scope: str, namespace: str, worker: str = "") -> Path:
    (where / ".rite").mkdir(parents=True)
    (where / ".rite" / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (where / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: jira\n  site: ritetest.atlassian.net\n"
        "  projects:\n    workers: KAN\n"
        f"  scope_label: '{scope}'\n"
        f"credentials:\n  namespace: {namespace}\n"
        "sandbox:\n  enabled: false\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    for ns_key in ("jira_email", "jira_token"):
        store(namespaced(namespace, ns_key), "x")
    if worker:
        (where / "workers" / worker).mkdir(parents=True)
        (where / "workers" / worker / "worker.yml").write_text(
            f"worker:\n  name: {worker}\n"
        )
    note(where)
    return where


@pytest.fixture
def started(monkeypatch):
    """Whether a Manager got as far as starting."""
    import rite_ai.managers.supervise as sup_mod

    seen: dict = {}

    def fake_supervise(root, manager, **kw):
        seen["manager"] = manager
        return type("R", (), {"ok": True, "reason": "done", "cycles": []})()

    monkeypatch.setattr(sup_mod, "supervise", fake_supervise)
    return seen


def _start(root: Path, monkeypatch):
    monkeypatch.chdir(root)
    return CliRunner().invoke(
        cli, ["start", "lead", "--sessions", "1", "--minutes", "1"]
    )


def test_start_refuses_when_the_other_project_is_unscoped(
    tmp_path: Path, monkeypatch, started
):
    yoloai = _project(tmp_path / "yoloai", scope="yoloai", namespace="yoloai-1a")
    pingr = _project(tmp_path / "pingr", scope="", namespace="pingr-2b")

    result = _start(yoloai, monkeypatch)
    assert result.exit_code == 1
    assert not started, "a Manager was started on a shared, unscoped board"
    assert "could take another project's tickets" in result.output
    assert str(pingr.resolve()) in result.output
    assert "Jira KAN on ritetest.atlassian.net" in result.output
    assert "only projects on this machine" in result.output


def test_start_refuses_when_this_project_is_unscoped(
    tmp_path: Path, monkeypatch, started
):
    _project(tmp_path / "yoloai", scope="yoloai", namespace="yoloai-1a")
    pingr = _project(tmp_path / "pingr", scope="", namespace="pingr-2b")

    result = _start(pingr, monkeypatch)
    assert result.exit_code == 1 and not started
    assert "this project" in result.output


def test_start_refuses_two_projects_with_one_scope_label(
    tmp_path: Path, monkeypatch, started
):
    a = _project(tmp_path / "a", scope="pingr", namespace="pingr-2b")
    _project(tmp_path / "b", scope="pingr", namespace="pingr-3c")

    result = _start(a, monkeypatch)
    assert result.exit_code == 1 and not started
    assert "same scope_label 'pingr'" in result.output


def test_control_two_scoped_projects_start(tmp_path: Path, monkeypatch, started):
    yoloai = _project(tmp_path / "yoloai", scope="yoloai", namespace="yoloai-1a")
    _project(tmp_path / "pingr", scope="pingr", namespace="pingr-2b")

    result = _start(yoloai, monkeypatch)
    assert started.get("manager") == "lead", result.output


def test_control_another_checkout_of_the_same_project_is_not_a_collision(
    tmp_path: Path, monkeypatch, started
):
    """Worktrees share the committed config, scope label and namespace."""
    main_checkout = _project(tmp_path / "main", scope="", namespace="rite-30ba")
    _project(tmp_path / "worktree", scope="", namespace="rite-30ba")

    result = _start(main_checkout, monkeypatch)
    assert started.get("manager") == "lead", result.output


def test_start_refuses_a_scope_label_that_is_a_workers_name(
    tmp_path: Path, monkeypatch, started
):
    root = _project(tmp_path / "p", scope="alpha", namespace="p-1", worker="alpha")
    result = _start(root, monkeypatch)
    assert result.exit_code == 1 and not started
    assert "also the name of 'alpha'" in result.output


# --- 4. rite doctor flags it -------------------------------------------------------


@pytest.fixture
def no_board_probe(monkeypatch):
    monkeypatch.setattr(main, "_doctor_board_can_create", lambda root, p: None)
    monkeypatch.setattr(
        main, "_doctor_refinement_reaches_you", lambda root, p: None, raising=False
    )


def test_doctor_flags_a_shared_unscoped_board(
    tmp_path: Path, monkeypatch, no_board_probe
):
    yoloai = _project(tmp_path / "yoloai", scope="yoloai", namespace="yoloai-1a")
    pingr = _project(tmp_path / "pingr", scope="", namespace="pingr-2b")
    monkeypatch.chdir(yoloai)

    result = CliRunner().invoke(cli, ["doctor"])
    assert f"board scope: {pingr.resolve()} also reads Jira KAN" in result.output
    assert result.exit_code == 1


def test_control_doctor_is_quiet_when_both_are_scoped(
    tmp_path: Path, monkeypatch, no_board_probe
):
    yoloai = _project(tmp_path / "yoloai", scope="yoloai", namespace="yoloai-1a")
    _project(tmp_path / "pingr", scope="pingr", namespace="pingr-2b")
    monkeypatch.chdir(yoloai)

    result = CliRunner().invoke(cli, ["doctor"])
    assert "board scope:" not in result.output
