"""Scope discipline: the checklist backbone and the diff budget (SCRUM-65).

A Worker delivered a fix as 27 files, +1505/-112, and called it done; held to
its definition of done it is 18 files, +621/-72. rite's review ran and
PRODUCED the growth: all 34 findings were resolved by adding something.

Criteria covered here: 1 (checklist), 2 (register), 3 (Worker section),
4 (the budget would have held KAN-28's original and passes the cleaned one).
Criteria 5-8 need the scope reviewer and ten measured deliveries; they are
not claimed by these tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rite_ai.publishing.scope_budget import HOLD, PASS, measure

CHECKLIST = Path(__file__).resolve().parents[1] / "templates" / "review-checklist.md"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)


def _repo(tmp_path: Path, files: dict[str, str], *, base: dict[str, str]) -> Path:
    repo = tmp_path / "mod"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for name, body in base.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(body)
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base", cwd=repo)
    _git("checkout", "-q", "-b", "work", cwd=repo)
    for name, body in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(body)
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work", cwd=repo)
    return repo


def _measure(repo: Path, dod_paths: set[str], **kw) -> object:
    return measure(
        repo,
        "main..work",
        dod_paths=dod_paths,
        claimed_files=kw.get("claimed_files", set()),
        exclude=kw.get("exclude", ["*.lock", "**/vendor/**"]),
        items=kw.get("items", 3),
        lines_per_item=kw.get("lines_per_item", 150),
        factor=kw.get("factor", 2.0),
    )


class TestTheChecklist:
    """Criterion 1.

    Asserted against what `load_checklist` RENDERS, not the raw file: the
    renderer keeps only `- [ ]` items and joins their continuation lines, so
    a rule written as a paragraph never reaches a reviewer. That is how the
    tie-breaker came to be the first item rather than a heading note.
    """

    def text(self) -> str:
        return CHECKLIST.read_text()

    def scope_items(self) -> list[str]:
        from rite_ai.review.checklist import load_checklist

        return [
            " ".join(i.text.split())
            for i in load_checklist(CHECKLIST)
            if i.category == "Scope"
        ]

    def test_scope_is_first_under_correctness(self):
        s = self.text()
        correctness = s.index("## Correctness")
        assert s.index("### Scope") > correctness
        # Nothing else comes between them.
        between = s[correctness : s.index("### Scope")]
        assert "- [ ]" not in between, between

    def test_it_is_worded_as_the_tie_breaker_where_a_reviewer_reads_it(self):
        assert any("these Scope lines win" in i for i in self.scope_items())

    def test_it_carries_the_six_scope_items(self):
        rendered = self.scope_items()
        assert len(rendered) == 6, rendered
        joined = " ".join(rendered)
        for phrase in (
            "required by an item of the ticket's agreed definition of done",
            "Prefer deleting",
            "filed as a ticket",
            "No hardening or behaviour the ticket did not ask for",
            "ratio of added comment lines",
            "No edits to files the definition of done does not need",
        ):
            assert phrase in joined, phrase

    def test_the_three_conflicting_lines_are_reconciled(self):
        s = self.text()
        # :200 — a discovery is a ticket, not a fix in this diff.
        assert "**not a fix in\n      this diff**" in s
        # :210 — conventions govern how, never how much.
        assert "govern **how** you write what is needed" in s
        # :191 — now the first Scope item, not a lone line under Correctness.
        assert s.count("not more and not less") == 0

    def test_rite_review_prints_it(self, tmp_path):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "review-checklist.md").write_text(self.text())
        (rite / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\n"
            "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
        )
        (rite / "modules.yaml").write_text("modules: {}\n")
        (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
        out = (
            CliRunner(env={"RITE_PROJECT_ROOT": str(tmp_path)})
            .invoke(cli, ["review"])
            .output
        )
        assert "### Scope" in out
        assert "these Scope lines win" in " ".join(out.split())


class TestTheRegisterCanRemove:
    """Criterion 2."""

    def test_the_register_accepts_removed_and_not_needed(self):
        s = (
            Path(__file__).resolve().parents[1] / "templates" / "commands" / "review.md"
        ).read_text()
        assert "may be a deletion" in s
        assert "`removed`" in s and "not needed" in s

    def test_the_terminating_check_reports_how_much_the_fixes_grew_it(self):
        s = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "agents"
            / "reviewer-terminating.md"
        ).read_text()
        assert "GREW the diff" in s
        assert "name the DoD item it serves" in s


class TestTheWorkerSection:
    """Criterion 3: at most five bullets."""

    def section(self) -> str:
        from rite_ai.workspace.manage import WorkerManifest, render_worker_claude_md

        md = render_worker_claude_md(
            WorkerManifest(name="alpha", modules=[], manager="lead")
        )
        return md[md.index("## Scope") : md.index("## What you must not do")]

    def test_it_is_at_most_five_bullets(self):
        assert self.section().count("\n- ") == 5

    def test_it_says_the_five_things(self):
        s = self.section()
        for phrase in (
            "smallest diff",
            "one line",
            "question to your Manager",
            "FILED, not fixed here",
            "definition of done's\n  scope wins",
        ):
            assert phrase in s, phrase


class TestTheBudget:
    """Criterion 4, on KAN-28's measured shape."""

    def test_it_holds_a_delivery_that_touches_files_outside_the_dod(self, tmp_path):
        # KAN-28's out-of-DoD paths, as the ticket names them.
        repo = _repo(
            tmp_path,
            {
                "env.go": "package main\n// in scope\n",
                "internal/config/load.go": "package config\n",
                "orchestrator/create/run.go": "package create\n",
                "sandbox_options.go": "package main\n",
                "principles/style.md": "words\n",
                "reconfigure.md": "words\n",
            },
            base={"env.go": "package main\n"},
        )
        budget = _measure(repo, {"env.go"})
        assert budget.verdict == HOLD
        assert set(budget.out_of_scope) == {
            "internal/config/load.go",
            "orchestrator/create/run.go",
            "sandbox_options.go",
            "principles/style.md",
            "reconfigure.md",
        }
        assert "outside the definition of" in budget.reasons[0]

    def test_control_it_passes_the_cleaned_change(self, tmp_path):
        repo = _repo(
            tmp_path,
            {"env.go": "package main\n" + "x := 1\n" * 20},
            base={"env.go": "package main\n"},
        )
        budget = _measure(repo, {"env.go"})
        assert budget.verdict == PASS, budget.reasons
        assert budget.out_of_scope == []

    def test_it_holds_on_size_alone_when_every_file_is_in_scope(self, tmp_path):
        repo = _repo(
            tmp_path,
            {"env.go": "package main\n" + "x := 1\n" * 400},
            base={"env.go": "package main\n"},
        )
        budget = _measure(repo, {"env.go"}, items=1, lines_per_item=10, factor=2.0)
        assert budget.verdict == HOLD
        assert any("allowance" in r for r in budget.reasons)

    def test_a_file_level_claim_is_scope_but_a_directory_claim_is_not(self, tmp_path):
        repo = _repo(
            tmp_path,
            {"docs/a.md": "a\n", "docs/b.md": "b\n"},
            base={"docs/a.md": "", "docs/b.md": ""},
        )
        claimed = _measure(repo, set(), claimed_files={"docs/a.md"})
        assert claimed.out_of_scope == ["docs/b.md"]

    def test_excluded_paths_do_not_count(self, tmp_path):
        repo = _repo(
            tmp_path,
            {"env.go": "package main\n", "uv.lock": "x\n" * 500},
            base={"env.go": "", "uv.lock": ""},
        )
        budget = _measure(repo, {"env.go"}, exclude=["uv.lock"])
        assert budget.files == 1
        assert budget.verdict == PASS

    def test_a_rename_counts_once(self, tmp_path):
        repo = _repo(tmp_path, {}, base={"old.go": "package main\n" * 5})
        _git("mv", "old.go", "new.go", cwd=repo)
        _git(
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "rename",
            cwd=repo,
        )
        budget = _measure(repo, {"new.go"})
        assert budget.files == 1

    def test_it_records_the_comment_to_code_ratio(self, tmp_path):
        repo = _repo(
            tmp_path,
            {"a.py": "# one\n# two\n# three\nx = 1\n"},
            base={"a.py": ""},
        )
        budget = _measure(repo, {"a.py"})
        assert budget.comment_lines == 3
        assert budget.code_lines == 1
        assert budget.comment_ratio == 3.0

    def test_a_git_that_cannot_be_asked_is_not_a_hold(self, tmp_path):
        """Shadow mode must not invent a verdict from a failed measurement."""
        budget = _measure(tmp_path / "not-a-repo", {"a.py"})
        assert budget.verdict == PASS
        assert budget.unmeasured


class TestShadowMode:
    """The budget is recorded and delivery proceeds (criterion 5's input)."""

    def test_enforce_is_off_by_default(self):
        from rite_ai.config.models import ProjectConfig

        assert ProjectConfig().scope.enforce is False

    def test_the_verdict_is_shaped_for_the_delivery_event(self, tmp_path):
        repo = _repo(tmp_path, {"a.py": "x = 1\n"}, base={"a.py": ""})
        note = _measure(repo, {"a.py"}).note()
        for key in (
            "verdict",
            "files",
            "added",
            "removed",
            "out_of_scope",
            "comment_ratio",
            "allowance",
            "reasons",
        ):
            assert key in note, key
