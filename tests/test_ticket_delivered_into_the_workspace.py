"""A Worker's ticket is read on the host and delivered into its workspace.

A sandboxed Worker holds no board credential (§5.3.4, narrowed 2026-09-29),
so `rite board show` cannot work inside it. `rite sandbox start` reads the
ticket where every other board read happens, outside the sandbox, and
writes it to `workers/<w>/TICKET.md` before yoloAI copies the directory in.
What the Worker gets is what the host read, rendered as `rite board show`
renders it, with the board and the time of the read, and said to be a copy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.sandbox.delivery import (
    DELIVERY_FILE,
    delivery_text,
    render_ticket,
)
from rite_ai.tickets import BackendError
from rite_ai.tickets.interface import Ticket
from tests.refined_board import board_with

TICKET = Ticket(
    id="KAN-7",
    title="timout is way too long",
    status="To Do",
    labels=["scheduled"],
    description="make it configurable or smth",
    url="https://example.atlassian.net/browse/KAN-7",
)


class _Board:
    def __init__(self, ticket=TICKET):
        self.ticket = ticket
        self.reads: list[str] = []

    def read(self, ticket_id):
        self.reads.append(ticket_id)
        return self.ticket


def _project(tmp_path: Path, monkeypatch) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        "modules:\n  app:\n    path: app/\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: jira\n  site: example.atlassian.net\n"
        "  projects:\n    workers: KAN\nsandbox:\n  enabled: true\n"
        "  backend: seatbelt\n"
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: [app]\n"
    )
    monkeypatch.chdir(tmp_path)
    return worker


def _start(board, *extra: str):
    seen: dict = {}

    def run(args, *a, **kw):
        if "new" in args:
            seen["new"] = True
            if "--prompt-file" in args:
                seen["prompt"] = Path(args[args.index("--prompt-file") + 1]).read_text()
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        patch("rite_ai.cli.main._ticket_backend", return_value=(board, None)),
        patch("rite_ai.cli.main._worker_cannot_deliver", return_value=None),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", *extra]
        )
    return result, seen


SITE = "example.atlassian.net"


def test_the_worker_gets_what_the_host_read_with_when_and_where(tmp_path, monkeypatch):
    worker = _project(tmp_path, monkeypatch)
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE) as (board, _):
        result, seen = _start(_Board(), "--ticket", "KAN-7")

    assert result.exit_code == 0, result.output
    assert board.reads == ["KAN-7"]
    text = (worker / DELIVERY_FILE).read_text()
    assert "Read from Jira example.atlassian.net, project KAN at " in text
    assert "a copy taken at that moment, not the live ticket" in text
    assert "make it configurable or smth" in text
    read_at = text.split(" at ", 1)[1].split(" (UTC)")[0]
    assert read_at in seen["prompt"], "the prompt names the same moment"
    assert DELIVERY_FILE in seen["prompt"]


def test_the_ticket_block_is_exactly_what_board_show_prints(tmp_path, monkeypatch):
    """One rendering, so the file and `rite board show` cannot drift."""
    worker = _project(tmp_path, monkeypatch)
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        _start(_Board(), "--ticket", "KAN-7")
    with patch("rite_ai.cli.main._ticket_backend", return_value=(_Board(), None)):
        shown = CliRunner().invoke(cli, ["board", "show", "KAN-7"]).output

    assert f"```\n{shown.rstrip()}\n```" in (worker / DELIVERY_FILE).read_text()


def test_hidden_text_is_made_visible_in_the_delivery(tmp_path, monkeypatch):
    """N1: the Worker reads what a reviewer sees in the tracker."""
    worker = _project(tmp_path, monkeypatch)
    hidden = Ticket(id="KAN-7", title="ok", description="do this\u200b quietly")
    with board_with(tmp_path, monkeypatch, hidden, jira_site=SITE):
        _start(_Board(hidden), "--ticket", "KAN-7")

    text = (worker / DELIVERY_FILE).read_text()
    assert "\u200b" not in text
    assert "ZERO WIDTH SPACE" in text


def test_a_ticket_that_cannot_be_read_refuses_the_start(tmp_path, monkeypatch):
    worker = _project(tmp_path, monkeypatch)
    (worker / DELIVERY_FILE).write_text("# Ticket KAN-6, an old one\n")
    failed = BackendError("JIRA rejected the credentials")
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE, error=failed):
        result, seen = _start(_Board(), "--ticket", "KAN-7")

    assert result.exit_code == 1
    *_, detail, last = result.output.strip().splitlines()
    # UNREADABLE, said as rite could not check: never as "no definition of done".
    assert "ticket KAN-7: UNREADABLE" in detail
    assert "JIRA rejected the credentials" in detail
    assert last.startswith("UNREADABLE: rite could not confirm")
    assert "new" not in seen, "no sandbox was created"
    assert not (worker / DELIVERY_FILE).exists()


def test_a_start_without_a_ticket_removes_the_previous_copy(tmp_path, monkeypatch):
    worker = _project(tmp_path, monkeypatch)
    (worker / DELIVERY_FILE).write_text("# Ticket KAN-6, an old one\n")

    result, _ = _start(_Board())

    assert result.exit_code == 0, result.output
    assert not (worker / DELIVERY_FILE).exists()


def test_further_host_read_content_is_a_section_of_the_same_file():
    """The one path for board content into a Worker (TR4's refinement record
    is the first expected)."""
    text = delivery_text(
        render_ticket(TICKET),
        "2026-09-29T08:00:00Z",
        "Jira example.atlassian.net",
        sections=(("Agreed refinement record", "Done when: a --timeout flag."),),
    )
    assert text.index("## The ticket") < text.index("## Agreed refinement record")
    assert text.rstrip().endswith("Done when: a --timeout flag.")


def test_the_workers_instructions_point_at_the_file_not_the_board():
    from rite_ai.config.models import WorkerManifest
    from rite_ai.workspace.manage import render_worker_claude_md

    md = render_worker_claude_md(WorkerManifest(name="alpha", modules=[]))
    section = md.split("## Your ticket", 1)[1].split("\n## ", 1)[0]
    assert DELIVERY_FILE in section
    assert "`rite board show` will not work here" in section
    assert "not the live ticket" in section
    # TR4: the Worker works to the record rite delivered, never to the title.
    assert '"Agreed definition of done"' in section
    assert "Cite the record id" in section
