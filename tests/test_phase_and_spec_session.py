"""The phase machine and `/spec` (SPEC §9.10, §9.13.1, D-53).

rite orchestrated work someone else had planned. The orientation table's
"no project spec" row had no code behind it, the generated `CLAUDE.md`
pointed a session at a table it did not contain, `rite start` printed an
inventory ending in `ready`, and a spec a session did write reached no
Owner. These pin each of those shut.

Not covered, and not coverable here: whether a live Claude session given
`/spec` writes a usable spec. That has not been run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.cli.init.claude_gen import generate_claude_md, install_claude_config
from rite_ai.cli.init.detect import detect_decision_convention
from rite_ai.cli.main import cli
from rite_ai.config.models import ProjectBrief, ProjectConfig
from rite_ai.config.parse import parse_config
from rite_ai.lifecycle import start
from rite_ai.phase import NO_PLAN_STEP, PHASE_GUIDE, detect_phase
from rite_ai.project_spec import (
    SPEC_SECTION_END,
    SPEC_SECTION_START,
    replace_spec_section,
)

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


def _project(root: Path, *, backend: str = "none", spec: str = "") -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
    )
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: {backend}\n  site: x.atlassian.net\n"
        f"  projects: {{workers: RW}}\n{spec}"
    )
    return root


def _with_claude_md(root: Path) -> Path:
    config = parse_config(root / ".rite" / "config.yaml")
    install_claude_config(
        root, "owner", ProjectBrief(name="acme", role="owner"), [], config
    )
    return root


def _one_line(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- phases


class TestPhaseComesFromDiskNotConfig:
    def test_no_rite_dir_is_not_set_up(self, tmp_path: Path):
        phase = detect_phase(tmp_path)
        assert phase.key == "not-set-up"
        assert "rite init" in " ".join(phase.next)

    def test_an_unusable_brief_is_not_set_up_and_does_not_just_say_init(
        self, tmp_path: Path
    ):
        """`rite init` refuses on an existing `.rite/` unless told to wipe
        it, so a bare "run rite init" would send someone into a refusal."""
        root = _project(tmp_path)
        (root / ".rite" / "brief.yaml").write_text("project: [broken\n")
        phase = detect_phase(root)
        assert phase.key == "not-set-up"
        steps = " ".join(phase.next)
        assert "rite doctor" in steps
        assert "wipe" in steps

    def test_greenfield_is_no_spec_and_routes_to_spec(self, tmp_path: Path):
        phase = detect_phase(_project(tmp_path))
        assert phase.key == "no-spec"
        assert "/spec" in " ".join(phase.next)

    def test_a_registered_path_with_nothing_there_is_no_spec(self, tmp_path: Path):
        """The failure a phase machine must not have: config names a spec,
        the file is gone, and the user is sent onward as if it existed."""
        root = _project(tmp_path, spec="spec:\n  paths: [SPEC.md]\n")
        phase = detect_phase(root)
        assert phase.key == "no-spec"
        assert "nothing is there" in phase.where
        assert "rite spec remove SPEC.md" in " ".join(phase.next)

    def test_an_unregistered_spec_file_is_named_with_the_command(self, tmp_path: Path):
        root = _project(tmp_path)
        (root / "SPEC.md").write_text("# acme\n")
        phase = detect_phase(root)
        assert phase.key == "no-spec"
        assert "rite spec add SPEC.md" in " ".join(phase.next)

    def test_a_docs_directory_is_only_a_note(self, tmp_path: Path):
        """A `docs/` may be README fragments; stating it as the spec would
        send someone to register a folder that is not one."""
        root = _project(tmp_path)
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("x")
        phase = detect_phase(root)
        assert phase.key == "no-spec"
        assert "/spec" in " ".join(phase.next)
        assert any("docs/" in note for note in phase.notes)

    def test_a_present_spec_without_a_board_names_the_board_first(self, tmp_path: Path):
        root = _project(tmp_path, spec="spec:\n  paths: [SPEC.md]\n")
        (root / "SPEC.md").write_text("# acme\n")
        phase = detect_phase(root)
        assert phase.key == "spec-ready"
        assert "ticket_backend" in phase.next[0]
        assert any(NO_PLAN_STEP in step for step in phase.next)

    def test_a_present_spec_with_a_board_names_the_check_that_tells_them_apart(
        self, tmp_path: Path
    ):
        """`rite start` does not read the board — that is a network call —
        so it names the command that does, instead of guessing."""
        root = _project(tmp_path, backend="jira", spec="spec:\n  paths: [SPEC.md]\n")
        (root / "SPEC.md").write_text("# acme\n")
        steps = " ".join(detect_phase(root).next)
        assert "rite board list" in steps
        assert "/ticket" in steps
        assert NO_PLAN_STEP in steps

    def test_active_claims_are_work_in_progress_without_a_spec(self, tmp_path: Path):
        root = _project(tmp_path)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/app.py"], "alpha", "RW-9")
        phase = detect_phase(root)
        assert phase.key == "in-progress"
        assert "/ticket RW-9" in " ".join(phase.next)
        assert any("/spec" in note for note in phase.notes)

    def test_the_missing_plan_step_says_what_to_do_instead(self):
        """The README says rite has no planning step. Promising `/plan`
        here would contradict it."""
        assert "no planning step" in NO_PLAN_STEP
        assert "/refine" in NO_PLAN_STEP
        assert "/plan" not in NO_PLAN_STEP


class TestStartPrintsWhereAndWhatNext:
    def test_start_carries_the_phase(self, tmp_path: Path):
        result = start(_project(tmp_path))
        assert result.ok
        assert result.phase is not None and result.phase.key == "no-spec"

    def test_a_failed_start_still_carries_the_phase(self, tmp_path: Path):
        result = start(tmp_path)
        assert not result.ok
        assert result.phase is not None and result.phase.key == "not-set-up"

    def test_the_cli_ends_with_where_and_next(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path))
        result = CliRunner().invoke(cli, ["start"])
        assert result.exit_code == 0, result.output
        tail = result.output.strip().splitlines()
        where = next(i for i, line in enumerate(tail) if line.startswith("where:"))
        assert tail[where + 1] == "next:"
        assert "/spec" in "\n".join(tail[where:])

    def test_the_cli_prints_the_phase_with_no_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["start"])
        assert result.exit_code == 1
        assert "where: This directory is not set up for rite yet." in result.output
        assert "rite init" in result.output


# ---------------------------------------------------------------- CLAUDE.md


class TestClaudeMdCarriesTheTable:
    def _md(self, tmp_path: Path, config: ProjectConfig | None = None) -> str:
        return generate_claude_md(
            "owner",
            ProjectBrief(name="acme", role="owner"),
            [],
            config or ProjectConfig(),
            tmp_path,
        )

    def test_the_table_is_in_the_file_not_pointed_at(self, tmp_path: Path):
        md = self._md(tmp_path)
        assert PHASE_GUIDE in md
        assert "orientation table as normal" not in md

    def test_every_phase_row_is_present(self, tmp_path: Path):
        md = self._md(tmp_path)
        for row in (
            "Not set up",
            "Work in progress",
            "No spec",
            "Spec, no tickets yet",
            "Tickets on the board",
        ):
            assert f"| {row} |" in md

    def test_the_session_is_told_to_guide_the_user(self, tmp_path: Path):
        md = self._md(tmp_path)
        assert "Tell the user where the project is and what comes next" in md

    def test_nothing_tells_a_session_to_append_to_brief_yaml(self, tmp_path: Path):
        """`ProjectBrief` has no `enriched` field; answers written there
        reached nobody (D-53)."""
        md = _one_line(self._md(tmp_path))
        assert "appending to the same file" not in md
        assert "under `technology.architecture`" not in md

    def test_spec_is_listed_and_refine_is_told_apart_from_it(self, tmp_path: Path):
        md = _one_line(self._md(tmp_path))
        assert "`/spec` — write this project's spec" in md
        assert "Not `/spec`." in md

    def test_install_writes_the_spec_command(self, tmp_path: Path):
        install_claude_config(
            tmp_path, "owner", ProjectBrief(name="a", role="owner"), [], ProjectConfig()
        )
        assert (tmp_path / ".claude" / "commands" / "spec.md").is_file()


# ---------------------------------------------------------------- templates


class TestSpecTemplate:
    text = (TEMPLATES / "commands" / "spec.md").read_text()

    def test_the_decision_register_is_in_the_skeleton_and_mandatory(self):
        assert "| D | Decision | Choice | Why |" in self.text
        assert "| D-1 | Scope of the first version |" in self.text
        assert "mandatory, even with a single row" in self.text

    def test_it_implements_nothing_and_creates_no_tickets(self):
        assert "implements nothing and creates no tickets" in self.text

    def test_it_ends_by_registering(self):
        assert "rite spec add SPEC.md" in self.text

    def test_nothing_is_written_before_the_user_approves(self):
        """An abandoned `/spec` used to leave a `SPEC.md` the user never
        agreed to and was never told about."""
        assert "Write nothing to disk in this step." in self.text
        draft = self.text.index("Draft the spec from this skeleton")
        show = self.text.index("Show the draft and ask them to correct it")
        write = self.text.index("write `SPEC.md` at the project root and register it")
        assert draft < show < write, "the file is still written before approval"

    def test_it_tells_the_user_to_commit_and_why(self):
        """A Worker gets its files by cloning, so an uncommitted spec exists
        in no Worker's checkout and the pointer resolves to nothing."""
        assert "Tell them to commit" in self.text
        assert "`.rite/config.yaml` and `CLAUDE.md`" in self.text
        assert "cloning" in self.text
        assert "do not run it for them" in self.text.lower()

    def test_answers_go_into_the_spec_not_the_brief(self):
        assert "Do not write the answers into `.rite/brief.yaml`" in self.text

    def test_questions_are_about_the_project_not_rite(self):
        assert "never about rite" in self.text

    def test_the_skeleton_register_is_detected_as_a_convention(self, tmp_path: Path):
        """A new spec holds one decision; the old three-reference bar would
        never have proposed a convention for it, so nothing cited it."""
        (tmp_path / "SPEC.md").write_text(
            "# acme\n\n## Decisions\n\n| D | Decision | Choice | Why |\n"
            "|---|---|---|---|\n| D-1 | Scope | CLI only | first version |\n"
        )
        assert "D-<number>" in detect_decision_convention(tmp_path, ["SPEC.md"])

    def test_a_stray_single_reference_is_still_not_a_convention(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("see D-1 for background\n")
        assert detect_decision_convention(tmp_path, ["SPEC.md"]) == ""


class TestDecisionReadersMovedOffEnriched:
    def test_reviewer_decisions_reads_the_spec_register(self):
        text = (TEMPLATES / "agents" / "reviewer-decisions.md").read_text()
        assert "enriched" not in text
        assert "decision register" in text

    def test_review_briefs_it_to_the_spec(self):
        text = (TEMPLATES / "commands" / "review.md").read_text()
        assert "enriched" not in text

    def test_refine_says_it_is_not_spec(self):
        assert "It is not `/spec`" in (TEMPLATES / "commands" / "refine.md").read_text()

    def test_the_spec_no_longer_calls_refine_a_spec_writer(self):
        spec = (REPO / "SPEC.md").read_text()
        assert "turn a ticket into a startable spec" not in spec


# ---------------------------------------------------------------- refresh


class TestRegisteringReachesEveryClaudeMd:
    def test_spec_add_rewrites_the_owner_section(self, tmp_path, monkeypatch):
        root = _with_claude_md(_project(tmp_path))
        before = (root / "CLAUDE.md").read_text()
        assert "**No spec yet.**" in before
        (root / "SPEC.md").write_text(
            "| D | Decision | Choice | Why |\n|---|---|---|---|\n| D-1 | a | b | c |\n"
        )
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        assert result.exit_code == 0, result.output
        assert "updated CLAUDE.md" in result.output
        after = (root / "CLAUDE.md").read_text()
        assert "- `SPEC.md`" in after
        assert "**No spec yet.**" not in after
        assert after.count(SPEC_SECTION_START) == 1
        # Everything outside the section is untouched.
        assert before.split(SPEC_SECTION_START)[0] == after.split(SPEC_SECTION_START)[0]
        assert before.split(SPEC_SECTION_END)[1] == after.split(SPEC_SECTION_END)[1]

    def test_spec_add_records_the_convention_for_one_row(self, tmp_path, monkeypatch):
        root = _with_claude_md(_project(tmp_path))
        (root / "SPEC.md").write_text(
            "| D | Decision | Choice | Why |\n|---|---|---|---|\n| D-1 | a | b | c |\n"
        )
        monkeypatch.chdir(root)
        CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        config = parse_config(root / ".rite" / "config.yaml")
        assert "D-<number>" in config.spec.convention

    def test_a_worker_created_before_the_spec_gets_it(self, tmp_path, monkeypatch):
        from rite_ai.workspace import add_worker

        root = _with_claude_md(_project(tmp_path))
        assert add_worker(root, "alpha").ok
        worker_md = root / "workers" / "alpha" / "CLAUDE.md"
        assert "Project spec" not in worker_md.read_text()
        (root / "SPEC.md").write_text("# acme\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        assert "updated workers/alpha/CLAUDE.md" in result.output
        assert "- `SPEC.md`" in (root / "workers" / "alpha" / "CLAUDE.md").read_text()

    def test_removing_the_last_spec_routes_back_to_spec(self, tmp_path, monkeypatch):
        root = _with_claude_md(_project(tmp_path))
        (root / "SPEC.md").write_text(
            "| D | Decision | Choice | Why |\n|---|---|---|---|\n| D-1 | a | b | c |\n"
        )
        monkeypatch.chdir(root)
        CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        result = CliRunner().invoke(cli, ["spec", "remove", "SPEC.md"])
        assert result.exit_code == 0, result.output
        assert "**No spec yet.**" in (root / "CLAUDE.md").read_text()
        assert parse_config(root / ".rite" / "config.yaml").spec.convention == ""

    def test_a_human_authored_claude_md_is_left_alone(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        (root / "CLAUDE.md").write_text("# my own instructions\n")
        (root / "SPEC.md").write_text("# acme\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        assert "not generated by rite, left untouched" in result.output
        assert (root / "CLAUDE.md").read_text() == "# my own instructions\n"


class TestSectionReplacementOnOlderFiles:
    SECTION = f"{SPEC_SECTION_START}\n## Project spec\n\nnew\n{SPEC_SECTION_END}"

    def test_a_file_from_before_markers_gets_the_section_inserted(self):
        legacy = (
            "# CLAUDE.md — acme\n\nGenerated by `rite init`.\n\n## Modules\n\nnone\n"
        )
        out = replace_spec_section(legacy, self.SECTION, "## Modules")
        assert out.index("## Project spec") < out.index("## Modules")

    def test_a_bare_project_spec_heading_is_replaced_not_duplicated(self):
        legacy = "# x\n\n## Project spec\n\nold\n\n## Modules\n\nnone\n"
        out = replace_spec_section(legacy, self.SECTION, "## Modules")
        assert out.count("## Project spec") == 1
        assert "old" not in out and "new" in out

    def test_no_anchor_means_no_guess(self):
        out = replace_spec_section("# x\n\nnothing here\n", self.SECTION, "## Modules")
        assert out is None

    def test_removing_leaves_no_blank_line_pile_up(self):
        text = (
            f"a\n\n{SPEC_SECTION_START}\n## Project spec\n"
            f"{SPEC_SECTION_END}\n\n## Your modules\n"
        )
        assert "\n\n\n" not in replace_spec_section(text, "", "## Your modules")


# ---------------------------------------------------------------- doctor


class TestDoctorReports:
    def _repo(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
            cwd=root,
            check=True,
        )
        return root

    def test_no_spec_points_at_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(self._repo(_project(tmp_path)))
        output = CliRunner().invoke(cli, ["doctor"]).output
        assert "spec: no project spec — run `/spec`" in output

    def test_an_unregistered_spec_file_is_named(self, tmp_path, monkeypatch):
        root = self._repo(_project(tmp_path))
        (root / "SPEC.md").write_text("# acme\n")
        monkeypatch.chdir(root)
        output = CliRunner().invoke(cli, ["doctor"]).output
        assert "SPEC.md exists but is not registered" in output
        assert "rite spec add SPEC.md" in output

    def test_a_missing_spec_is_not_counted_as_a_problem(self, tmp_path, monkeypatch):
        """A project with no spec yet is early, not misconfigured."""
        monkeypatch.chdir(self._repo(_project(tmp_path)))
        output = CliRunner().invoke(cli, ["doctor"]).output
        has_problems = "problem(s) found" in output
        problems = output.split("problem(s) found")[0] if has_problems else ""
        assert "spec" not in problems.splitlines()[-1] if problems else True

    def test_an_orphaned_enriched_section_is_reported(self, tmp_path, monkeypatch):
        root = self._repo(_project(tmp_path))
        (root / ".rite" / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\nenriched:\n  follow_ups:\n"
            "    - question: q\n      answer: a\n"
        )
        monkeypatch.chdir(root)
        output = CliRunner().invoke(cli, ["doctor"]).output
        assert "`enriched:` section nothing reads" in output
