"""`rite add worker` asks about a module's own instruction files (S23).

A module often keeps instructions of its own — `CLAUDE.md`, `AGENTS.md`,
`CONTRIBUTING.md`. Until now `rite add worker` neither read them nor
mentioned them, so a Worker was started with no idea the module had
conventions at all.

🔴 **Following them silently would be the worse bug.** Those files are
written for the module, mostly for people, and they do not know how rite
drives a Worker: "open a pull request from your fork", "push before you
ask", "run the full suite on every commit". A Worker holds no GitHub
credential and does not decide whether to push. So the files are FOUND and
NAMED, the question is PUT, and the answer is recorded per Worker — and
where one of them contradicts rite, the Worker is told to report that
rather than choose quietly.
"""

from __future__ import annotations

from itertools import combinations

import pytest
import yaml
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.models import Module, WorkerManifest
from rite_ai.config.parse import ParseError, parse_worker
from rite_ai.workspace.manage import (
    MODULE_DOC_NAMES,
    module_docs,
    modules_for_worker,
    render_worker_claude_md,
)


def _project(tmp_path, *, docs: tuple[str, ...] = (), module: str = "backend"):
    """A project with one registered module, holding `docs`."""
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "modules.yaml").write_text(
        f"modules:\n  {module}:\n    path: {module}\n    branch: main\n"
    )
    checkout = tmp_path / module
    checkout.mkdir()
    for name in docs:
        (checkout / name).write_text(f"# {name}\n")
    return tmp_path


@pytest.fixture
def at_a_terminal(monkeypatch):
    """Somebody is there to answer.

    ⚠ Needed because the prompt is GUARDED by whether anyone is: a test
    that answers a question is, by construction, not the case the guard is
    for. Flipped here so the asking path and the not-asking path are both
    reachable.
    """
    monkeypatch.setattr("rite_ai.cli.main._somebody_is_there", lambda: True)


def _add(*args, answer: str | None = None):
    return CliRunner().invoke(
        cli, ["add", "worker", *args], input=answer, catch_exceptions=False
    )


def _manifest(tmp_path, worker: str) -> WorkerManifest:
    parsed = parse_worker(tmp_path / "workers" / worker / "worker.yml")
    assert not isinstance(parsed, ParseError), parsed.message
    return parsed


class TestItFindsThem:
    def test_a_modules_own_claude_md_is_found(self, tmp_path):
        root = _project(tmp_path, docs=("CLAUDE.md",))

        assert module_docs(root, [Module(name="backend", path="backend")]) == [
            "backend/CLAUDE.md"
        ]

    def test_all_three_names_are_looked_for(self, tmp_path):
        root = _project(tmp_path, docs=MODULE_DOC_NAMES)

        found = module_docs(root, [Module(name="backend", path="backend")])

        assert found == [f"backend/{n}" for n in MODULE_DOC_NAMES]

    def test_a_file_that_is_not_there_is_not_named(self, tmp_path):
        """A Worker told to read a file that does not exist is a Worker
        that stops on its first instruction."""
        root = _project(tmp_path, docs=("CONTRIBUTING.md",))

        assert module_docs(root, [Module(name="backend", path="backend")]) == [
            "backend/CONTRIBUTING.md"
        ]

    def test_a_module_with_no_checkout_contributes_nothing(self, tmp_path):
        root = _project(tmp_path)

        assert module_docs(root, [Module(name="gone", path="gone")]) == []

    def test_a_directory_of_that_name_is_not_a_file(self, tmp_path):
        """`is_file`, not `exists` — a `CLAUDE.md/` directory is not a
        document, and telling a Worker to read one is telling it nonsense."""
        root = _project(tmp_path)
        (root / "backend" / "CLAUDE.md").mkdir()

        assert module_docs(root, [Module(name="backend", path="backend")]) == []

    def test_a_trailing_slash_in_the_recorded_path_does_not_double(self, tmp_path):
        """⚠ `rite add module` records `backend/`, and an f-string join
        made `backend//AGENTS.md` — a path that reads as a typo in the one
        file a Worker trusts."""
        root = _project(tmp_path, docs=("AGENTS.md",))

        found = module_docs(root, [Module(name="backend", path="backend/")])

        assert found == ["backend/AGENTS.md"]
        assert "//" not in found[0]


