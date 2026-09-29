"""TR4: a Worker is started only on a REFINED ticket, and is handed the record
rite checked, from the same read as the ticket text.

The property (the note's part 4, race 4): what was checked is what was
delivered. `refinement.status.of` reads the board once; `rite sandbox start`
writes that read's ticket and that read's record into `TICKET.md`, and never
reads the board again. Every other state refuses, names itself, and leaves no
copy of an earlier ticket behind.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.refinement.status import render_for_worker
from rite_ai.sandbox.delivery import DELIVERY_FILE
from rite_ai.tickets.interface import Ticket
from tests.refined_board import board_with

TICKET = Ticket(
    id="7",
    title="timout is way too long",
    status="OPEN",
    description="make it configurable or smth",
)


def _project(tmp_path: Path, monkeypatch) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        "modules:\n  app:\n    path: app/\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: github\n  repo: org/repo\n"
        "sandbox:\n  enabled: true\n  backend: seatbelt\n"
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: [app]\n"
    )
    monkeypatch.chdir(tmp_path)
    return worker


def _start(*args: str):
    seen: dict = {}

    def run(argv, *a, **kw):
        if "new" in argv:
            seen["new"] = True
            if "--prompt-file" in argv:
                seen["prompt"] = Path(argv[argv.index("--prompt-file") + 1]).read_text()
        stdout = '{"sandboxes": []}' if "ls" in argv else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        patch("rite_ai.cli.main._worker_cannot_deliver", return_value=None),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", *args]
        )
    return result, seen


def test_a_refined_ticket_is_delivered_with_the_record_rite_checked(
    tmp_path, monkeypatch
):
    worker = _project(tmp_path, monkeypatch)
    with board_with(tmp_path, monkeypatch, TICKET) as (board, record):
        result, seen = _start("--ticket", "7")

    assert result.exit_code == 0, result.output
    assert board.reads == ["7"], "one read of the board, and only one"
    text = (worker / DELIVERY_FILE).read_text()
    section = text.split("## Agreed definition of done\n\n", 1)[1]
    assert section.rstrip() == render_for_worker(record)
    assert "make it configurable or smth" in text.split("## The ticket", 1)[1]
    assert record.record_id in seen["prompt"]
    assert "Cite the record id" in seen["prompt"]


def test_an_unrefined_ticket_starts_no_worker_and_leaves_no_copy(tmp_path, monkeypatch):
    worker = _project(tmp_path, monkeypatch)
    (worker / DELIVERY_FILE).write_text("# Ticket 6, an old one\n")
    with board_with(tmp_path, monkeypatch, TICKET, refined=False):
        result, seen = _start("--ticket", "7")

    assert result.exit_code == 1
    assert "NOT REFINED" in result.output and "rite refine status 7" in result.output
    assert "new" not in seen, "no sandbox was created"
    assert not (worker / DELIVERY_FILE).exists()


def test_a_ticket_edited_after_its_record_starts_no_worker(tmp_path, monkeypatch):
    worker = _project(tmp_path, monkeypatch)
    before = Ticket(id="7", title=TICKET.title, description="the original text")
    with board_with(tmp_path, monkeypatch, TICKET, signed_against=before):
        result, seen = _start("--ticket", "7")

    assert result.exit_code == 1 and "STALE" in result.output
    assert "new" not in seen
    assert not (worker / DELIVERY_FILE).exists()


def test_a_prompt_files_an_unrefined_chore_and_starts_nothing(tmp_path, monkeypatch):
    """TRQ11 (Robert, 2026-09-29): the person's words become a chore,
    explicitly unrefined, refined later, and no Worker starts on it."""
    _project(tmp_path, monkeypatch)
    made = []

    class Board:
        def create(self, title, description="", labels=None):
            made.append(labels)
            return Ticket(id="8", title=title)

    with patch("rite_ai.cli.main._ticket_backend", return_value=(Board(), None)):
        result, seen = _start("--prompt", "add a CSV export")

    assert result.exit_code == 1
    assert made == [["chore", "scheduled"]]
    assert "filed chore 8" in result.output
    assert "rite refine accept 8" in result.output
    assert "rite sandbox start alpha --ticket 8" in result.output
    assert "new" not in seen
