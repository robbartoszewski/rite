"""A Manager's text travels as a file it wrote, not a heredoc (SCRUM-69).

🔴 **What happened** (the a8 dogfood, 2026-10-03 13:14). Partway through a
session `rite reply` began failing with "can't create temp file for here
document: operation not permitted", and the Manager's instructions taught no
other way to reach the person, so it went silent. Measured here: zsh writes
every here-document to a file under `TMPPREFIX` (default `/tmp/zsh`), and SB8
stopped granting `/tmp` to a Manager, so inside the real Manager profile the
heredoc fails with exactly those words. SCRUM-45 is the other half: a doubled
end line makes the engine refuse the whole call before rite runs.

**So the Manager writes its text with its own Write tool into its own drafts
directory, and runs `--from-file <draft>`.** No shell creates anything, nothing
in the file is expanded, and there is no end line. These pin:
- the boundary condition itself, and that the file form delivers under it;
- that only a draft of THIS Manager's is read, because it is sent in its name;
- deliver-exactly-once: a sent draft is consumed, a failed send keeps it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import MANAGER_ENV, stdin_text
from rite_ai.managers.mailbox import MAIL_DIR_ENV, OUTBOX, how_to_reply, read

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
    "      preset: lead\n    - name: helper\n      engine: claude\n"
    "      preset: executor\n"
)

HEREDOC = "cat <<'E'\nhello\nE\n"


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
    monkeypatch.setenv(MANAGER_ENV, "lead")
    return tmp_path


def _draft(root: Path, text: str, manager: str = "lead", name="msg.md") -> Path:
    path = stdin_text.drafts_dir(root, manager) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _reply(path) -> object:
    return CliRunner().invoke(
        cli, ["reply", "--manager", "lead", "--from-file", str(path)]
    )


# --- the boundary condition, measured ------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
class TestInsideTheRealManagerProfile:
    """The dogfood's words, reproduced in the profile `rite start` writes."""

    def _profile(self, root: Path) -> tuple[Path, Path]:
        from rite_ai.managers.enclosure import compose, engine_tmp

        profile = root / "p.sb"
        profile.write_text(compose(root, "lead"))
        tmp = engine_tmp(root, "lead")
        tmp.mkdir(parents=True, exist_ok=True)
        return profile, tmp

    def _heredoc_inside(self, root: Path, env: dict) -> subprocess.CompletedProcess:
        profile, _ = self._profile(root)
        script = root / "hd.sh"
        script.write_text(HEREDOC)
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), "/bin/zsh", str(script)],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    def test_control_a_heredoc_fails_with_only_tmpdir_inside(self, project):
        _, tmp = self._profile(project)
        env = {"TMPDIR": str(tmp)}
        env.pop("TMPPREFIX", None)
        done = self._heredoc_inside(
            project, {**env, "TMPPREFIX": "/tmp/zsh"}
        )  # zsh's own default
        assert done.returncode != 0
        assert "can't create temp file for here document" in done.stderr
        assert "operation not permitted" in done.stderr

    def test_the_engine_temp_environment_lets_it_run(self, project):
        from rite_ai.managers.boundaries import temp_environment

        _, tmp = self._profile(project)
        done = self._heredoc_inside(project, temp_environment(tmp))
        assert done.returncode == 0, done.stderr
        assert done.stdout == "hello\n"


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_where_no_heredoc_can_be_made_the_file_form_still_delivers(project, tmp_path):
    """Portable form of the boundary condition: zsh cannot create its temp
    file. The heredoc the old instructions taught fails; the file form they
    teach now delivers, through the same shell."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o555)
    env = {**os.environ, MANAGER_ENV: "lead", "TMPPREFIX": str(blocked / "zsh")}
    env.pop("RITE_PROJECT_ROOT", None)
    rite = shutil.which("rite", path=str(Path(sys.prefix) / "bin"))

    old = subprocess.run(
        ["zsh", "-c", f"{rite} reply --manager lead - {HEREDOC.split(' ', 1)[1]}"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert old.returncode != 0 and "here document" in old.stderr, old.stderr
    assert read(project, "lead", OUTBOX) == []

    line = next(
        ln.strip().removeprefix("2. Run: ")
        for ln in how_to_reply(project, "lead").splitlines()
        if ln.strip().startswith("2. Run: ") and " reply " in ln
    )
    path = Path(
        next(
            ln
            for ln in how_to_reply(project, "lead").splitlines()
            if ln.startswith("1. Write your message")
        ).split("`")[1]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("KAN-7 merged; CI green")
    new = subprocess.run(
        ["zsh", "-c", line], cwd=project, env=env, capture_output=True, text=True
    )
    assert new.returncode == 0, new.stderr
    (got,) = read(project, "lead", OUTBOX)
    assert got.text == "KAN-7 merged; CI green"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_drafts_path_with_a_space_survives_the_shell(project, tmp_path, monkeypatch):
    """On macOS a Manager's directory is under `Application Support`. The
    taught command quotes the path, so a real shell hands it over whole."""
    spaced = tmp_path / "Application Support"
    monkeypatch.setenv(MAIL_DIR_ENV, str(spaced))
    told = how_to_reply(project, "lead")
    line = next(
        ln.strip().removeprefix("2. Run: ")
        for ln in told.splitlines()
        if ln.strip().startswith("2. Run: ") and " reply " in ln
    )
    path = stdin_text.drafts_dir(project, "lead") / "reply.md"
    assert " " in str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("done")
    env = {**os.environ, MANAGER_ENV: "lead", MAIL_DIR_ENV: str(spaced)}
    env.pop("RITE_PROJECT_ROOT", None)
    rite = shutil.which("rite", path=str(Path(sys.prefix) / "bin"))
    done = subprocess.run(
        ["bash", "-c", line.replace(line.split(" ", 1)[0], rite, 1)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert [m.text for m in read(project, "lead", OUTBOX)] == ["done"]


# --- only this Manager's own draft is read ------------------------------------


class TestOnlyThisManagersDraftIsRead:
    """A draft goes out in the Manager's name, so it must be one only that
    Manager could have written: a regular file directly in its own drafts
    directory. Each refusal reads nothing and sends nothing."""

    def _refused(self, project, given, why):
        got = _reply(given)
        assert got.exit_code == 1, got.output
        assert why in got.output, got.output
        assert "Nothing was sent" in got.output
        assert read(project, "lead", OUTBOX) == []

    def test_a_file_elsewhere(self, project, tmp_path):
        other = tmp_path / "secret.txt"
        other.write_text("the token")
        _draft(project, "x")
        self._refused(project, other, "is not in your drafts directory")

    def test_a_climb_out_with_dotdot(self, project, tmp_path):
        own = _draft(project, "x").parent
        (own.parent / "escape.md").write_text("the token")
        self._refused(project, own / ".." / "escape.md", "not in your drafts")

    def test_a_subdirectory(self, project):
        own = _draft(project, "x").parent
        (own / "sub").mkdir()
        (own / "sub" / "m.md").write_text("nested")
        self._refused(project, own / "sub" / "m.md", "not in your drafts")

    def test_a_link_to_a_file_outside(self, project, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("the token")
        own = _draft(project, "x").parent
        (own / "link.md").symlink_to(outside)
        self._refused(project, own / "link.md", "it is a link")

    def test_a_link_to_another_managers_draft(self, project):
        peer = _draft(project, "helper's words", manager="helper")
        own = _draft(project, "x").parent
        (own / "borrowed.md").symlink_to(peer)
        self._refused(project, own / "borrowed.md", "it is a link")

    def test_another_managers_draft_named_directly(self, project):
        peer = _draft(project, "helper's words", manager="helper")
        _draft(project, "x")
        self._refused(project, peer, "not in your drafts")

    def test_a_directory(self, project):
        own = _draft(project, "x").parent
        (own / "dir.md").mkdir()
        self._refused(project, own / "dir.md", "not a regular file")

    def test_a_fifo(self, project):
        own = _draft(project, "x").parent
        os.mkfifo(own / "pipe.md")
        self._refused(project, own / "pipe.md", "not a regular file")

    def test_one_too_large(self, project):
        big = _draft(project, "a" * (stdin_text.DRAFT_LIMIT + 1))
        self._refused(project, big, "a message is at most")

    def test_control_a_draft_of_its_own_is_read_verbatim(self, project):
        text = "see `rm -rf ~` and $(env) — as text"
        got = _reply(_draft(project, text + "\n"))
        assert got.exit_code == 0, got.output
        assert [m.text for m in read(project, "lead", OUTBOX)] == [text]

    def test_a_bare_name_is_one_in_its_drafts_directory(self, project):
        _draft(project, "short", name="reply.md")
        got = _reply("reply.md")
        assert got.exit_code == 0, got.output
        assert [m.text for m in read(project, "lead", OUTBOX)] == ["short"]


# --- deliver exactly once (SCRUM-45) ------------------------------------------


class TestDeliveredExactlyOnce:
    def test_a_sent_draft_is_consumed_and_the_same_command_sends_nothing_more(
        self, project
    ):
        path = _draft(project, "KAN-7 merged")
        assert _reply(path).exit_code == 0
        assert not path.exists()
        again = _reply(path)
        assert again.exit_code == 1
        assert "removed once queued" in again.output
        assert len(read(project, "lead", OUTBOX)) == 1

    def test_a_send_that_failed_keeps_its_draft_for_the_retry(
        self, project, monkeypatch
    ):
        import rite_ai.managers.mailbox as mailbox

        path = _draft(project, "KAN-7 merged")

        def disk_full(*a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(mailbox, "send", disk_full)
        got = _reply(path)
        assert got.exit_code == 1 and "Nothing was sent" in got.output
        assert path.exists(), "the retry would have nothing to send"

    def test_a_question_refused_as_a_reply_keeps_its_draft_for_ask(self, project):
        path = _draft(project, "which schema should KAN-7 use?")
        got = _reply(path)
        assert got.exit_code == 1
        assert path.exists()
        assert f"--from-file {path}" in got.output or "--from-file '" in got.output
        asked = CliRunner().invoke(
            cli, ["ask", "--manager", "lead", "--from-file", str(path)]
        )
        assert asked.exit_code == 0, asked.output
        assert not path.exists()


# --- one source, and the others still refused ---------------------------------


class TestOneSource:
    def test_both_a_file_and_stdin_is_refused(self, project):
        path = _draft(project, "x")
        got = CliRunner().invoke(
            cli,
            ["reply", "--manager", "lead", "-", "--from-file", str(path)],
            input="y",
        )
        assert got.exit_code == 1 and "not both" in got.output
        assert path.exists() and read(project, "lead", OUTBOX) == []

    def test_neither_is_refused_and_teaches_the_file_form(self, project):
        got = CliRunner().invoke(cli, ["reply", "--manager", "lead"])
        assert got.exit_code == 1
        assert "--from-file " in got.output and "Write tool" in got.output

    def test_stdin_is_still_accepted(self, project):
        got = CliRunner().invoke(
            cli, ["reply", "--manager", "lead", "-"], input="via stdin"
        )
        assert got.exit_code == 0, got.output
        assert [m.text for m in read(project, "lead", OUTBOX)] == ["via stdin"]

    def test_text_on_the_command_line_is_still_refused(self, project):
        got = CliRunner().invoke(cli, ["reply", "--manager", "lead", "done"])
        assert got.exit_code == 1
        assert "never the command line" in got.output
        assert read(project, "lead", OUTBOX) == []


# --- the other commands take the same file -------------------------------------


def test_a_deferral_takes_its_meanwhile_from_the_files_first_line(project):
    from rite_ai.managers import checkins
    from tests.test_deferring_a_question import CLOSED

    with (project / ".rite" / "config.yaml").open("a") as config:
        config.write(CLOSED)
    path = _draft(project, "tickets 14 and 15\nrename --out to --output")
    got = CliRunner().invoke(
        cli,
        [
            "ask",
            "--manager",
            "lead",
            "--defer",
            "--while",
            "-",
            "--from-file",
            str(path),
        ],
    )
    assert got.exit_code == 0, got.output
    (queued,) = checkins._queued(project, "lead")
    assert queued.meanwhile == "tickets 14 and 15"
    assert not path.exists()


def test_a_route_takes_the_file(project):
    import json

    from rite_ai.managers import routing

    path = _draft(project, "run the suite on fix-12 and report")
    got = CliRunner().invoke(
        cli, ["route", "--ticket", "RT-12", "helper", "--from-file", str(path)]
    )
    assert got.exit_code == 0, got.output
    (queued,) = routing._routes_dir(project, "lead").glob("*.json")
    assert (
        json.loads(queued.read_text())["text"] == "run the suite on fix-12 and report"
    )
    assert not path.exists()


def test_a_refinement_round_takes_the_file(project):
    from rite_ai.refinement import protocol

    round_ = "Questions:\n1. Which timeout: a file's, or the HTTP call's?"
    path = _draft(project, round_)
    got = CliRunner().invoke(cli, ["refine", "ask", "KAN-7", "--from-file", str(path)])
    assert got.exit_code == 0, got.output
    assert not path.exists()
    import json

    (queued,) = protocol._asks_dir(project, "lead").glob("*.json")
    assert json.loads(queued.read_text()) == {"ticket": "KAN-7", "text": round_}