class TestItAsks:
    def test_it_names_the_files_before_asking(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha", answer="y\n")

        assert "these modules keep instructions of their own" in result.output
        assert "backend/CONTRIBUTING.md" in result.output

    def test_yes_records_them_against_the_worker(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("alpha", answer="y\n")

        assert _manifest(tmp_path, "alpha").follow_module_docs == [
            "backend/CONTRIBUTING.md"
        ]

    def test_no_records_nothing(self, tmp_path, monkeypatch, at_a_terminal):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("beta", answer="n\n")

        assert _manifest(tmp_path, "beta").follow_module_docs == []

    def test_no_is_said_out_loud_rather_than_passing_in_silence(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("beta", answer="n\n")

        assert "not followed" in result.output

    def test_nothing_found_means_nothing_asked(self, tmp_path, monkeypatch):
        """A question with no subject is noise, and answering it changes
        nothing."""
        monkeypatch.chdir(_project(tmp_path))

        result = _add("alpha")

        assert "instructions of their own" not in result.output
        assert _manifest(tmp_path, "alpha").follow_module_docs == []

    def test_the_flag_answers_without_being_asked(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha", "--follow-module-docs")

        assert "should this Worker follow them?" not in result.output
        assert _manifest(tmp_path, "alpha").follow_module_docs == [
            "backend/CONTRIBUTING.md"
        ]

    def test_and_the_negative_flag_does_too(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha", "--no-follow-module-docs")

        assert "should this Worker follow them?" not in result.output
        assert _manifest(tmp_path, "alpha").follow_module_docs == []

    def test_only_the_modules_this_worker_gets_are_asked_about(
        self, tmp_path, monkeypatch
    ):
        """⚠ The subset rule is `add_worker`'s, shared rather than copied:
        asking about a module the Worker will not have is asking about a
        file it will not have either."""
        root = _project(tmp_path, docs=("CONTRIBUTING.md",))
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend\n  frontend:\n    path: frontend\n"
        )
        (root / "frontend").mkdir()
        (root / "frontend" / "AGENTS.md").write_text("# frontend\n")
        monkeypatch.chdir(root)

        result = _add("alpha", "--modules", "backend", answer="y\n")

        assert "backend/CONTRIBUTING.md" in result.output
        assert "frontend/AGENTS.md" not in result.output


class TestNobodyIsThereToAnswer:
    """🔴 **A prompt is not an exception, it is the absence of an answer.**

    `click.confirm` on an empty stdin ABORTS. Asking unconditionally turned
    `rite add worker` in a script — or in CI — into a command that creates
    no Worker at all, which an existing test caught going red after this
    feature landed.
    """

    def test_it_does_not_stall_or_abort(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha")

        assert "Aborted" not in result.output
        assert _manifest(tmp_path, "alpha").name == "alpha"

    def test_it_follows_nothing_it_was_not_told_to_follow(self, tmp_path, monkeypatch):
        """The safe default of the two: instructions nobody chose do not
        get into a Worker's brief."""
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("alpha")

        assert _manifest(tmp_path, "alpha").follow_module_docs == []

    def test_and_says_both_that_and_how_to_answer(self, tmp_path, monkeypatch):
        """⚠ A default taken in silence is the other half of the same
        defect — the user would never learn the files existed."""
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha")

        assert "not asked" in result.output
        assert "backend/CONTRIBUTING.md" in result.output
        assert "--follow-module-docs" in result.output

    def test_a_flag_still_works_with_nobody_there(self, tmp_path, monkeypatch):
        """Which is the whole point of having the flags."""
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("alpha", "--follow-module-docs")

        assert _manifest(tmp_path, "alpha").follow_module_docs == [
            "backend/CONTRIBUTING.md"
        ]


class TestRitesOwnFilesAreNotAModulesConventions:
    """🔴 **A project whose repository is its own module** — which `rite
    init` supports and warns about — has the project's GENERATED
    `CLAUDE.md` sitting at the module's path.

    Offering it would ask whether a Worker should follow the Owner's
    brief, and a yes would put project-wide instructions into a session
    that is explicitly not allowed to make project-wide decisions. Found
    by an existing test going red, not by review.
    """

    def test_a_claude_md_rite_generated_is_not_offered(self, tmp_path):
        from rite_ai.cli.init.claude_gen import GENERATED_MARKER

        root = _project(tmp_path)
        (root / "backend" / "CLAUDE.md").write_text(
            f"# CLAUDE.md\n\n{GENERATED_MARKER}\n"
        )

        assert module_docs(root, [Module(name="backend", path="backend")]) == []

    def test_nor_is_a_workers_generated_one(self, tmp_path):
        from rite_ai.project_spec import WORKER_GENERATED_MARKER

        root = _project(tmp_path)
        (root / "backend" / "CLAUDE.md").write_text(
            f"# CLAUDE.md\n\n{WORKER_GENERATED_MARKER}\n"
        )

        assert module_docs(root, [Module(name="backend", path="backend")]) == []

    def test_but_a_hand_written_one_still_is(self, tmp_path):
        """⚠ The control. Without it, excluding everything would pass."""
        root = _project(tmp_path, docs=("CLAUDE.md",))

        assert module_docs(root, [Module(name="backend", path="backend")]) == [
            "backend/CLAUDE.md"
        ]

    def test_the_other_two_names_are_judged_the_same_way(self, tmp_path):
        """Every name in the range, not just the one that collided."""
        from rite_ai.cli.init.claude_gen import GENERATED_MARKER

        for name in MODULE_DOC_NAMES:
            root = tmp_path / f"g{name}"
            root.mkdir()
            _project(root)
            (root / "backend" / name).write_text(f"x\n{GENERATED_MARKER}\n")

            found = module_docs(root, [Module(name="backend", path="backend")])

            assert found == [], name


class TestTheWorkerIsActuallyTold:
    """A decision recorded in worker.yml and rendered nowhere is an orphan
    key — the state `claude_instructions` was in before it had a reader."""

    def test_the_files_are_named_in_the_workers_own_instructions(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("alpha", answer="y\n")

        text = (tmp_path / "workers" / "alpha" / "CLAUDE.md").read_text()
        assert "backend/CONTRIBUTING.md" in text

    def test_and_which_wins_when_they_disagree_with_rite(self, tmp_path):
        """⚠ The whole reason this is a question. A module's file may tell
        a Worker to do something rite does not allow it to do."""
        md = render_worker_claude_md(
            WorkerManifest(
                name="alpha",
                modules=["backend"],
                follow_module_docs=["backend/CONTRIBUTING.md"],
            )
        )

        assert "THIS file and the ticket win" in md
        assert "report" in md

    def test_declining_leaves_no_section_at_all(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("beta", answer="n\n")

        text = (tmp_path / "workers" / "beta" / "CLAUDE.md").read_text()
        assert "modules' own instructions" not in text

    def test_the_key_is_omitted_from_worker_yml_when_empty(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """An empty key invites someone to wonder what it does — the rule
        `claude_instructions` is written under."""
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        _add("beta", answer="n\n")

        raw = yaml.safe_load((tmp_path / "workers" / "beta" / "worker.yml").read_text())
        assert "follow_module_docs" not in raw["worker"]

    def test_it_round_trips_through_worker_yml(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """Written and read back by the parser, so the next command to load
        this Worker sees the same decision."""
        monkeypatch.chdir(_project(tmp_path, docs=MODULE_DOC_NAMES))

        _add("alpha", answer="y\n")

        assert _manifest(tmp_path, "alpha").follow_module_docs == [
            f"backend/{n}" for n in MODULE_DOC_NAMES
        ]


class TestTheInvariantAcrossEveryCombinationOfTheThree:
    """⚠ **Every subset of the three files, not none-and-all.**

    Three names give eight combinations, and the interesting ones are in
    the middle: two of three present, in an order that is not the order
    they are looked for. A finder that stopped at the first hit, or that
    reported the order of the directory listing rather than its own, is
    invisible at both extremes and wrong in six of the eight.
    """

    def _subsets(self):
        for size in range(len(MODULE_DOC_NAMES) + 1):
            yield from combinations(MODULE_DOC_NAMES, size)

    def test_every_subset_is_found_whole_and_in_order(self, tmp_path):
        for i, subset in enumerate(self._subsets()):
            root = tmp_path / f"p{i}"
            root.mkdir()
            _project(root, docs=subset)

            found = module_docs(root, [Module(name="backend", path="backend")])

            expected = [f"backend/{n}" for n in MODULE_DOC_NAMES if n in subset]
            assert found == expected, f"{subset}: {found}"

    def test_every_subset_survives_the_whole_command(self, tmp_path, monkeypatch):
        """Found, asked, recorded, parsed back — for all eight."""
        for i, subset in enumerate(self._subsets()):
            root = tmp_path / f"c{i}"
            root.mkdir()
            _project(root, docs=subset)
            monkeypatch.chdir(root)

            _add("alpha", "--follow-module-docs")

            # ⚠ Not asserted on the exit code: a registered module with no
            # clone source makes `rite add worker` exit 1 by design, and
            # that is about cloning, not about this question. The Worker is
            # registered either way, and what it was told is the property.
            expected = [f"backend/{n}" for n in MODULE_DOC_NAMES if n in subset]
            assert _manifest(root, "alpha").follow_module_docs == expected, subset

    def test_declining_records_nothing_for_every_subset(self, tmp_path, monkeypatch):
        """The negative half of the same range: no subset sneaks through."""
        for i, subset in enumerate(self._subsets()):
            root = tmp_path / f"d{i}"
            root.mkdir()
            _project(root, docs=subset)
            monkeypatch.chdir(root)

            _add("alpha", "--no-follow-module-docs")

            assert _manifest(root, "alpha").follow_module_docs == [], subset

    def test_every_subset_across_two_modules_keeps_module_order(self, tmp_path):
        """The range crossed with a second module: the files are grouped by
        module, in the order the modules are given."""
        for i, subset in enumerate(self._subsets()):
            root = tmp_path / f"m{i}"
            root.mkdir()
            (root / ".rite").mkdir()
            for name in ("backend", "frontend"):
                (root / name).mkdir()
                for doc in subset:
                    (root / name / doc).write_text("x\n")

            found = module_docs(
                root,
                [
                    Module(name="backend", path="backend"),
                    Module(name="frontend", path="frontend"),
                ],
            )

            expected = [
                f"{mod}/{n}"
                for mod in ("backend", "frontend")
                for n in MODULE_DOC_NAMES
                if n in subset
            ]
            assert found == expected, subset


class TestTheSubsetRuleIsShared:
    def test_an_unknown_module_is_refused_once_not_twice(self, tmp_path, monkeypatch):
        """`add_worker` refuses it a moment later in the same words, so the
        asking step stays quiet rather than printing a second copy."""
        monkeypatch.chdir(_project(tmp_path, docs=("CONTRIBUTING.md",)))

        result = _add("alpha", "--modules", "nope")

        assert result.exit_code == 1
        assert result.output.count("unknown module") == 1

    def test_modules_for_worker_returns_the_same_set_add_worker_uses(self, tmp_path):
        root = _project(tmp_path)
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  a:\n    path: a\n  b:\n    path: b\n"
        )

        assert [m.name for m in modules_for_worker(root)] == ["a", "b"]
        assert [m.name for m in modules_for_worker(root, ["b"])] == ["b"]
        assert isinstance(modules_for_worker(root, ["zz"]), str)
