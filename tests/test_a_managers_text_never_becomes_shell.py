"""A Manager's text reaches rite from a file and is never run as shell (F14).

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

🔴 **The taught form is now a FILE (SCRUM-69).** The Manager writes its text
with its own Write tool into its drafts directory and runs the command the
instructions print, `--from-file <draft>`. These tests write the draft as that
tool would (the bytes, nothing interpreted) and run the printed command line
through a real shell, so the property is tested on the form actually taught.
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
from rite_ai.managers import MANAGER_ENV, checkins, routing, stdin_text
from rite_ai.managers.mailbox import INBOX, OUTBOX, how_to_reply, read
from rite_ai.managers.supervise import _substitutes
from rite_ai.managers.transcripts import Refusal
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


def _taught(instructions: str, verb: str, also: str = "") -> tuple[Path, str]:
    """`(draft path, command line)` the instructions teach for `verb`: the
    file step names the path, the run step the command."""
    lines = instructions.splitlines()
    for i, line in enumerate(lines):
        run = line.strip()
        if run.startswith("2. Run: ") and f" {verb} " in run and also in run:
            write = lines[i - 1]
            assert write.lstrip().startswith("1. Write "), write
            path = Path(write.split("`")[1])
            return path, run.removeprefix("2. Run: ")
    raise AssertionError(f"no file form for {verb!r} in:\n{instructions}")


def _write_draft(path: Path, text: str) -> None:
    """What the Manager's Write tool does: the bytes, nothing interpreted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
    draft, command = _taught(
        routing.briefing("lead", "lead", roles, root=project), "route"
    )
    _write_draft(draft, ticket.description)
    script = command.replace("<ID>", ticket.id).replace("<manager>", "helper")

    _run(shell, script, project, "lead")
    assert not draft.exists(), "the draft was not consumed once queued"
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
    draft, command = _taught(how_to_reply(project, "lead"), verb)
    _write_draft(draft, text)
    _run(shell, command, project, "lead")

    assert canaries(canary) == []
    (got,) = read(project, "lead", OUTBOX)
    assert got.text == text
    assert not draft.exists()


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
    assert "reads its text from a file or stdin, never the command line" in got.output
    assert "--from-file" in got.output and "<<'" not in got.output
    assert not list((project / ".rite").rglob("*.json"))


def test_no_instruction_teaches_a_heredoc_any_more(project):
    """🔴 SCRUM-69/45: a doubled end line is no longer reachable from the
    taught form, because the taught form has no end line. Every command a
    Manager is taught for its text names a draft file."""
    roles, _ = _manager_roles(project)
    said = "\n".join(
        [
            how_to_reply(project, "lead"),
            routing.briefing("lead", "lead", roles, root=project),
            routing.briefing("helper", "lead", roles, root=project),
            checkins.instructions(project, "lead"),
        ]
    )
    assert "<<'" not in said and "RITE_TEXT_" not in said
    runs = [ln for ln in said.splitlines() if ln.strip().startswith("2. Run: ")]
    assert len(runs) >= 5, runs
    assert all("--from-file " in run for run in runs), runs


def test_no_instruction_teaches_text_in_double_quotes(project):
    """⚠ The check-in instructions are in the list since SCRUM-33: they were
    not, and they were the one place that still taught `--while "<…>"`."""
    roles, _ = _manager_roles(project)
    said = "\n".join(
        [
            how_to_reply(project, "lead"),
            routing.briefing("lead", "lead", roles, root=project),
            routing.briefing("helper", "lead", roles, root=project),
            checkins.instructions(project, "lead"),
        ]
    )
    for verb in ("reply", "ask", "route"):
        assert f'{verb} --manager lead "' not in said
        assert f'{verb} "' not in said
    assert '--while "' not in said
    assert "--while -" in said, "the check-in instructions no longer teach a deferral"
    assert stdin_text.FILE_RULE in said


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
    monkeypatch.setattr(
        supervise, "refusals", lambda *a, **k: [Refusal(c) for c in [command]]
    )
    said: list[str] = []
    supervise._say_refusals(tmp_path, 0.0, said.append, engine="claude", manager="")
    (line,) = said
    assert "backticks or $( )" in line
    assert "quoted heredoc" in line
    assert "did not apply" not in line


# --- SCRUM-33: `--while` is text too ------------------------------------------


def test_while_on_the_command_line_is_refused_and_nothing_is_queued_or_sent(
    project, monkeypatch
):
    monkeypatch.setenv(MANAGER_ENV, "lead")
    got = CliRunner().invoke(
        cli,
        ["ask", "--manager", "lead", "--defer", "--while", "ticket 9", "-"],
        input="rename the flag?",
    )
    assert got.exit_code == 1
    assert "--while takes `-`" in got.output
    assert "--defer --while - --from-file " in got.output
    assert not list((project / ".rite").rglob("*.json"))
    assert read(project, "lead", OUTBOX) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_a_deferral_as_taught_arrives_verbatim_and_runs_nothing(
    project, tmp_path_factory, shell
):
    """The form the check-in instructions teach, run by a real shell with a
    hostile meanwhile AND a hostile question. Deferred into a window that is
    not open, so both are read back from the queue exactly as stored."""
    from tests.test_deferring_a_question import CLOSED

    with (project / ".rite" / "config.yaml").open("a") as config:
        config.write(CLOSED)
    canary = tmp_path_factory.mktemp("canary") / "ran"
    meanwhile = f"ticket 9, not `touch {canary}-while` nor $(touch {canary}-wd)"
    question = hostile(canary).replace("?", "")
    draft, command = _taught(checkins.instructions(project, "lead"), "ask", "--defer")
    assert "--while -" in command
    _write_draft(draft, meanwhile + "\n" + question)
    _run(shell, command, project, "lead")
    assert not draft.exists()

    assert canaries(canary) == []
    # Read as stored: `_queued` strips text for display, `defer` writes it
    # as given, and the property is what was written.
    (queued,) = checkins._queued(project, "lead")
    stored = json.loads(queued.path.read_text())
    assert stored["meanwhile"] == meanwhile
    assert stored["text"] == question
