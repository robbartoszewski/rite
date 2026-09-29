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
    last = result.output.strip().splitlines()[-1]
    assert last.startswith("NOT REFINED") and "rite refine accept 7" in last
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


# --- the refusal reaches the Manager, with what to do (DF13's path) ----------


def test_every_refusal_line_names_a_remedy_and_survives_the_brokers_cut():
    """`broker.honour` keeps the last line of stderr, cut to 200 characters,
    and that is what the Manager's next instruction carries. So the last line
    must name the state and the remedy, for the longest ticket id accepted."""
    from rite_ai.cli.main import refused_for_refinement
    from rite_ai.managers.broker import _TICKET_MAX
    from rite_ai.refinement import status as st

    longest = "K" * _TICKET_MAX
    for state in (st.NOT_REFINED, st.STALE, st.CONFLICT, st.UNREADABLE):
        line = refused_for_refinement(state, longest)
        assert len(line) <= 200, (state, len(line))
        assert line.startswith(state)
        verb = "accept" if state in (st.NOT_REFINED, st.STALE) else "status"
        assert f"rite refine {verb} {longest}" in line


def test_a_managers_refused_request_tells_it_the_state_and_the_remedy(
    tmp_path, monkeypatch
):
    """End to end on the Manager's side: the CLI's real refusal, through the
    broker's real extraction and DF13's note, into the Manager's inbox."""
    import subprocess

    from rite_ai.managers import broker as broker_mod
    from rite_ai.managers.mailbox import INBOX, read
    from rite_ai.managers.supervise import _honour_worker_requests

    _project(tmp_path, monkeypatch)
    with board_with(tmp_path, monkeypatch, TICKET, refined=False):
        cli_result, _ = _start("--ticket", "7")
    assert cli_result.exit_code == 1

    def launched(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", cli_result.output)

    monkeypatch.setattr(broker_mod.subprocess, "run", launched)
    request = broker_mod.Request(worker="alpha", ticket="7")
    requests = broker_mod.requests_dir(tmp_path, "lead")
    requests.mkdir(parents=True, exist_ok=True)
    (requests / "1.json").write_text('{"worker": "alpha", "ticket": "7"}')
    _honour_worker_requests(
        tmp_path,
        "lead",
        lambda raw: broker_mod.honour(tmp_path, request),
        lambda _line: None,
    )
    (note,) = read(tmp_path, "lead", INBOX)
    assert "NOT started: NOT REFINED" in note.text
    assert 'rite refine accept 7 --item "…"' in note.text


def test_ticket_md_and_the_ticket_command_send_a_worker_to_the_record():
    """TR3: a Worker works to rite's record, and judges no ticket "complete
    enough" itself. Neither text still says "stop rather than guessing", and
    `/ticket` does not send a sandboxed Worker to a command it cannot run."""
    from rite_ai.sandbox.delivery import delivery_text, render_ticket

    root = Path(__file__).resolve().parent.parent
    command = (root / "templates" / "commands" / "ticket.md").read_text()
    step_one = command.split("\n2. ", 1)[0]
    assert "stop rather than guessing" not in command
    assert '"Agreed definition of done"' in step_one
    assert "try `rite board show` or `rite refine status`" in step_one
    # Why the Worker does not check, beside the instruction not to, so the
    # branching is not "fixed" by adding a check back.
    assert "rite checked that record on the host, in the same\n     read" in step_one
    assert "UNREADABLE means rite could not check" in step_one

    text = delivery_text(render_ticket(TICKET), "2026-09-29T00:00:00Z", "GitHub")
    assert "stop rather than guessing" not in text
    assert "Work to the agreed definition of done" in text


def test_refine_records_with_accept_after_a_yes_and_never_rewrites_the_ticket():
    """TR3: `/refine` is the refinement protocol with a person present. It ends
    at `rite refine accept`, only after an explicit yes, and leaves the
    person's own text alone (TR8's G2: an agent does not replace their words)."""
    root = Path(__file__).resolve().parent.parent
    text = (root / "templates" / "commands" / "refine.md").read_text()
    assert "rite refine accept <ID> --item" in text
    assert "Nothing is recorded until they say\n   yes." in text
    assert "Do not edit the ticket's title or description." in text
    assert "Write the ticket body to full quality" not in text


def test_the_queue_rule_says_only_refined_is_ready():
    """Now true, and enforced by TR4: no Worker starts without a record."""
    from rite_ai.cli.init.claude_gen import _working_the_queue_section

    section = _working_the_queue_section()
    assert "A ticket rite does not report REFINED is not ready" in section
    assert "no Worker starts without one" in section
