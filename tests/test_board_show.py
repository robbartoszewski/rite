"""A Worker has to read its ticket's scope before it starts; `list` and
`query` print one line per ticket and no description."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.tickets.interface import Ticket
from rite_ai.tickets.jira import _issue_to_ticket, adf_to_text

ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Export invoices"}]},
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Done when CSV downloads"}
                            ],
                        }
                    ],
                }
            ],
        },
    ],
}


def test_a_jira_description_reads_as_text():
    assert adf_to_text(ADF) == "Export invoices\n- Done when CSV downloads\n"
    ticket = _issue_to_ticket(
        {"key": "DEF-1", "fields": {"summary": "s", "description": ADF}}
    )
    assert ticket.description == "Export invoices\n- Done when CSV downloads"
    assert (
        _issue_to_ticket({"key": "DEF-2", "fields": {"description": None}}).description
        == ""
    )


def test_board_show_prints_the_description():
    backend = MagicMock()
    backend.read.return_value = Ticket(
        id="DEF-9",
        title="Export invoices",
        status="To Do",
        labels=["alpha"],
        description="Done when CSV downloads",
    )
    with patch("rite_ai.cli.main._ticket_backend", return_value=(backend, None)):
        result = CliRunner().invoke(cli, ["board", "show", "DEF-9"])
    assert result.exit_code == 0, result.output
    backend.read.assert_called_once_with("DEF-9")
    assert "DEF-9  [To Do]  Export invoices" in result.output
    assert "Done when CSV downloads" in result.output


def test_board_show_says_why_it_could_not_read():
    from rite_ai.tickets import BackendError

    backend = MagicMock()
    backend.read.return_value = BackendError("DEF-404 → 404")
    with patch("rite_ai.cli.main._ticket_backend", return_value=(backend, None)):
        result = CliRunner().invoke(cli, ["board", "show", "DEF-404"])
    assert result.exit_code == 1
    assert "404" in result.output
