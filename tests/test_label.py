"""Project labelling — see `rite_ai.label`.

The requirement came from using rite, not from reviewing it: four
decisions arrived with no project name on them, on a machine running more
than one project, and could not be told apart.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.label import (
    ASCII_DOT,
    DOT,
    PALETTE,
    ProjectLabel,
    colour_enabled,
    colour_for,
    decorate,
    label,
    project_name,
)


class _Stream:
    def __init__(self, tty: bool, encoding: str = "utf-8"):
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


def _project(root: Path, name: str | None) -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    if name is not None:
        (root / ".rite" / "brief.yaml").write_text(
            f"project:\n  name: {name}\n  role: manager\n"
        )
    (root / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return root


class TestColourIsDerivedFromTheName:
    def test_same_name_same_colour(self):
        assert colour_for("rite") == colour_for("rite")

    def test_different_names_generally_differ(self):
        names = ["rite", "acme", "northwind", "contoso-api", "deployment"]
        assert len({colour_for(n) for n in names}) > 1

    def test_stable_across_processes(self):
        """The regression guard for `hash()`.

        Python randomises string hashing per process unless PYTHONHASHSEED
        is pinned, so a name-derived colour built on `hash()` would change
        on every invocation — the one thing "stable per project across
        sessions and machines" rules out. Run in real subprocesses with
        different seeds, because that is the only way this can fail.
        """
        code = (
            "from rite_ai.label import colour_for;"
            "print(','.join(colour_for(n) for n in ('rite','acme','northwind')))"
        )
        results = set()
        for seed in ("0", "1", "99991", "random"):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
                check=True,
            )
            results.add(proc.stdout.strip())
        assert len(results) == 1, f"colour moved between processes: {results}"

    def test_every_colour_is_in_the_palette(self):
        for n in ("a", "b", "c", "rite", "acme", "x" * 50):
            assert colour_for(n) in PALETTE

    def test_red_is_not_a_project_colour(self):
        """Red means error everywhere else in this tool; a project that
        hashed onto it would look like a failing one every time it spoke."""
        assert not [c for c in PALETTE if "red" in c]


class TestProjectNameAlwaysAnswers:
    def test_reads_the_brief(self, tmp_path: Path):
        assert project_name(_project(tmp_path, "acme")) == "acme"

    def test_falls_back_to_the_directory_when_brief_is_missing(self, tmp_path: Path):
        root = tmp_path / "some-project"
        _project(root, None)
        assert project_name(root) == "some-project"

    def test_falls_back_when_the_brief_is_malformed(self, tmp_path: Path):
        """The case that matters most: a broken config is exactly the
        message that reaches someone with no context, so a label that
        vanishes when the project is broken is missing precisely when it
        is needed."""
        root = tmp_path / "broken-project"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: [unclosed\n")
        assert project_name(root) == "broken-project"

    def test_falls_back_when_the_brief_has_no_name(self, tmp_path: Path):
        root = tmp_path / "nameless"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text("project:\n  role: manager\n")
        assert project_name(root) == "nameless"


class TestColourIsDecorationOnly:
    def test_the_name_survives_without_a_terminal(self):
        rendered = ProjectLabel("rite", "cyan").render(_Stream(tty=False))
        assert rendered == f"{DOT} rite"
        assert "\x1b" not in rendered

    def test_colour_only_wraps_the_dot(self):
        rendered = ProjectLabel("rite", "cyan").render(_Stream(tty=True))
        assert "\x1b" in rendered
        # The name itself is never styled — it is the part that carries
        # the meaning, and it must read identically either way.
        assert rendered.endswith(" rite")

    def test_no_color_env_disables_it(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        assert colour_enabled(_Stream(tty=True)) is False

    def test_dumb_terminal_disables_it(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert colour_enabled(_Stream(tty=True)) is False

    def test_a_pipe_disables_it(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert colour_enabled(_Stream(tty=False)) is False

    def test_ascii_only_output_falls_back_to_a_plain_dot(self):
        rendered = ProjectLabel("rite", "cyan").render(
            _Stream(tty=False, encoding="ascii")
        )
        assert rendered == f"{ASCII_DOT} rite"

    def test_plain_never_carries_escapes(self):
        assert "\x1b" not in ProjectLabel("rite", "cyan").plain()


class TestDecorateLabelsEveryLine:
    def test_multi_line_messages_are_labelled_throughout(self, tmp_path: Path):
        """A YAML parse error quotes the offending line, so watchdog
        reasons run to several lines. A continuation line scrolling into
        view on its own is the unattributed text this exists to prevent."""
        root = _project(tmp_path / "proj", "proj")
        out = decorate(root, "first\nsecond\nthird", stream=_Stream(tty=False))
        assert out.split("\n") == [
            f"{DOT} proj  first",
            f"{DOT} proj  second",
            f"{DOT} proj  third",
        ]

    def test_single_line_is_prefixed_once(self, tmp_path: Path):
        root = _project(tmp_path / "proj", "proj")
        assert (
            decorate(root, "hello", stream=_Stream(tty=False)) == f"{DOT} proj  hello"
        )


class TestStoredStateKeepsThePlainName:
    """Rendering happens where a message is displayed. Nothing decorative
    is ever written into a file another program has to parse."""

    def test_outbox_records_the_project(self, tmp_path: Path):
        from rite_ai.reporting.outbox import enqueue, list_pending

        root = _project(tmp_path / "p", "acme")
        path = enqueue(root, "blocker", {"detail": "x"})
        assert json.loads(path.read_text())["project"] == "acme"
        assert list_pending(root)[0].project == "acme"

    def test_outbox_file_has_no_escapes_or_decoration(self, tmp_path: Path):
        from rite_ai.reporting.outbox import enqueue

        root = _project(tmp_path / "p", "acme")
        raw = enqueue(root, "blocker", {"detail": "x"}).read_text()
        assert "\x1b" not in raw
        assert DOT not in raw

    def test_a_message_queued_before_the_field_existed_still_has_a_project(
        self, tmp_path: Path
    ):
        from rite_ai.reporting.outbox import list_pending

        root = _project(tmp_path / "p", "acme")
        out = root / ".rite" / "outbox"
        out.mkdir(parents=True)
        (out / "1_blocker.json").write_text(
            json.dumps({"kind": "blocker", "payload": {}, "timestamp": 1})
        )
        assert list_pending(root)[0].project == "acme"

    def test_handover_snapshot_records_the_project(self, tmp_path: Path):
        from rite_ai.handover import read_snapshot, write_snapshot

        root = _project(tmp_path / "p", "acme")
        path = write_snapshot(root, ticket="T-1")
        assert json.loads(path.read_text())["project"] == "acme"
        assert read_snapshot(root).project == "acme"
        assert DOT not in path.read_text()


class TestHandoverCommentNamesItsProject:
    def test_the_comment_landing_on_a_board_says_which_project(self, tmp_path: Path):
        """A board can carry tickets from several projects, and this
        comment is read long after the session that queued it is gone."""
        from unittest.mock import MagicMock

        from rite_ai.lifecycle.commands import _deliver_via_backend
        from rite_ai.reporting.outbox import OutboxMessage

        root = _project(tmp_path / "p", "rite")
        backend = MagicMock()
        backend.comment.return_value = None
        backend.label.return_value = None

        _deliver_via_backend(backend, root)(
            OutboxMessage(
                kind="handover",
                payload={"ticket": "T-1", "reason": "session ended"},
                timestamp=0.0,
                path=Path("x"),
                project="acme",
            )
        )
        text = backend.comment.call_args[0][1]
        assert text.startswith(f"{DOT} acme — ")

    def test_it_uses_the_queueing_project_not_the_running_one(self, tmp_path: Path):
        """A queued message may be flushed by a later run in a different
        project's directory, and must still say where it came from."""
        from unittest.mock import MagicMock

        from rite_ai.lifecycle.commands import _deliver_via_backend
        from rite_ai.reporting.outbox import OutboxMessage

        root = _project(tmp_path / "p", "rite")
        backend = MagicMock()
        backend.comment.return_value = None
        backend.label.return_value = None
        _deliver_via_backend(backend, root)(
            OutboxMessage(
                kind="handover",
                payload={"ticket": "T-1", "reason": "r"},
                timestamp=0.0,
                path=Path("x"),
                project="acme",
            )
        )
        assert "acme" in backend.comment.call_args[0][1]
        assert "rite —" not in backend.comment.call_args[0][1]


