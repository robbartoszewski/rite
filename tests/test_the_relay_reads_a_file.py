"""A Manager's text travels as a file it wrote, not a heredoc (SCRUM-69).

🔴 **What happened** (the a8 dogfood, 2026-10-03 13:14). Partway through a
session `rite reply` began failing with "can't create temp file for here
document: operation not permitted", and the Manager's instructions taught no
other way to reach the person, so it went silent. Measured here: zsh writes
every here-document to a file under `TMPPREFIX` (default `/tmp/zsh`), and SB8
stopped granting `/tmp` to a Manager, so inside the real Manager profile the
heredoc fails with exactly those words. SCRUM-45 is the other half: a doubled
end line makes the engine refuse the whole call before rite runs.

**So the Manager writes its text with its own file-writing tool (not the
shell) into its own drafts directory, and runs `--from-file <draft>`.** No
shell creates anything, nothing in the file is expanded, and there is no end
line. These pin:
- the boundary condition itself, and that the file form delivers under it;
- that only a draft of THIS Manager's is read, because it is sent in its name;
- deliver-exactly-once: a sent draft is consumed, a failed send keeps it,
  and two sends of one draft at the same moment deliver it once.
"""

from __future__ import annotations

import fcntl
import os
import re
import shlex
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

    def _profile(self, root: Path, manager: str = "lead") -> tuple[Path, Path]:
        """The profile `rite start` composes, and the directories its
        `write_profile` creates outside the boundary."""
        from rite_ai.managers import manager_dir
        from rite_ai.managers.enclosure import _own_subdirs, compose, engine_tmp
        from rite_ai.managers.mailbox import INBOX, mailbox_dir

        for box in (OUTBOX, INBOX):
            mailbox_dir(root, manager, box).mkdir(parents=True, exist_ok=True)
        manager_dir(root, manager).mkdir(parents=True, exist_ok=True)
        _own_subdirs(root, manager)
        profile = root / f"{manager}.sb"
        profile.write_text(compose(root, manager))
        tmp = engine_tmp(root, manager)
        tmp.mkdir(parents=True, exist_ok=True)
        return profile, tmp

    def _inside(
        self, root: Path, env: dict, script: str, manager: str = "lead"
    ) -> subprocess.CompletedProcess:
        profile, _ = self._profile(root, manager)
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), "/bin/zsh", "-c", script],
            capture_output=True,
            text=True,
            cwd=root,
            env={**os.environ, **env},
        )

    def test_control_a_heredoc_fails_with_only_tmpdir_inside(self, project):
        _, tmp = self._profile(project)
        # zsh's own default prefix: what a Manager had before SCRUM-69.
        done = self._inside(
            project, {"TMPDIR": str(tmp), "TMPPREFIX": "/tmp/zsh"}, HEREDOC
        )
        assert done.returncode != 0
        assert "can't create temp file for here document" in done.stderr
        assert "operation not permitted" in done.stderr

    def test_the_engine_temp_environment_lets_it_run(self, project):
        from rite_ai.managers.boundaries import heredoc_dir, temp_environment

        _, tmp = self._profile(project)
        env = temp_environment(tmp, heredoc_dir(project, "lead"))
        assert env["TMPPREFIX"].startswith(str(heredoc_dir(project, "lead")))
        done = self._inside(project, env, HEREDOC)
        assert done.returncode == 0, done.stderr
        assert done.stdout == "hello\n"

    def test_the_taught_file_form_delivers_inside_it(self, project):
        """The acceptance test the ticket names, in the real profile: with
        no heredoc possible at all (`/tmp/zsh`), the taught line delivers
        and consumes its draft."""
        _, tmp = self._profile(project)
        told = how_to_reply(project, "lead")
        line = next(
            ln.strip().removeprefix("2. Run: ")
            for ln in told.splitlines()
            if ln.strip().startswith("2. Run: ") and " reply " in ln
        )
        path = _draft(
            project, "KAN-7 merged; see `rite doctor` and $(id)", name="reply.md"
        )
        env = {
            MANAGER_ENV: "lead",
            "TMPDIR": str(tmp),
            "TMPPREFIX": "/tmp/zsh",
        }
        done = self._inside(project, env, line)
        assert done.returncode == 0, done.stderr
        assert [m.text for m in read(project, "lead", OUTBOX)] == [
            "KAN-7 merged; see `rite doctor` and $(id)"
        ]
        assert not path.exists()

    def test_the_manager_cannot_swap_its_own_directory_or_outbox(self, project):
        """🔴 SCRUM-69 re-review, measured: a subpath grant covers its own
        path, so a Manager could rename its directory away and link
        another's in its place. The profile now refuses renaming the
        directory and the outbox themselves; inside them nothing changes."""
        from rite_ai.managers import manager_dir
        from rite_ai.managers.mailbox import mailbox_dir

        self._profile(project)
        own = manager_dir(project, "lead")
        out = mailbox_dir(project, "lead", OUTBOX)
        q = shlex.quote
        done = self._inside(
            project,
            {},
            f"mv {q(str(own))} {q(str(project / 'stolen'))}; echo state=$?; "
            f"mv {q(str(out))} {q(str(project / 'stolen-out'))}; echo out=$?; "
            f"mkdir {q(str(own / 'a'))} && mv {q(str(own / 'a'))} "
            f"{q(str(own / 'b'))}; echo inside=$?",
        )
        assert "state=0" not in done.stdout, done.stdout
        assert "out=0" not in done.stdout, done.stdout
        assert own.is_dir() and not own.is_symlink() and out.is_dir()
        assert "inside=0" in done.stdout, done.stderr  # control
        assert (own / "b").is_dir()

    def test_another_manager_cannot_reach_its_heredocs(self, project):
        """🔴 Why `TMPPREFIX` is not `engine_tmp` (SCRUM-69 review): a sibling
        must not read or replace a Manager's heredoc. Measured from inside
        `helper`'s own profile, with a control that it can write its own."""
        from rite_ai.managers.boundaries import heredoc_dir

        self._profile(project, "lead")
        theirs = heredoc_dir(project, "lead")
        (theirs / "zsh-sample").write_text("lead's heredoc")
        mine = heredoc_dir(project, "helper")
        done = self._inside(
            project,
            {},
            f"cat {shlex.quote(str(theirs / 'zsh-sample'))}; "
            f"echo x > {shlex.quote(str(theirs / 'planted'))}; "
            f"echo ok > {shlex.quote(str(mine / 'own'))}",
            manager="helper",
        )
        assert "lead's heredoc" not in done.stdout
        assert not (theirs / "planted").exists()
        assert (mine / "own").read_text() == "ok\n", done.stderr


