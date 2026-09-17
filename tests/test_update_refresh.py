"""`rite update` refreshes a project's generated files without clobbering edits.

Asserted against real generated output, not fixtures that imitate it: what
`rite init` and `rite add worker` actually write is what a project has."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.init.claude_gen import generate_claude_md
from rite_ai.cli.main import cli
from rite_ai.config.models import ProjectBrief, ProjectConfig
from rite_ai.generated_sections import MARKER_RE, parse
from rite_ai.update.refresh import refresh_template, refresh_text


def _generated(config: ProjectConfig | None = None, tmp: Path = Path("/tmp")) -> str:
    brief = ProjectBrief(name="acme", role="owner")
    return generate_claude_md("owner", brief, [], config or ProjectConfig(), tmp)


def _section(text: str, heading: str) -> str:
    return next(
        b.content for b in parse(text) if b.kind == "section" and b.heading == heading
    )


def _unmarked(text: str) -> str:
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not MARKER_RE.match(line.strip())
    )


def test_parsing_is_lossless():
    text = _generated() + "\n## My notes\n\nkeep this\n"
    assert "".join(b.raw for b in parse(text)) == text


def test_every_generated_section_is_marked():
    text = _generated()
    sections = [b for b in parse(text) if b.kind == "section"]
    assert sections and all(b.recorded for b in sections)


def test_a_current_project_is_left_byte_identical():
    text = _generated()
    new, changes = refresh_text(text, _generated())
    assert new == text
    assert changes == []


def test_an_unedited_section_is_refreshed_when_rite_changes_it():
    old = _generated().replace(
        "## Claims system\n", "## Claims system\n\nAn older sentence.\n", 1
    )
    # Re-mark so the older text is what rite wrote, not an edit.
    from rite_ai.generated_sections import mark_sections

    old = mark_sections(_unmarked(old))
    new, changes = refresh_text(old, _generated())
    assert [c.action for c in changes] == ["refreshed"]
    assert new == _generated()


def test_an_edited_section_is_kept_byte_for_byte_and_reported():
    text = _generated().replace("Claim → work", "Claim → MY OWN STEP → work", 1)
    new, changes = refresh_text(text, _generated())
    assert new == text
    (change,) = changes
    assert change.action == "kept-edited"
    assert "MY OWN STEP" in change.diff


def test_a_section_this_version_adds_is_inserted_in_order():
    full = _generated()
    without = "".join(
        b.raw
        for b in parse(full)
        if not (b.kind == "section" and b.heading == "Claims system")
    )
    new, changes = refresh_text(without, full)
    assert [c.action for c in changes] == ["inserted"]
    headings = [b.heading for b in parse(new) if b.kind == "section"]
    assert headings == [b.heading for b in parse(full) if b.kind == "section"]


def test_your_own_sections_and_the_header_are_never_touched():
    text = _generated()
    head, rest = text.split("\n## ", 1)
    mine = "\n## House rules\n\nNo deploys on Friday.\n"
    edited = head.replace("acme", "acme (my header edit)") + mine + "\n## " + rest
    new, _ = refresh_text(edited, _generated())
    assert new.startswith(head.replace("acme", "acme (my header edit)"))
    assert "## House rules\n\nNo deploys on Friday.\n" in new


def test_it_is_idempotent():
    legacy = _unmarked(_generated()).replace("Claim → work", "Claim → mine → work", 1)
    once, _ = refresh_text(legacy, _generated())
    twice, changes = refresh_text(once, _generated())
    assert twice == once
    assert all(c.action in ("kept-edited", "kept-unknown") for c in changes)


class TestAFileWrittenBeforeMarkersExisted:
    def test_identical_sections_are_adopted_without_changing_their_text(self):
        legacy = _unmarked(_generated())
        new, changes = refresh_text(legacy, _generated())
        assert {c.action for c in changes} == {"marked"}
        assert _unmarked(new) == legacy

    def test_a_differing_section_is_kept_because_nothing_can_tell_whose_it_is(self):
        legacy = _unmarked(_generated()).replace(
            "Claim → work", "Claim → older → work", 1
        )
        new, changes = refresh_text(legacy, _generated())
        kept = [c for c in changes if c.action == "kept-unknown"]
        assert len(kept) == 1 and "older" in kept[0].diff
        assert "Claim → older → work" in new

    def test_take_rite_replaces_exactly_the_named_section(self):
        legacy = _unmarked(_generated()).replace(
            "Claim → work", "Claim → older → work", 1
        )
        heading = next(
            b.heading for b in parse(legacy) if b.kind == "section" and "older" in b.raw
        )
        new, changes = refresh_text(legacy, _generated(), frozenset({heading}))
        assert any(c.action == "taken" and c.target == heading for c in changes)
        assert "older" not in new


def test_the_v0_2_0_owner_gets_the_phase_table_and_can_take_the_guidance():
    """The case that motivated this: a project initialised before the phase
    table and the Worker-fungibility guidance existed."""
    current = _generated()
    phase = "Where this project is, and what to do next"
    role = _section(current, "Role: Owner")
    old_role = (
        role.split("\n\n**Workers are interchangeable")[0]
        + role[role.index("\n\n**Ticket backend:**") :]
    )
    legacy = "".join(
        b.raw
        for b in parse(_unmarked(current))
        if not (b.kind == "section" and b.heading == phase)
    ).replace(role, old_role)
    assert phase not in legacy and "interchangeable" not in legacy

    new, changes = refresh_text(legacy, current)
    actions = {c.target: c.action for c in changes}
    assert actions[phase] == "inserted"
    assert actions["Role: Owner"] == "kept-unknown"
    assert "interchangeable" not in new

    taken, _ = refresh_text(new, current, frozenset({"Role: Owner"}))
    assert "Workers are interchangeable" in taken


def test_spec_add_keeps_markers_attached(tmp_path: Path):
    from rite_ai.cli.init.claude_gen import _spec_section
    from rite_ai.config.models import SpecConfig
    from rite_ai.project_spec import replace_spec_section

    text = _generated()
    config = ProjectConfig(spec=SpecConfig(paths=["SPEC.md"]))
    new = replace_spec_section(text, _spec_section(config), "## Modules")
    sections = {b.heading: b for b in parse(new) if b.kind == "section"}
    assert sections["Modules"].recorded is not None


class TestCopiedTemplates:
    def test_a_missing_command_is_installed(self, tmp_path: Path):
        template = tmp_path / "spec.md"
        template.write_text("new command\n")
        root = tmp_path / "proj"
        dest = root / ".claude" / "commands" / "spec.md"
        ch = refresh_template(
            dest, template, "commands/spec.md", "spec.md", frozenset(), True, root
        )
        assert ch.action == "installed" and dest.read_text() == "new command\n"

    def test_a_released_version_is_refreshed_and_an_edit_is_kept(self, tmp_path: Path):
        import hashlib

        template = tmp_path / "review.md"
        template.write_text("current\n")
        released = b"what v0.1.0 shipped\n"
        root = tmp_path / "proj"
        dest = root / ".claude" / "commands" / "review.md"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(released)
        history = {
            "commands/review.md": frozenset({hashlib.sha256(released).hexdigest()})
        }
        with patch("rite_ai.update.template_history.RELEASED", history):
            ch = refresh_template(
                dest,
                template,
                "commands/review.md",
                "review.md",
                frozenset(),
                True,
                root,
            )
            assert ch.action == "refreshed" and dest.read_text() == "current\n"
            dest.write_text("my edits\n")
            ch = refresh_template(
                dest,
                template,
                "commands/review.md",
                "review.md",
                frozenset(),
                True,
                root,
            )
            assert ch.action == "kept-edited" and dest.read_text() == "my edits\n"

    def test_the_history_covers_what_the_last_release_shipped(self):
        """A release that forgets `tools/template_history.py` would treat its
        own templates as user edits on the next upgrade.

        Asked of the LAST TAG's bytes, not the working tree's. The working
        tree is main, where a template is edited long before it is released,
        so judging it against `RELEASED` made every template edit red until
        the next tag — and unfixable, because the tool that regenerates the
        table reads tags and the tag does not exist yet. The mistake worth
        catching shows at the tag, which is where this now looks.
        """
        import hashlib
        import subprocess

        from rite_ai.update.template_history import RELEASED

        root = Path(__file__).resolve().parent.parent

        def git(*args: str) -> bytes:
            return subprocess.run(
                ["git", *args], cwd=root, capture_output=True, check=True
            ).stdout

        try:
            tags = sorted(t for t in git("tag", "-l", "v*").decode().split() if t)
        except (OSError, subprocess.CalledProcessError) as e:  # pragma: no cover
            pytest.skip(f"git tags are not readable here ({e})")
        if not tags:  # pragma: no cover - a shallow or tagless checkout
            pytest.skip("no release tags in this checkout")
        tag = tags[-1]
        listing = (
            git(
                "ls-tree",
                "-r",
                "--name-only",
                tag,
                "--",
                "templates/commands",
                "templates/agents",
                "templates/review-checklist.md",
            )
            .decode()
            .split()
        )
        assert listing, f"{tag} shipped no templates, which cannot be right"
        missing = []
        for path in listing:
            digest = hashlib.sha256(git("show", f"{tag}:{path}")).hexdigest()
            key = path.removeprefix("templates/")
            if digest not in RELEASED.get(key, frozenset()):
                missing.append(key)
        # Rendered, not copied: what a release WROTE is the template with that
        # release's pin in it — the same substitution the generator makes.
        template = git("show", f"{tag}:templates/ci/publish-gate.yml").decode()
        rendered = template.replace(
            "{{RITE_INSTALL_SPEC}}",
            f"git+https://github.com/robbartoszewski/rite.git@{tag}",
        )
        if hashlib.sha256(rendered.encode()).hexdigest() not in RELEASED.get(
            "ci/publish-gate.yml", frozenset()
        ):
            missing.append("ci/publish-gate.yml")
        assert not missing, (
            f"{tag} shipped {missing}, which `RELEASED` does not carry — run "
            "`uv run python tools/template_history.py` after tagging, or "
            "`rite update` will treat that release's own files as user edits"
        )


def _project(tmp_path: Path, monkeypatch) -> Path:
    from rite_ai.cli.init import run_init

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    import rite_ai.sandbox as sb

    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)
    return root


def test_dry_run_writes_nothing_and_a_real_run_refreshes(tmp_path: Path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    claude = root / "CLAUDE.md"
    legacy = _unmarked(claude.read_text())
    phase = "## Where this project is, and what to do next"
    legacy = legacy.replace(
        legacy[
            legacy.index(phase) : legacy.index("\n## ", legacy.index(phase) + 1) + 1
        ],
        "",
    )
    claude.write_text(legacy)
    (root / ".claude" / "commands" / "spec.md").unlink()

    dry = CliRunner().invoke(cli, ["update", "--files-only", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would add" in dry.output and "would install" in dry.output
    assert claude.read_text() == legacy
    assert not (root / ".claude" / "commands" / "spec.md").exists()

    real = CliRunner().invoke(cli, ["update", "--files-only"])
    assert real.exit_code == 0, real.output
    assert phase in claude.read_text()
    assert (root / ".claude" / "commands" / "spec.md").exists()

    again = CliRunner().invoke(cli, ["update", "--files-only"])
    assert "generated files already current" in again.output


def test_a_claude_md_rite_did_not_write_is_left_alone(tmp_path: Path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    (root / "CLAUDE.md").write_text("# my own instructions\n")
    result = CliRunner().invoke(cli, ["update", "--files-only"])
    assert "not generated by rite" in result.output
    assert (root / "CLAUDE.md").read_text() == "# my own instructions\n"


class TestASectionAReleaseWrote:
    """A file written before markers existed still receives improvements to
    the sections a release wrote identically for every project — those bytes
    are attributable, so refreshing them is not a guess."""

    def test_it_is_refreshed_without_being_asked(self):
        import hashlib
        from unittest.mock import patch

        current = _generated()
        older = _unmarked(current).replace(
            "Claim → work", "Claim → what v0.2.0 said → work", 1
        )
        section = _section(older, "Ticket workflow")
        history = {
            "Ticket workflow": frozenset(
                {hashlib.sha256(section.strip().encode()).hexdigest()}
            )
        }
        with patch("rite_ai.update.section_history.SECTIONS", history):
            new, changes = refresh_text(older, current)
        assert [c.action for c in changes if c.target == "Ticket workflow"] == [
            "refreshed"
        ]
        assert "what v0.2.0 said" not in new

    def test_an_edit_is_still_kept(self):
        current = _generated()
        mine = _unmarked(current).replace("Claim → work", "Claim → MY STEP → work", 1)
        new, changes = refresh_text(mine, current)
        assert [c.action for c in changes if c.target == "Ticket workflow"] == [
            "kept-unknown"
        ]
        assert "MY STEP" in new

    def test_the_history_covers_this_versions_static_sections(self):
        """A release that forgets `tools/section_history.py` stops delivering
        improvements to every project initialised before it."""
        import hashlib

        from rite_ai.update.section_history import SECTIONS

        current = _generated()
        stale = [
            heading
            for heading in (
                "Ticket workflow",
                "Claims system",
                "Review convention",
                "Publish gate",
            )
            if hashlib.sha256(_section(current, heading).strip().encode()).hexdigest()
            not in SECTIONS.get(heading, frozenset())
        ]
        assert not stale, (
            f"{stale} differ from every release — if this is a release, run "
            "`uv run python tools/section_history.py` after tagging"
        )
