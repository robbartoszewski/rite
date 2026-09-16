from pathlib import Path

from rite_ai.cli.init.claude_gen import (
    _role_section,
    generate_claude_md,
    install_claude_config,
)
from rite_ai.config.models import (
    ExpertiseEntry,
    Module,
    ProjectBrief,
    ProjectConfig,
    TicketBackendConfig,
)


def _brief(**overrides) -> ProjectBrief:
    base = dict(
        name="acme",
        role="owner",
        root_branch="main",
        kind="full-stack",
        features="Order tracking",
        platform="linux",
        languages=["python"],
        frameworks=[],
        architecture="",
    )
    base.update(overrides)
    return ProjectBrief(**base)


def test_generate_claude_md_owner_no_modules(tmp_path: Path):
    brief = _brief(role="owner")
    md = generate_claude_md("owner", brief, [], ProjectConfig(), tmp_path)
    assert "Owner" in md
    assert "No modules registered yet" in md
    assert "rite claim" in md
    assert "/ticket" in md
    assert "/review" in md
    assert "/refine" in md


def test_generate_claude_md_manager(tmp_path: Path):
    brief = _brief(role="manager")
    md = generate_claude_md("manager", brief, [], ProjectConfig(), tmp_path)
    assert "Role: Manager" in md
    assert "rite add worker" in md
    assert "Role: Owner" not in md


def test_generate_claude_md_rejects_worker_role(tmp_path: Path):
    brief = _brief()
    try:
        generate_claude_md("worker", brief, [], ProjectConfig(), tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported role")


def test_generate_claude_md_never_invents_architecture(tmp_path: Path):
    brief = _brief(architecture="")
    md = generate_claude_md("owner", brief, [], ProjectConfig(), tmp_path)
    assert "Not specified" in md
    assert "event sourcing" not in md.lower() or "if this project has" in md.lower()


def test_generate_claude_md_uses_given_architecture_verbatim(tmp_path: Path):
    brief = _brief(architecture="event sourcing, CQRS")
    md = generate_claude_md("owner", brief, [], ProjectConfig(), tmp_path)
    assert "event sourcing, CQRS" in md


def test_generate_claude_md_detected_module_commands(tmp_path: Path):
    module_dir = tmp_path / "backend"
    module_dir.mkdir()
    (module_dir / "pyproject.toml").write_text(
        "[project]\nname='x'\n[dependency-groups]\ndev=['pytest']\n"
    )
    modules = [
        Module(
            name="backend", path="backend/", url=None, branch="main", description="API"
        )
    ]
    md = generate_claude_md("owner", _brief(), modules, ProjectConfig(), tmp_path)
    assert "uv run pytest" in md
    assert "- Test: `uv run pytest`" in md


def test_generate_claude_md_undetected_module_placeholder_not_invented(tmp_path: Path):
    module_dir = tmp_path / "mystery"
    module_dir.mkdir()
    modules = [Module(name="mystery", path="mystery/", url=None, branch="main")]
    md = generate_claude_md("owner", _brief(), modules, ProjectConfig(), tmp_path)
    assert "not detected" in md
    assert "Do not guess" in md


def test_generate_claude_md_ticket_backend_none(tmp_path: Path):
    md = generate_claude_md("owner", _brief(), [], ProjectConfig(), tmp_path)
    assert "No ticket backend configured" in md


def test_generate_claude_md_ticket_backend_jira(tmp_path: Path):
    config = ProjectConfig(
        ticket_backend=TicketBackendConfig(type="jira", site="team.atlassian.net")
    )
    md = generate_claude_md("owner", _brief(), [], config, tmp_path)
    assert "team.atlassian.net" in md


def test_generate_claude_md_expertise_listed(tmp_path: Path):
    config = ProjectConfig(expertise=[ExpertiseEntry(name="alice", tags=["frontend"])])
    md = generate_claude_md("owner", _brief(), [], config, tmp_path)
    assert "alice" in md
    assert "frontend" in md


def test_install_claude_config_writes_agents_and_commands(tmp_path: Path):
    counts = install_claude_config(
        "owner" and tmp_path, "owner", _brief(), [], ProjectConfig()
    )
    assert (tmp_path / "CLAUDE.md").exists()
    agents = sorted(p.name for p in (tmp_path / ".claude" / "agents").glob("*.md"))
    commands = sorted(p.name for p in (tmp_path / ".claude" / "commands").glob("*.md"))
    assert agents == [
        "reviewer-decisions.md",
        "reviewer-round1.md",
        "reviewer-seam.md",
        "reviewer-terminating.md",
    ]
    assert commands == ["refine.md", "review.md", "ticket.md"]
    assert counts["agents"] == 4
    assert counts["commands"] == 3
    # Nothing of the user's was moved aside: there was no CLAUDE.md here.
    assert counts["preserved"] == ""


def test_the_module_map_is_actually_consumed_by_an_instruction(tmp_path: Path):
    """`rite init` detected `Test:` and `Lint:` for every module and wrote
    them into the module map — and then no instruction anywhere told a
    session to run them. Detected-and-unread is the same shape as
    built-and-uncalled: the work happens, the output is correct, and nothing
    downstream reads it.

    This asserts the seam, not the prose: the file that PRINTS the commands
    is the file that has to tell the reader to run them.
    """
    module_dir = tmp_path / "backend"
    module_dir.mkdir()
    (module_dir / "pyproject.toml").write_text(
        "[project]\nname='x'\n[dependency-groups]\ndev=['pytest','ruff']\n"
    )
    modules = [Module(name="backend", path="backend/", url=None, branch="main")]

    md = generate_claude_md("owner", _brief(), modules, ProjectConfig(), tmp_path)

    # The map still carries them...
    assert "- Test: `uv run pytest`" in md
    assert "- Lint: `uv run ruff check .`" in md
    # ...and something in the same file now says to run them.
    workflow = md[md.index("## Ticket workflow") :]
    assert "tests and lint" in workflow
    assert "`Test:` and `Lint:`" in workflow
    assert "not detected" in workflow  # and what to do when they are absent


def test_the_ticket_command_tells_the_worker_to_run_them(tmp_path: Path):
    """The other half of the same seam. `/ticket` is the end-to-end
    instruction a Worker follows; it went claim → work → review with no step
    that ran anything."""
    install_claude_config(tmp_path, "owner", _brief(), [], ProjectConfig())
    ticket = (tmp_path / ".claude" / "commands" / "ticket.md").read_text()
    assert "test and lint commands" in ticket
    assert "`Test:` and `Lint:`" in ticket
    # Before the review convention, not after it — a reviewer should not be
    # the first thing to run the suite.
    assert ticket.index("test and lint commands") < ticket.index("(`/review`)")


class TestAssignmentGuidance:
    """A tester's Owner session assigned one Worker per module and had to be
    corrected. Workers map to a workspace, not a module (SPEC §5.3.4), and
    the session only knows what this file tells it."""

    def _rendered(self, role: str) -> str:
        return _role_section(role, ProjectConfig())

    def test_the_owner_is_told_workers_are_not_module_scoped(self):
        text = self._rendered("owner")
        assert "interchangeable" in text
        assert "never keep one Worker per module" in text
        assert "rite claim" in text

    def test_the_manager_is_told_the_same(self):
        text = self._rendered("manager")
        assert "interchangeable" in text
        assert "one Worker per module" in text
