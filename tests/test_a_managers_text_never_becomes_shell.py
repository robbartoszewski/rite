"""A Manager's text reaches rite on stdin and is never run as shell (F14).

WHY THIS EXISTS. In the v0.6.0 dogfood (2026-09-27) a Claude Owner ran
`rite reply --manager lead "… \\`rite update --files-only\\` …"`, as its
instructions taught. The shell ran the command in backticks — one that
changes files — and spliced its output into the reply sent to the person.
Claude Code did not ask: a substitution whose inner command is allowlisted
runs, and `python`, `rm`, `gh`, `git` and `env` are. A Manager's text often
quotes a ticket someone else wrote, so this is command injection.

The acceptance: text arrives verbatim and nothing in it runs, including when
the text is a TICKET's, routed through the Owner's instructions exactly as
rite composes them and run by a real shell. The control shows the test can
see a command run: the old double-quoted form runs the same canary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import _manager_roles, cli
from rite_ai.managers import MANAGER_ENV, routing, stdin_text
from rite_ai.managers.mailbox import INBOX, OUTBOX, how_to_reply, read
from rite_ai.managers.supervise import _substitutes
from rite_ai.tickets.interface import Ticket
from tests.refined_board import refined

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
    "      preset: lead\n    - name: helper\n      engine: claude\n"
    "      preset: executor\n"
)

SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]


def hostile(canary: Path) -> str:
    """Text as a stranger's ticket might carry it: every way into a shell a
    quoted heredoc must hold, and a guessed delimiter on a line of its own."""
    return (
        f"Fix the login. Don't break `touch {canary}-backtick`.\n"
        f"Also $(touch {canary}-dollar) and ${{HOME}} and \\n stay as text.\n"
        "EOF\n"
        "RITE_TEXT_000000000000\n"
        f"touch {canary}-after-a-guessed-end\n"
        "'single' \"double\" <<'X' and a trailing space "
    )


def canaries(canary: Path) -> list[Path]:
    return sorted(canary.parent.glob(canary.name + "*"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def _taught(instructions: str, verb: str) -> str:
    """The heredoc the instructions teach for `verb`, all three lines."""
    lines = instructions.splitlines()
    for i, line in enumerate(lines):
        if f" {verb} " in line and line.rstrip().endswith("'"):
            return "\n".join(lines[i : i + 3])
    raise AssertionError(f"no heredoc for {verb!r} in:\n{instructions}")


def _run(shell: str, script: str, root: Path, manager: str) -> None:
    env = {**os.environ, MANAGER_ENV: manager}
    env.pop("RITE_PROJECT_ROOT", None)
    done = subprocess.run(
        [shell, "-c", script], cwd=root, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_a_tickets_text_routed_as_taught_arrives_verbatim_and_runs_nothing(
    project, tmp_path_factory, shell
):
    """The path the RC run exercises: a ticket someone else wrote, quoted by
    the Owner into `rite route`, exactly as its instructions show it."""
    canary = tmp_path_factory.mktemp("canary") / "ran"
    ticket = Ticket(id="RT-7", title="login", description=hostile(canary))
    roles, _ = _manager_roles(project)
    taught = _taught(routing.briefing("lead", "lead", roles), "route")
    command, placeholder, end = taught.split("\n")
    script = "\n".join(
        [command.replace("<ID>", ticket.id).replace("<manager>", "helper")]
        + [ticket.description, end]
    )
    assert placeholder.startswith("<")

    _run(shell, script, project, "lead")
    (queued,) = routing._routes_dir(project, "lead").glob("*.json")
    assert json.loads(queued.read_text())["text"] == ticket.description
    routing.deliver_routes(
        project,
        "lead",
        "lead",
        ["lead", "helper"],
        lambda _m: None,
        read_ticket=lambda i: ticket,
        refinement=refined,
    )

    assert canaries(canary) == []
    (got,) = read(project, "helper", INBOX)
    # The Owner's text verbatim, then rite's one unquoted separator, then the
    # agreed definition of done rite checked (TR5), both quoted.
    assert got.text.split("\n", 1)[1].startswith(
        routing._quoted(ticket.description)
        + "\n"
        + routing.RECORD_FOLLOWS
        + "\n> Agreed definition of done for RT-7"
    )


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("verb", ["reply", "ask"])
def test_a_reply_or_question_as_taught_arrives_verbatim_and_runs_nothing(
    project, tmp_path_factory, shell, verb
):
    canary = tmp_path_factory.mktemp("canary") / "ran"
    text = hostile(canary).replace("?", "")
    command, _placeholder, end = _taught(how_to_reply(project, "lead"), verb).split(
        "\n"
    )
    _run(shell, "\n".join([command, text, end]), project, "lead")

    assert canaries(canary) == []
    (got,) = read(project, "lead", OUTBOX)
    assert got.text == text


@pytest.mark.parametrize("shell", SHELLS)
def test_control_the_old_double_quoted_form_runs_the_canary(
    project, tmp_path_factory, shell
):
    """Without this, a zero above could mean the test cannot see a command
    run. The same text in double quotes, as v0.6.0 taught it."""
    canary = tmp_path_factory.mktemp("canary") / "ran"
    text = f"see `touch {canary}-backtick`"
    rite = shutil.which("rite", path=str(Path(os.sys.prefix) / "bin"))
    env = {**os.environ, MANAGER_ENV: "lead"}
    subprocess.run(
        [shell, "-c", f'{rite} reply --manager lead "{text}"'],
        cwd=project,
        env=env,
        capture_output=True,
    )
    assert canaries(canary) == [Path(f"{canary}-backtick")]


@pytest.mark.parametrize(
    "args",
    [
        ["reply", "--manager", "lead", "done"],
        ["ask", "--manager", "lead", "which one"],
        ["route", "--ticket", "RT-7", "helper", "run the suite"],
    ],
    ids=["reply", "ask", "route"],
)
def test_text_on_the_command_line_is_refused_and_nothing_is_sent(
    project, monkeypatch, args
):
    monkeypatch.setenv(MANAGER_ENV, "lead")
    got = CliRunner().invoke(cli, args)
    assert got.exit_code == 1
    assert "reads its text from stdin, never the command line" in got.output
    assert "<<'RITE_TEXT_" in got.output
    assert not list((project / ".rite").rglob("*.json"))


def test_every_heredoc_rite_teaches_ends_on_an_unguessable_line(project):
    """A fixed delimiter is one a ticket can contain on a line of its own,
    and everything after it would be shell."""
    roles, _ = _manager_roles(project)
    said = [how_to_reply(project, "lead"), routing.briefing("lead", "lead", roles)]
    ends = [
        line
        for text in said
        for line in text.splitlines()
        if line.startswith("RITE_TEXT_")
    ]
    assert len(ends) >= 3
    assert len(set(ends)) == len(ends)
    assert all(len(end) == len("RITE_TEXT_") + 12 for end in ends)


def test_no_instruction_teaches_text_in_double_quotes(project):
    roles, _ = _manager_roles(project)
    said = "\n".join(
        [
            how_to_reply(project, "lead"),
            routing.briefing("lead", "lead", roles),
            routing.briefing("helper", "lead", roles),
        ]
    )
    for verb in ("reply", "ask", "route"):
        assert f'{verb} --manager lead "' not in said
        assert f'{verb} "' not in said
    assert stdin_text.RULE in said


@pytest.mark.parametrize(
    "command, substitutes",
    [
        ('rite reply --manager lead "don\'t run `rite doctor`"', True),
        ('rite reply "a $(env) b"', True),
        ('echo "a \\" `x`"', True),
        ("echo 'a `b` $(c)'", False),
        ("rite reply - <<'RITE_TEXT_ab'\n`x` $(y)\nRITE_TEXT_ab", False),
        ("git status", False),
        ('echo "\\`not one\\`"', False),
    ],
)
def test_w9_a_refused_substitution_is_named_as_one(command, substitutes):
    assert _substitutes(command) is substitutes


def test_w9_the_refusal_says_substitution_not_a_broken_settings_file(
    tmp_path, monkeypatch
):
    from rite_ai.managers import supervise

    command = 'rite reply --manager lead "see `rite doctor`"'
    monkeypatch.setattr(supervise, "refused_commands", lambda *a, **k: [command])
    said: list[str] = []
    supervise._say_refusals(tmp_path, 0.0, said.append, engine="claude", manager="")
    (line,) = said
    assert "backticks or $( )" in line
    assert "quoted heredoc" in line
    assert "did not apply" not in line
