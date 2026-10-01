"""C1 + C2: a Worker `rite init` creates is the Worker `rite add worker` creates.

Both halves of one defect, found in the v0.7.0 dogfood: `setup.offer_a_worker`
called `add_worker(root, name)` — two of its five parameters — so an
init-created Worker was

* **linked to no Manager** (C1), though the sole declared one had been sitting
  in `answers.config.coordination.managers` for two hundred lines. The brief
  that produced said "No Manager assigned yet." on line 7 and "Tell your
  Manager you are free" on line 112: a Worker handed instructions that
  repeatedly name an authority it was told does not exist. Nothing detected
  it — `what_is_missing` checked that a Manager was declared, that a module
  was registered and that a Worker existed, never that they were connected;
* **never offered the modules' own instruction files** (C2, S23), because the
  find-and-ask lived in `cli/main.py` and was reached from `add_worker_cmd`
  alone. The real project's module carried all three files and
  `workers/alpha/worker.yml` had no `follow_module_docs` key at all.

⚠ **These are BEHAVIOURAL tests.** A test that enumerates `add_worker`'s
parameters and asserts init passes each one is a ledger: it passes with the
bug present as soon as someone adds the argument and gets it wrong. What is
asserted here is what the Worker ends up being told.

⚠ Driven interactively and through `--config managers.add`, never through a
bare `--yes` — which declares no Manager by design (S15), so a `--yes` run
could not tell a missing link from a missing Manager.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli import main as cli_main
from rite_ai.cli.main import cli
from rite_ai.config.parse import ParseError, parse_worker
from rite_ai.workspace.manage import MODULE_DOC_NAMES

# existing code? y · path · changes · add mod/? · what is 'mod'? · Manager? ·
# Worker? · name
# (no role question — C7; no sandbox question — the fixture below; the module
# description is C8's, asked once per module registered)
INTERACTIVE = "y\n\n\n\n\n\n\n\n"
# … and the same with the Manager declined: Manager? n · another name? Enter
DECLINING_THE_MANAGER = "y\n\n\n\n\nn\n\n\n\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


@pytest.fixture
def at_a_terminal(monkeypatch):
    """Somebody is there to answer the module-docs question.

    ⚠ Needed because the prompt is GUARDED by whether anyone is, and a test
    that answers a question is by construction not the case the guard is for.
    Patched where the guard lives, which is the one place both entry points
    read it.
    """
    monkeypatch.setattr("rite_ai.cli.module_docs.somebody_is_there", lambda: True)


def _project(tmp_path: Path, *, docs: tuple[str, ...] = MODULE_DOC_NAMES) -> Path:
    """A project root that is not itself a repository, holding one module
    repository that keeps `docs` of its own."""
    root = tmp_path / "proj"
    mod = root / "mod"
    mod.mkdir(parents=True)
    (mod / "main.go").write_text("package main\n")
    for name in docs:
        (mod / name).write_text(f"# {name}\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "c"],
    ):
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
            cwd=mod,
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path, *args: str, input: str | None = None):
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result


def _preset(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "preset.yaml"
    path.write_text(body)
    return ["--config", str(path)]


def _worker(root: Path, name: str = "w1"):
    parsed = parse_worker(root / "workers" / name / "worker.yml")
    assert not isinstance(parsed, ParseError), parsed.message
    return parsed


def _brief_of(root: Path, name: str = "w1") -> str:
    return (root / "workers" / name / "CLAUDE.md").read_text()


class TestTheWorkerIsLinkedToTheManager:
    """C1. The name was available; it was discarded."""

    def test_the_worker_reports_to_the_manager_init_declared(self, tmp_path):
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE)

        assert _worker(root).manager == "lead"

    def test_and_its_brief_says_so_instead_of_contradicting_itself(self, tmp_path):
        """🔴 The reason an empty field was worse than it looks:
        `render_worker_claude_md` branches on `manifest.manager` for ONE line
        and the rest of the brief is unconditional."""
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE)

        brief = _brief_of(root)
        assert "Your Manager is **lead**." in brief
        assert "No Manager assigned yet." not in brief
        # The unconditional lines that made the contradiction visible.
        assert "Tell your Manager you are free" in brief

    def test_init_says_which_manager_it_linked(self, tmp_path):
        """A line saying how many modules were cloned looked complete."""
        root = _project(tmp_path)

        out = _init(root, input=INTERACTIVE).output

        assert "reports to Manager 'lead'" in out

    def test_the_config_route_links_it_too(self, tmp_path):
        """⚠ Not `--yes` alone: that declares no Manager by design (S15), so
        it cannot tell a missing link from a missing Manager."""
        root = _project(tmp_path)
        args = _preset(tmp_path, "managers:\n  add: lead\nworkers:\n  add: w1\n")

        _init(root, "--yes", *args)

        assert _worker(root).manager == "lead"
        assert "Your Manager is **lead**." in _brief_of(root)

    def test_a_manager_under_another_name_is_the_one_linked(self, tmp_path):
        """Not the string "lead": whatever was declared."""
        root = _project(tmp_path)
        args = _preset(
            tmp_path,
            "managers:\n  add: planner\n  preset: planner\nworkers:\n  add: w1\n",
        )

        _init(root, "--yes", *args)

        assert _worker(root).manager == "planner"

    def test_the_manager_it_names_is_the_one_rite_start_would_find(self, tmp_path):
        """Checked through the resolver every Manager command uses, not by
        reading a key: a link to a name nothing can start is not a link."""
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE)

        roles, problems = cli_main._manager_roles(root)
        assert not problems, problems
        assert _worker(root).manager in [r.name for r in roles]


class TestWithNoManagerDeclaredItIsStillHonest:
    """🔴 **None-safe, not `managers[0]`.** Every path that declines, presets
    `managers.add: false`, or hits the parser's refusal leaves
    `coordination.managers` empty, and a Worker is still declared on those
    paths."""

    def test_declining_the_manager_leaves_the_link_empty(self, tmp_path):
        root = _project(tmp_path)

        _init(root, input=DECLINING_THE_MANAGER)

        assert _worker(root).manager == ""

    def test_and_the_brief_says_there_is_none_rather_than_naming_one(self, tmp_path):
        root = _project(tmp_path)

        _init(root, input=DECLINING_THE_MANAGER)

        assert "No Manager assigned yet." in _brief_of(root)

    def test_a_config_that_declines_one_does_not_fail_the_run(self, tmp_path):
        """`managers.add: false` plus `workers.add` is the combination an
        unguarded `managers[0]` would have raised IndexError on."""
        root = _project(tmp_path)
        args = _preset(tmp_path, "managers:\n  add: false\nworkers:\n  add: w1\n")

        _init(root, "--yes", *args)

        assert _worker(root).manager == ""

    def test_and_so_does_a_bare_yes_with_a_worker(self, tmp_path):
        """`--yes` declares no Manager (S15) but `--config` can still declare
        a Worker, which is the same empty-list path by a different route."""
        root = _project(tmp_path)
        args = _preset(tmp_path, "workers:\n  add: w1\n")

        out = _init(root, "--yes", *args).output

        assert _worker(root).manager == ""
        assert "reports to Manager" not in out


class TestTheWorkerIsOfferedTheModulesOwnInstructions:
    """C2 / S23. A shipped feature that was unreachable from init."""

    def test_the_files_are_named_and_the_question_is_put(self, tmp_path, at_a_terminal):
        root = _project(tmp_path)

        out = _init(root, input=INTERACTIVE + "y\n").output

        assert "these modules keep instructions of their own" in out
        assert "should this Worker follow them?" in out
        for name in MODULE_DOC_NAMES:
            assert f"mod/{name}" in out

    def test_yes_lands_in_the_workers_own_record(self, tmp_path, at_a_terminal):
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE + "y\n")

        assert _worker(root).follow_module_docs == [
            f"mod/{name}" for name in MODULE_DOC_NAMES
        ]

    def test_and_in_the_brief_the_worker_actually_reads(self, tmp_path, at_a_terminal):
        """A decision recorded in worker.yml and rendered nowhere is an orphan
        key — the state `claude_instructions` was in before it had a reader."""
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE + "y\n")

        brief = _brief_of(root)
        for name in MODULE_DOC_NAMES:
            assert f"mod/{name}" in brief

    def test_no_records_nothing_and_says_so(self, tmp_path, at_a_terminal):
        root = _project(tmp_path)

        out = _init(root, input=INTERACTIVE + "n\n").output

        assert "not followed" in out
        assert _worker(root).follow_module_docs == []

    def test_a_module_with_no_instructions_of_its_own_is_not_asked_about(
        self, tmp_path, at_a_terminal
    ):
        """A question with no subject is noise."""
        root = _project(tmp_path, docs=())

        out = _init(root, input=INTERACTIVE).output

        assert "instructions of their own" not in out
        assert _worker(root).follow_module_docs == []

    def test_only_the_files_that_are_there_are_offered(self, tmp_path, at_a_terminal):
        """A Worker told to read a file that does not exist stops on its first
        instruction."""
        root = _project(tmp_path, docs=("CONTRIBUTING.md",))

        _init(root, input=INTERACTIVE + "y\n")

        assert _worker(root).follow_module_docs == ["mod/CONTRIBUTING.md"]


class TestNobodyIsThereToAnswer:
    """🔴 **A prompt is not an exception, it is the absence of an answer**
    (defect class 15). `click.confirm` on an empty stdin ABORTS, which is how
    reusing the CLI's step without its guard would turn an unattended `rite
    init` into one that creates no Worker."""

    def test_yes_does_not_ask_follows_nothing_and_says_both(self, tmp_path):
        root = _project(tmp_path)
        args = _preset(tmp_path, "managers:\n  add: lead\nworkers:\n  add: w1\n")

        out = _init(root, "--yes", *args).output

        assert "should this Worker follow them?" not in out
        assert "not asked" in out
        assert _worker(root).follow_module_docs == []

    def test_and_names_the_files_it_did_not_follow(self, tmp_path):
        """A default taken in silence is the other half of the same defect —
        the user would never learn the files existed."""
        root = _project(tmp_path)
        args = _preset(tmp_path, "workers:\n  add: w1\n")

        out = _init(root, "--yes", *args).output

        for name in MODULE_DOC_NAMES:
            assert f"mod/{name}" in out

    def test_it_says_how_to_answer_in_the_words_of_the_command_run(self, tmp_path):
        """⚠ `--follow-module-docs` alone is `rite add worker`'s remedy, not
        init's. The same guard, the caller's own way out of it."""
        root = _project(tmp_path)
        args = _preset(tmp_path, "workers:\n  add: w1\n")

        out = _init(root, "--yes", *args).output

        assert "re-run `rite init` with somebody at the terminal" in out
        assert "rite add worker w1 --follow-module-docs" in out

    def test_an_interactive_run_with_nothing_attached_does_not_abort(self, tmp_path):
        """The case the guard is actually for: no `--yes`, and no tty either —
        a scripted `rite init` with answers on a pipe, which is how the suite
        itself drives it."""
        root = _project(tmp_path)

        out = _init(root, input=INTERACTIVE).output

        assert "Aborted" not in out
        assert _worker(root).name == "w1"
        assert _worker(root).follow_module_docs == []

    def test_and_still_links_the_manager(self, tmp_path):
        """C1 does not depend on anybody being there to answer C2's
        question."""
        root = _project(tmp_path)

        _init(root, input=INTERACTIVE)

        assert _worker(root).manager == "lead"


class TestOneStepBothEntryPoints:
    """The durable half: `rite add worker` and `rite init` call the same
    function, so a third entry point is a call rather than a copy. S23's
    original defect was that this step existed in a command instead of in a
    place both could reach."""

    def test_init_goes_through_the_shared_step(self, tmp_path, monkeypatch):
        from rite_ai.cli import module_docs

        calls = []
        real = module_docs.settle_module_docs

        def spy(*a, **kw):
            calls.append(kw.get("interactive", True))
            return real(*a, **kw)

        monkeypatch.setattr(module_docs, "settle_module_docs", spy)
        _init(_project(tmp_path), input=INTERACTIVE)

        assert calls == [True]

    def test_and_so_does_rite_add_worker(self, tmp_path, monkeypatch):
        from rite_ai.cli import module_docs

        root = _project(tmp_path)
        _init(root, "--yes")
        calls = []
        real = module_docs.settle_module_docs
        monkeypatch.setattr(
            module_docs,
            "settle_module_docs",
            lambda *a, **kw: calls.append(a[1]) or real(*a, **kw),
        )
        monkeypatch.chdir(root)

        CliRunner().invoke(cli, ["add", "worker", "beta"], catch_exceptions=False)

        assert calls == [None], "the module subset, unset for a whole-project Worker"

    def test_the_guard_cannot_be_switched_off_by_a_caller(self, tmp_path):
        """⚠ The no-tty probe is INSIDE the step: `interactive=True` from a
        caller is still not a person. A seam that let the caller decide is a
        seam that can reintroduce the abort."""
        from rite_ai.cli.module_docs import settle_module_docs

        root = _project(tmp_path)
        _init(root, "--yes")

        found = settle_module_docs(root, None, None, interactive=True)

        assert found == []