class TestUnattendedCommandsLabelTheirOutput:
    def test_scheduler_tick_labels_every_line(self, tmp_path, monkeypatch):
        """The label sits after the timestamp the scheduler log gained —
        what matters is that every line carries it, not its column."""
        root = _project(tmp_path / "myproj", "myproj")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["scheduler-tick"])
        assert result.exit_code == 0, result.output
        for line in result.output.strip().split("\n"):
            assert f"{DOT} myproj  " in line, line

    def test_watchdog_labels_the_all_clear(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "myproj", "myproj")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["watchdog"])
        assert f"{DOT} myproj  ok" in result.output

    def test_watchdog_labels_a_config_error(self, tmp_path, monkeypatch):
        """The label has to survive the project being broken."""
        root = tmp_path / "brokenproj"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: [unclosed\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["watchdog"])
        assert result.exit_code == 1
        assert result.output.strip()
        for line in result.output.strip().split("\n"):
            assert line.startswith(f"{DOT} brokenproj  "), line

    def test_status_header_is_the_label(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "myproj", "myproj")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["status"])
        assert f"{DOT} myproj (manager)" in result.output


class TestLabelHelper:
    def test_label_pairs_the_name_with_its_colour(self, tmp_path: Path):
        root = _project(tmp_path / "p", "acme")
        lb = label(root)
        assert lb.name == "acme"
        assert lb.colour == colour_for("acme")