def _taught_reply_line(project: Path) -> tuple[str, Path]:
    told = how_to_reply(project, "lead").splitlines()
    line = next(
        ln.strip().removeprefix("2. Run: ")
        for ln in told
        if ln.strip().startswith("2. Run: ") and " reply " in ln
    )
    path = Path(next(ln for ln in told if ln.startswith("1. Write ")).split("`")[1])
    return line, path


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_where_no_heredoc_can_be_made_the_file_form_still_delivers(
    project, tmp_path, shell
):
    """Portable form of the boundary condition, on every platform and as any
    user: the shell's temp directory does not EXIST (a mode bit would not
    stop root). The heredoc the old instructions taught fails under zsh; the
    file form they teach now delivers, through the same shell."""
    if shutil.which(shell) is None:
        pytest.skip(f"no {shell} here")
    nowhere = tmp_path / "no-such-dir"
    env = {
        **os.environ,
        MANAGER_ENV: "lead",
        "TMPPREFIX": str(nowhere / "zsh"),
        "TMPDIR": str(nowhere),
    }
    env.pop("RITE_PROJECT_ROOT", None)
    rite = shutil.which("rite", path=str(Path(sys.prefix) / "bin"))
    if shell == "zsh":
        old = subprocess.run(
            ["zsh", "-c", f"{rite} reply --manager lead - {HEREDOC.split(' ', 1)[1]}"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
        )
        assert old.returncode != 0 and "here document" in old.stderr, old.stderr
        assert read(project, "lead", OUTBOX) == []

    line, path = _taught_reply_line(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("KAN-7 merged; CI green")
    new = subprocess.run(
        [shell, "-c", line], cwd=project, env=env, capture_output=True, text=True
    )
    assert new.returncode == 0, new.stderr
    (got,) = read(project, "lead", OUTBOX)
    assert got.text == "KAN-7 merged; CI green"
    assert not path.exists()


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

    def test_a_relative_name_is_from_the_current_directory(self, project):
        """🔴 SCRUM-69 review: `reply.md` used to mean the one in the drafts
        directory, wherever the Manager was. A Manager that wrote
        `./reply.md` in the project and named it would have sent a STALE
        draft of the same name. A path means what it means to every other
        command: from the current directory."""
        _draft(project, "stale", name="reply.md")
        (project / "reply.md").write_text("fresh, in the project")
        self._refused(project, "reply.md", "not in your drafts directory")

    def test_control_a_relative_name_from_inside_the_drafts_directory(
        self, project, monkeypatch
    ):
        path = _draft(project, "short", name="reply.md")
        monkeypatch.setenv("RITE_PROJECT_ROOT", str(project))
        monkeypatch.chdir(path.parent)
        got = _reply("reply.md")
        assert got.exit_code == 0, got.output
        assert [m.text for m in read(project, "lead", OUTBOX)] == ["short"]

    def test_the_drafts_directory_itself_a_link(self, project, tmp_path, monkeypatch):
        """🔴 SCRUM-69 review. The Manager can write its own directory, so it
        can make `drafts` a link to anywhere. Run by a person at the host —
        unconfined — rite resolved the link and would have sent, then
        removed, a file the Manager can never touch."""
        from rite_ai.managers import manager_dir

        target = tmp_path / "dot-ssh"
        target.mkdir()
        (target / "id_ed25519").write_text("PRIVATE KEY")
        manager_dir(project, "lead").mkdir(parents=True, exist_ok=True)
        stdin_text.drafts_dir(project, "lead").symlink_to(target)
        monkeypatch.delenv(MANAGER_ENV)  # a person at the host
        given = stdin_text.drafts_dir(project, "lead") / "id_ed25519"
        self._refused(project, given, "it is a link")
        assert (target / "id_ed25519").read_text() == "PRIVATE KEY"

    def test_a_second_hard_link(self, project, tmp_path):
        """Seatbelt checks paths, not files: a hard link in the drafts
        directory could name a file the Manager cannot open by its own path.
        A draft is a file the Manager wrote, so it has one link."""
        own = _draft(project, "x").parent
        elsewhere = tmp_path / "elsewhere.txt"
        elsewhere.write_text("not the Manager's words")
        os.link(elsewhere, own / "linked.md")
        self._refused(project, own / "linked.md", "hard links")
        assert elsewhere.exists()


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
        assert f"--from-file {shlex.quote(str(path))}" in got.output
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
        assert "--from-file " in got.output
        assert "your file-writing tool, not the shell" in got.output

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


# --- two sends of one draft at the same moment deliver it once -------------------


class TestExactlyOnceWithNoWindow:
    """🔴 SCRUM-69 review: two `rite reply --from-file reply.md` started
    together both read the file before either removed it. Closed by a lock
    held from reading to removal, and a check, under the lock, that the name
    still names the file that was opened."""

    def test_while_another_rite_holds_the_draft_this_one_sends_nothing(self, project):
        path = _draft(project, "KAN-7 merged")
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_RDONLY); "
                "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); "
                "sys.stdin.read()",
                str(path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            got = _reply(path)
            assert got.exit_code == 1
            assert "being sent by another rite" in got.output
            assert read(project, "lead", OUTBOX) == []
            assert path.exists()
        finally:
            holder.stdin.close()
            holder.wait(timeout=30)
        # Released: now it goes, once.
        assert _reply(path).exit_code == 0
        assert len(read(project, "lead", OUTBOX)) == 1

    def test_one_removed_while_this_rite_waited_is_not_sent_again(
        self, project, monkeypatch
    ):
        """The other order: this rite opened the draft, and before it took
        the lock the first rite sent it and removed it. Without the check
        under the lock it would send the removed file's text a second time."""
        path = _draft(project, "KAN-7 merged")
        first_text, first = stdin_text.read_draft(project, "lead", str(path))
        real = fcntl.flock
        calls = []

        def first_finishes_in_between(fd, how):
            if not calls:
                calls.append(fd)
                assert first.consume() == ""  # sent, removed, released
            return real(fd, how)

        monkeypatch.setattr(fcntl, "flock", first_finishes_in_between)
        with pytest.raises(stdin_text.DraftRefused, match="sent and removed"):
            stdin_text.read_draft(project, "lead", str(path))
        assert calls and first_text == "KAN-7 merged"

    def test_a_new_draft_of_the_same_name_is_not_removed_by_the_old_send(self, project):
        """Only the file that was READ is removed: a Manager that wrote a
        new `reply.md` meanwhile keeps it, and is told it was not sent."""
        path = _draft(project, "old")
        _, draft = stdin_text.read_draft(project, "lead", str(path))
        path.unlink()
        path.write_text("new")
        said = draft.consume()
        assert "NOT sent" in said
        (kept,) = [p for p in path.parent.iterdir() if p.read_text() == "new"]
        assert str(kept) in said

    def test_control_the_draft_that_was_read_is_removed(self, project):
        path = _draft(project, "old")
        _, draft = stdin_text.read_draft(project, "lead", str(path))
        assert draft.consume() == ""
        assert list(path.parent.iterdir()) == []


# --- a refusal after reading keeps the draft where it was written ----------------


class TestARefusalKeepsTheDraft:
    def test_an_empty_reply(self, project):
        path = _draft(project, "   \n")
        assert _reply(path).exit_code == 1
        assert path.exists()

    def test_a_route_with_no_ticket_on_the_board(self, project):
        path = _draft(project, "run the suite")
        got = CliRunner().invoke(
            cli, ["route", "--ticket", "", "helper", "--from-file", str(path)]
        )
        assert got.exit_code == 1, got.output
        assert path.exists()

    def test_a_deferral_with_no_meanwhile(self, project):
        path = _draft(project, "which schema?")
        got = CliRunner().invoke(
            cli, ["ask", "--manager", "lead", "--defer", "--from-file", str(path)]
        )
        assert got.exit_code == 1 and "refusing to defer" in got.output
        assert path.exists()

    def test_a_round_not_in_the_shape_rite_sends(self, project):
        path = _draft(project, "just a chat, no numbered questions")
        got = CliRunner().invoke(
            cli, ["refine", "ask", "KAN-7", "--from-file", str(path)]
        )
        assert got.exit_code == 1 and "not in the shape" in got.output
        assert path.exists()

    def test_and_each_refusal_released_it_so_the_retry_goes(self, project):
        path = _draft(project, "   \n")
        assert _reply(path).exit_code == 1
        path.write_text("now with text")
        assert _reply(path).exit_code == 0, "the refused run still held its lock"


# --- a person at the host is told what a person can do ---------------------------


def _undelimited(text: str) -> str:
    return re.sub(r"RITE_TEXT_[0-9a-f]+", "RITE_TEXT_X", text)


# What 195d79d (before SCRUM-69) printed, pinned as text so a change to
# `stdin_text.refusal` cannot pass by changing both sides.
BEFORE_69_REPLY = (
    "refusing: this command reads its text from stdin, never the command line. "
    "In double quotes the shell runs anything in backticks or $( ) BEFORE rite "
    "sees the text — and if yours had any, it already ran. Send it through a "
    "quoted heredoc, where nothing is expanded:\nrite reply --manager lead - "
    "<<'RITE_TEXT_X'\n<your message>\nRITE_TEXT_X\n"
)
BEFORE_69_WHILE = (
    "refusing: --while takes `-`, and what you will do meanwhile is the first "
    "line of stdin. In double quotes on the command line the shell runs anything "
    "in backticks or $( ) BEFORE rite sees it — and if yours had any, it "
    "already ran. Send both through one quoted heredoc, where nothing is "
    "expanded:\nrite ask --manager lead --defer --while - - <<'RITE_TEXT_X'\n"
    "<what you will do meanwhile, on this one line>\n<your question>\n"
    "RITE_TEXT_X\n"
)


def test_a_person_at_the_host_gets_the_refusal_they_always_got(project, monkeypatch):
    """SCRUM-69 review: a person typing `rite reply --manager lead "hi"` was
    told to write into the Manager's drafts directory with "your Write
    tool". Without `RITE_MANAGER` the refusal is the heredoc one, word for
    word as before: the command-line refusal and the `--while` one."""
    monkeypatch.delenv(MANAGER_ENV)
    got = CliRunner().invoke(cli, ["reply", "--manager", "lead", "hi"])
    assert got.exit_code == 1
    assert _undelimited(got.output) == BEFORE_69_REPLY
    got = CliRunner().invoke(
        cli, ["ask", "--manager", "lead", "--defer", "--while", "x", "-"]
    )
    assert got.exit_code == 1
    assert _undelimited(got.output) == BEFORE_69_WHILE


def test_control_the_manager_itself_is_taught_the_file(project):
    got = CliRunner().invoke(cli, ["reply", "--manager", "lead", "hi"])
    assert got.exit_code == 1
    assert "--from-file " in got.output and "<<'" not in got.output


def test_command_line_text_beside_a_file_says_what_already_ran(project):
    """The worse problem first: with text on the command line whatever it
    held has run, whatever else was given."""
    path = _draft(project, "x")
    got = CliRunner().invoke(
        cli, ["reply", "--manager", "lead", "see $(id)", "--from-file", str(path)]
    )
    assert got.exit_code == 1
    assert "it already ran" in got.output
    assert path.exists() and read(project, "lead", OUTBOX) == []


def test_a_ticket_is_quoted_in_the_command_a_refusal_teaches(project):
    """The ticket goes into the taught `2. Run:` line, and a Manager copies
    that line: it is quoted, so it stays one word."""
    got = CliRunner().invoke(cli, ["route", "--ticket", "RT-1;id", "helper"])
    assert got.exit_code == 1
    assert "--ticket 'RT-1;id' helper" in got.output


def test_each_manager_gets_its_own_heredoc_and_drafts_directories(project):
    from rite_ai.managers.boundaries import boundary_for, heredoc_dir

    try:
        boundary = boundary_for()
    except Exception:
        pytest.skip("no boundary on this machine")
    boundary.write_profile(project, "lead", project / "home")
    for path in (heredoc_dir(project, "lead"), stdin_text.drafts_dir(project, "lead")):
        assert path.is_dir()
        assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)


# --- a Manager's directory that is a link is never followed ----------------------


def _swap_for_a_link(project: Path, to: Path) -> Path:
    from rite_ai.managers import manager_dir

    own = manager_dir(project, "lead")
    own.mkdir(parents=True, exist_ok=True)
    own.rename(own.with_name("moved-away"))
    own.symlink_to(to, target_is_directory=True)
    return own


def test_a_linked_manager_directory_is_not_read_through(project, monkeypatch):
    """The swap the re-review measured, done before this fix (or on a
    profile without the deny): rite run by a person at the host sent
    helper's draft as lead's, and removed it."""
    from rite_ai.managers import manager_dir

    peer = _draft(project, "helper's words", manager="helper", name="reply.md")
    own = _swap_for_a_link(project, manager_dir(project, "helper"))
    monkeypatch.delenv(MANAGER_ENV)
    got = _reply(own / "drafts" / "reply.md")
    assert got.exit_code == 1 and "it is a link" in got.output, got.output
    assert peer.read_text() == "helper's words"
    assert read(project, "lead", OUTBOX) == []


def test_a_linked_manager_directory_is_refused_at_start(project, tmp_path):
    """And at the next start, where `.resolve()` would have granted the
    link's target read-write (`$HOME`, say)."""
    from rite_ai.managers.boundaries import boundary_for
    from rite_ai.managers.enclosure import LinkedManagerPath, compose

    target = tmp_path / "home-ish"
    target.mkdir()
    _swap_for_a_link(project, target)
    try:
        boundary = boundary_for()
    except Exception:
        pytest.skip("no boundary on this machine")
    with pytest.raises(LinkedManagerPath, match="is a link"):
        boundary.write_profile(project, "lead", project / "home")
    assert not (target / "drafts").exists(), "created through the link"
    if sys.platform == "darwin":
        assert str(target.resolve()) not in compose(project, "lead")


def test_the_launch_puts_zsh_heredocs_in_the_managers_own_directory(
    project, monkeypatch
):
    """🔴 SCRUM-69 re-review: the wiring itself, not `temp_environment` with
    the right argument passed by a test. Reverting the starter to
    `temp_environment(engine_tmp)` left every test green."""
    import rite_ai.managers.supervise as sup
    from rite_ai.managers.boundaries import boundary_for, heredoc_dir
    from rite_ai.managers.session import StartResult

    try:
        boundary = boundary_for()
    except Exception:
        pytest.skip("no boundary on this machine")
    seen = {}

    def fake_start(root, manager, **kwargs):
        seen.update(kwargs)
        return StartResult(False, "not starting anything in a test")

    monkeypatch.setattr(sup, "start_session", fake_start)
    sup._default_starter(
        project,
        "lead",
        engine="claude",
        resume_id="",
        max_sessions=None,
        window_seconds=None,
        permission="",
        prompt="go",
        agent="",
    )
    prefix = seen["pane_env"]["TMPPREFIX"]
    assert prefix.startswith(str(heredoc_dir(project, "lead")) + os.sep), prefix
    assert not prefix.startswith(str(boundary.engine_tmp(project, "lead")))


def test_a_deferred_question_is_consumed_before_it_is_asked_now(project, monkeypatch):
    """With no check-in window to wait for, a deferral is queued and then
    asked now. If asking now fails, the question is queued already: the
    draft must be gone, or the retry queues it a second time."""
    from rite_ai.managers import checkins

    def fails(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(checkins, "ask_now", fails)
    path = _draft(project, "tickets 14 and 15\nrename --out to --output?")
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
    assert got.exit_code != 0
    assert not path.exists()
    assert len(checkins._queued(project, "lead")) == 1


def test_a_linked_manager_directory_stops_the_launch_and_says_why(
    project, tmp_path, monkeypatch
):
    """`rite start` reports it as a refusal to start, not a traceback, and
    asks tmux for nothing."""
    import rite_ai.managers.supervise as sup
    from rite_ai.managers.boundaries import boundary_for

    try:
        boundary_for()
    except Exception:
        pytest.skip("no boundary on this machine")
    target = tmp_path / "home-ish"
    target.mkdir()
    _swap_for_a_link(project, target)
    monkeypatch.setattr(
        sup, "start_session", lambda *a, **k: pytest.fail("tmux was asked")
    )
    result = sup._default_starter(
        project,
        "lead",
        engine="claude",
        resume_id="",
        max_sessions=None,
        window_seconds=None,
        permission="",
        prompt="go",
        agent="",
    )
    assert not result.ok and "is a link" in result.message
