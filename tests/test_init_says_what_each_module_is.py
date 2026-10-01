"""SCRUM-11 / C8: a module `rite init` registers says what it is.

`rite add module --description` sets one and it renders into the brief's module
table, under the module's own heading. Every module `rite init` registered had
`description: ''` — init built `Module(...)` itself and wrote `modules.yaml`
directly, and `_add_the_linked_module` called `add_module(root, name, url=url)`
without the parameter the CLI passes. Same shape as C1 and C2: init's path was
thinner than the command it stands in for.

The default is read out of the module's own README, which is where a module
already says what it is, and it is **offered**: Enter accepts it, anything
typed replaces it, `--yes` takes it silently (the rule every other `--yes`
answer follows). Never derived and written without being shown first — a wrong
description in the one file an agent reads to find out what it is working on is
worse than none.

Also here: `modules.yaml` had TWO writers that agreed by coincidence —
`manage.write_modules_file`, atomic and under the lock, and
`scaffold.write_modules`, a plain truncating `write_text`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli
from rite_ai.workspace.manage import DESCRIPTION_LIMIT, description_from_readme


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _module_repo(path: Path, readme: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.go").write_text("package main\n")
    if readme is not None:
        (path / "README.md").write_text(readme)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "c")
    return path


def _project(tmp_path: Path, readme: str | None = None) -> Path:
    """A non-repository root holding one module repository."""
    root = tmp_path / "proj"
    root.mkdir()
    _module_repo(root / "api", readme)
    return root


def _init(root: Path, *args: str, input: str | None = None):
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result


def _modules(root: Path) -> dict:
    return yaml.safe_load((root / ".rite" / "modules.yaml").read_text())["modules"]


# existing code? y · path · changes · add api/? Enter · describe api · Manager?
# Enter · Worker? n
def _answers(description: str = "") -> str:
    return f"y\n\n\n\n{description}\n\nn\n"


A_README = "# api\n\n[![build](x)](y)\n\nThe case service. It also does other things.\n"


class TestTheDefaultComesFromTheModulesOwnReadme:
    """Unit-level, because the derivation is the part with edges."""

    def test_the_first_prose_line_is_taken(self, tmp_path):
        d = _module_repo(tmp_path / "m", "The case service.\n")

        assert description_from_readme(d) == "The case service"

    def test_the_title_heading_is_not_a_description(self, tmp_path):
        """A README opens with the module's own name, which the table already
        shows."""
        d = _module_repo(tmp_path / "m", "# api\n\nThe case service.\n")

        assert description_from_readme(d) == "The case service"

    def test_badges_and_images_are_skipped(self, tmp_path):
        d = _module_repo(tmp_path / "m", A_README)

        assert description_from_readme(d) == "The case service"

    def test_only_the_first_sentence(self, tmp_path):
        """READMEs run long and the brief renders this as one line."""
        d = _module_repo(tmp_path / "m", "Does A. Then B. Then C.\n")

        assert description_from_readme(d) == "Does A"

    def test_a_long_line_is_cut_rather_than_wrapped(self, tmp_path):
        d = _module_repo(tmp_path / "m", "x" * 400 + "\n")

        got = description_from_readme(d)

        assert len(got) == DESCRIPTION_LIMIT
        assert got.endswith("…")

    def test_markdown_emphasis_does_not_travel_into_the_yaml(self, tmp_path):
        """⚠ A line opening with `**` is prose. Matching a bare `*` as a
        bullet skipped it entirely and the module got no description."""
        d = _module_repo(tmp_path / "m", "**The** `case` service.\n")

        assert description_from_readme(d) == "The case service"

    def test_but_a_bullet_list_is_not_a_description(self, tmp_path):
        d = _module_repo(tmp_path / "m", "# api\n\n* does A\n- does B\n")

        assert description_from_readme(d) == ""

    def test_no_readme_is_no_guess(self, tmp_path):
        d = _module_repo(tmp_path / "m")

        assert description_from_readme(d) == ""

    def test_a_readme_with_nothing_but_a_title_is_no_guess_either(self, tmp_path):
        """🔴 Returns "" rather than reaching further for something that looks
        like prose. A wrong description is worse than none."""
        d = _module_repo(tmp_path / "m", "# api\n\n![logo](l.png)\n")

        assert description_from_readme(d) == ""

    def test_a_directory_of_that_name_is_not_a_readme(self, tmp_path):
        d = _module_repo(tmp_path / "m")
        (d / "README.md").mkdir()

        assert description_from_readme(d) == ""

    def test_a_module_directory_that_is_not_there_is_not_an_error(self, tmp_path):
        assert description_from_readme(tmp_path / "gone") == ""


class TestItIsOfferedNotTaken:
    def test_the_question_is_put_with_the_readmes_line_as_the_default(self, tmp_path):
        root = _project(tmp_path, A_README)

        out = _init(root, input=_answers()).output

        assert "What is 'api'?" in out
        assert "[The case service]" in out

    def test_enter_accepts_it(self, tmp_path):
        root = _project(tmp_path, A_README)

        _init(root, input=_answers())

        assert _modules(root)["api"]["description"] == "The case service"

    def test_anything_typed_replaces_it(self, tmp_path):
        root = _project(tmp_path, A_README)

        _init(root, input=_answers("Cases, and nothing else"))

        assert _modules(root)["api"]["description"] == "Cases, and nothing else"

    def test_a_module_with_no_readme_is_still_asked(self, tmp_path):
        """The question is about the module, not about the README — a module
        with no README is the one that most needs a line."""
        root = _project(tmp_path)

        out = _init(root, input=_answers("The API")).output

        assert "What is 'api'?" in out
        assert _modules(root)["api"]["description"] == "The API"

    def test_and_pressing_enter_there_leaves_it_empty(self, tmp_path):
        root = _project(tmp_path)

        _init(root, input=_answers())

        assert _modules(root)["api"]["description"] == ""


class TestYesTakesTheDefaultSilently:
    def test_the_readmes_line_is_taken(self, tmp_path):
        root = _project(tmp_path, A_README)

        _init(root, "--yes")

        assert _modules(root)["api"]["description"] == "The case service"

    def test_without_a_question(self, tmp_path):
        root = _project(tmp_path, A_README)

        out = _init(root, "--yes").output

        assert "What is 'api'?" not in out

    def test_and_a_config_that_names_one_wins(self, tmp_path):
        """⚠ The control for the derivation: a value the user wrote is not
        overwritten by one read out of a file."""
        root = _project(tmp_path, A_README)
        preset = tmp_path / "p.yaml"
        preset.write_text(
            "modules:\n  api:\n    path: api/\n"
            "    description: Ours, not the README's\n"
        )

        _init(root, "--yes", "--config", str(preset))

        assert _modules(root)["api"]["description"] == "Ours, not the README's"


class TestItReachesTheBriefsModuleTable:
    """A field written to `modules.yaml` and rendered nowhere is an orphan key
    — which is what `claude_instructions` was before it had a reader."""

    def test_the_projects_claude_md_carries_it(self, tmp_path):
        root = _project(tmp_path, A_README)

        _init(root, "--yes")

        assert "The case service" in (root / "CLAUDE.md").read_text()


class TestTheLinkedModuleGetsOneToo:
    """`_add_the_linked_module` is the `add_module` call that omitted
    `description=`. ⚠ Asked BEFORE the clone, so there is no README to derive
    from — the parameter is what parity means here."""

    def _a_remote(self, tmp_path: Path) -> Path:
        remote = tmp_path / "linked.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
        src = _module_repo(tmp_path / "src")
        _git(src, "push", "-q", str(remote), "HEAD:main")
        return remote

    # existing code? y · path Enter · where is the code? url · add as module
    # 'linked'? Enter · Manager? Enter · what is 'linked'? · Worker? n
    def _answers(self, remote: Path, description: str) -> str:
        return f"y\n\n{remote}\n\n\n{description}\nn\n"

    def test_the_question_is_put_and_the_answer_registered(self, tmp_path):
        remote = self._a_remote(tmp_path)
        root = tmp_path / "proj"
        root.mkdir()

        out = _init(root, input=self._answers(remote, "The linked one")).output

        assert "What is 'linked'?" in out
        assert _modules(root)["linked"]["description"] == "The linked one"

    def test_it_goes_through_add_module_with_the_description(
        self, tmp_path, monkeypatch
    ):
        """Through the writer `rite add module` uses, with the field it takes
        — not a second copy of registration."""
        from rite_ai import workspace

        seen = {}
        real = workspace.add_module

        def spy(root, name, **kw):
            seen.update(kw)
            return real(root, name, **kw)

        monkeypatch.setattr(workspace, "add_module", spy)
        remote = self._a_remote(tmp_path)
        root = tmp_path / "proj"
        root.mkdir()

        _init(root, input=self._answers(remote, "A described module"))

        assert seen.get("description") == "A described module"

    def test_enter_leaves_it_empty_rather_than_inventing_one(self, tmp_path):
        """⚠ The control, and the reason this path has no derived default:
        nothing is cloned when the question is put, so there is no README to
        read. An empty answer stays empty."""
        remote = self._a_remote(tmp_path)
        root = tmp_path / "proj"
        root.mkdir()

        out = _init(root, input=self._answers(remote, "")).output

        assert "What is 'linked'?" in out
        assert _modules(root)["linked"]["description"] == ""


class TestModulesYamlHasOneWriter:
    """🔴 There were two, and they did not write the same way: `add_module`
    and `remove_module` went through the atomic writer under
    `_locked_modules`, while `rite init` and `rite module set-command` went
    through a plain truncating `write_text` — the half `_locked_modules`' own
    docstring calls the worse one, because a process killed between the
    truncate and the write leaves a torn `modules.yaml` that `load_project`
    then fails on."""

    def test_the_init_path_writes_through_the_one_writer(self, tmp_path, monkeypatch):
        from rite_ai.workspace import manage

        calls = []
        real = manage.write_modules_file
        monkeypatch.setattr(
            manage,
            "write_modules_file",
            lambda p, m: calls.append(p.name) or real(p, m),
        )

        _init(_project(tmp_path, A_README), "--yes")

        assert calls and set(calls) == {"modules.yaml"}

    def test_it_writes_atomically(self, tmp_path, monkeypatch):
        """What the second writer did not do. Asserted through
        `state.write_atomic`, which is where the guarantee lives."""
        from rite_ai.workspace import manage

        calls = []
        real = manage.write_atomic
        monkeypatch.setattr(
            manage,
            "write_atomic",
            lambda p, t, **kw: calls.append(p.name) or real(p, t, **kw),
        )

        _init(_project(tmp_path, A_README), "--yes")

        assert "modules.yaml" in calls

    def test_and_so_does_rite_module_set_command(self, tmp_path, monkeypatch):
        """The third caller of the old plain writer."""
        from rite_ai.workspace import manage

        root = _project(tmp_path, A_README)
        _init(root, "--yes")
        calls = []
        real = manage.write_modules_file
        monkeypatch.setattr(
            manage,
            "write_modules_file",
            lambda p, m: calls.append(p.name) or real(p, m),
        )
        monkeypatch.chdir(root)

        result = CliRunner().invoke(
            cli, ["module", "set-command", "api", "test", "go test ./..."]
        )

        assert result.exit_code == 0, result.output
        assert calls == ["modules.yaml"]

    def test_the_file_still_round_trips_through_the_parser(self, tmp_path):
        """One writer, same shape: whatever it writes, `parse_modules` reads."""
        from rite_ai.config.parse import parse_modules

        root = _project(tmp_path, A_README)
        _init(root, "--yes")

        parsed = parse_modules(root / ".rite" / "modules.yaml")

        assert [m.name for m in parsed] == ["api"]
        assert parsed[0].description == "The case service"
