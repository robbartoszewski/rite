"""`rite update` refreshes a project's generated files without clobbering edits.

Asserted against real generated output, not fixtures that imitate it: what
`rite init` and `rite add worker` actually write is what a project has."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
        dest = tmp_path / ".claude" / "commands" / "spec.md"
        ch = refresh_template(
            dest, template, "commands/spec.md", "spec.md", frozenset(), True
        )
        assert ch.action == "installed" and dest.read_text() == "new command\n"

    def test_a_released_version_is_refreshed_and_an_edit_is_kept(self, tmp_path: Path):
        import hashlib

        template = tmp_path / "review.md"
        template.write_text("current\n")
        released = b"what v0.1.0 shipped\n"
        dest = tmp_path / "dest.md"
        dest.write_bytes(released)
        history = {
            "commands/review.md": frozenset({hashlib.sha256(released).hexdigest()})
        }
        with patch("rite_ai.update.template_history.RELEASED", history):
            ch = refresh_template(
                dest, template, "commands/review.md", "review.md", frozenset(), True
            )
            assert ch.action == "refreshed" and dest.read_text() == "current\n"
            dest.write_text("my edits\n")
            ch = refresh_template(
                dest, template, "commands/review.md", "review.md", frozenset(), True
            )
            assert ch.action == "kept-edited" and dest.read_text() == "my edits\n"

    def test_the_history_covers_what_this_version_ships(self):
        """A release that forgets `tools/template_history.py` would treat its
        own templates as user edits on the next upgrade."""
        import hashlib

        from rite_ai.cli.init.claude_gen import _AGENT_FILES, _COMMAND_FILES
        from rite_ai.cli.init.paths import templates_dir
        from rite_ai.update.template_history import RELEASED

        src = templates_dir()
        stale = []
        for sub, names in (("agents", _AGENT_FILES), ("commands", _COMMAND_FILES)):
            for name in names:
                digest = hashlib.sha256((src / sub / name).read_bytes()).hexdigest()
                if digest not in RELEASED.get(f"{sub}/{name}", frozenset()):
                    stale.append(f"{sub}/{name}")
        assert not stale, (
            f"{stale} differ from every released version — if this is a release, "
            "run `uv run python tools/template_history.py` after tagging"
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
