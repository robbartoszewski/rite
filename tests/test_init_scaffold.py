import subprocess
from pathlib import Path

import yaml

from rite_ai.cli.init import scaffold
from rite_ai.cli.init.questionnaire import KbAnswers
from rite_ai.config.models import (
    Module,
    ProjectBrief,
    ProjectConfig,
    TicketBackendConfig,
)
from rite_ai.config.parse import parse_brief, parse_config, parse_modules


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


def _ignored(root: Path, relpath: str) -> bool:
    """Does git itself ignore this path? Asserting on the presence of a
    literal line in .gitignore asserts the mechanism; this asserts the
    effect, which is the thing that matters and the thing that silently
    changed when `.rite/handover.json` became `.rite/handover/`."""
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("x")
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", relpath],
            cwd=root,
            capture_output=True,
        ).returncode
        == 0
    )


def _git_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_write_brief_roundtrips_through_parser(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    brief = _brief(architecture="event sourcing, CQRS")
    path = scaffold.write_brief(rite_dir, brief)

    parsed = parse_brief(path)
    assert isinstance(parsed, ProjectBrief)
    assert parsed.name == "acme"
    assert parsed.architecture == "event sourcing, CQRS"
    assert parsed.languages == ["python"]


def test_write_modules_roundtrips(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    modules = [
        Module(
            name="backend",
            path="backend/",
            url="git@x:o/b.git",
            branch="main",
            description="API",
        ),
        Module(name="shared", path="shared/", url=None, branch="main", description=""),
    ]
    path = scaffold.write_modules(rite_dir, modules)
    parsed = parse_modules(path)
    assert isinstance(parsed, list)
    names = {m.name for m in parsed}
    assert names == {"backend", "shared"}
    backend = next(m for m in parsed if m.name == "backend")
    assert backend.url == "git@x:o/b.git"
    shared = next(m for m in parsed if m.name == "shared")
    assert shared.url is None


def test_write_config_roundtrips(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    config = ProjectConfig(
        ticket_backend=TicketBackendConfig(
            type="jira", site="team.atlassian.net", projects={}, credential="jira_token"
        )
    )
    path = scaffold.write_config(rite_dir, config)
    parsed = parse_config(path)
    assert isinstance(parsed, ProjectConfig)
    assert parsed.ticket_backend.type == "jira"
    assert parsed.ticket_backend.site == "team.atlassian.net"


def test_write_config_roundtrips_new_sections(tmp_path: Path):
    """`config_to_yaml` must serialise every field — a section it drops is
    a section silently deleted the next time anything (e.g. `rite schedule
    set`) reads-modifies-writes config.yaml."""
    from rite_ai.config.models import (
        BudgetConfig,
        PoolConfig,
        SandboxConfig,
        ScheduleConfig,
        ScheduleWindow,
        WatchdogConfig,
    )

    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    config = ProjectConfig(
        watchdog=WatchdogConfig(interval_minutes=7),
        pool=PoolConfig(coordinator_standby=4, warn_threshold=0.3),
        sandbox=SandboxConfig(
            enabled=True,
            backend="docker",
            token_permissions=["contents"],
            max_concurrent_workers=9,
        ),
        budget=BudgetConfig(weekly_quota_pct=80),
        schedule=ScheduleConfig(
            timezone="Europe/Warsaw",
            windows=[ScheduleWindow(hours="09:00-18:00", workers=3)],
        ),
    )
    path = scaffold.write_config(rite_dir, config)
    parsed = parse_config(path)
    assert isinstance(parsed, ProjectConfig)
    assert parsed.watchdog.interval_minutes == 7
    assert parsed.pool.coordinator_standby == 4
    assert parsed.sandbox.enabled is True
    assert parsed.sandbox.backend == "docker"
    assert parsed.budget.weekly_quota_pct == 80
    assert parsed.schedule.timezone == "Europe/Warsaw"
    assert len(parsed.schedule.windows) == 1
    assert parsed.schedule.windows[0].hours == "09:00-18:00"


def test_write_context_index_is_empty_table(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    path = scaffold.write_context_index(rite_dir)
    assert "When to consult" in path.read_text()


def test_write_review_checklist_copies_default(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    path = scaffold.write_review_checklist(rite_dir)
    assert "Security" in path.read_text()
    assert "Correctness" in path.read_text()


def test_write_kb_copies_authored_file_and_records_link(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    project_root = tmp_path
    source_file = project_root / "notes.md"
    source_file.write_text("some standard\n")

    kb = KbAnswers(links=["https://example.com/std"], files=["notes.md"], commit=True)
    index_path, count = scaffold.write_kb(rite_dir, project_root, kb)

    assert count == 2
    assert (rite_dir / "kb" / "notes.md").read_text() == "some standard\n"
    text = index_path.read_text()
    assert "https://example.com/std" in text
    assert "notes.md" in text
    assert (rite_dir / "kb" / ".cache").is_dir()


def test_write_kb_missing_file_is_noted_not_raised(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    kb = KbAnswers(links=[], files=["does-not-exist.md"], commit=True)
    index_path, count = scaffold.write_kb(rite_dir, tmp_path, kb)
    assert count == 1
    assert "not found" in index_path.read_text()


def test_update_gitignore_noop_without_git(tmp_path: Path):
    scaffold.update_gitignore(tmp_path, kb_commit=True)
    assert not (tmp_path / ".gitignore").exists()


def test_fetched_kb_caches_are_ignored_but_authored_kb_is_not(tmp_path: Path):
    root = _git_project(tmp_path)
    scaffold.update_gitignore(root, kb_commit=True)
    assert _ignored(root, ".rite/kb/.cache/example-com.md")
    assert not _ignored(root, ".rite/kb/INDEX.md")


def test_update_gitignore_excludes_the_lock_sidecars(tmp_path: Path):
    """`rite_ai.state.locked` creates `<file>.lock` beside every piece of
    shared state it guards. They are per-machine, contentless, and
    recreated on demand — a scaffolded project should not be committing
    them."""
    root = _git_project(tmp_path)
    scaffold.update_gitignore(root, kb_commit=True)
    assert _ignored(root, ".rite/claims.json.lock")


def test_update_gitignore_excludes_whole_kb_when_not_committed(tmp_path: Path):
    root = _git_project(tmp_path)
    scaffold.update_gitignore(root, kb_commit=False)
    assert _ignored(root, ".rite/kb/INDEX.md")


def test_update_gitignore_idempotent(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    scaffold.update_gitignore(tmp_path, kb_commit=True)
    scaffold.update_gitignore(tmp_path, kb_commit=True)
    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".rite/kb/.cache/") == 1


def test_install_pre_push_hooks_root_and_modules(tmp_path: Path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    module_dir = tmp_path / "backend"
    (module_dir / ".git" / "hooks").mkdir(parents=True)

    installed = scaffold.install_pre_push_hooks(
        tmp_path, [Module(name="backend", path="backend/", url=None, branch="main")]
    )
    assert len(installed) == 2
    hook = (tmp_path / ".git" / "hooks" / "pre-push").read_text()
    # Range-scoped `pre-push` (Stage 2 #3), not the unscoped full-history
    # `publish check` this used to exec — and via the pipx-safe console
    # script, not `python3 -m rite_ai.gate`.
    assert "rite publish pre-push" in hook
    assert "python3 -m rite_ai.gate" not in hook


def test_install_pre_push_hooks_does_not_clobber_existing(tmp_path: Path):
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-push").write_text("#!/bin/sh\necho custom\n")

    installed = scaffold.install_pre_push_hooks(tmp_path, [])
    assert installed == []
    assert "custom" in (hooks_dir / "pre-push").read_text()


def test_install_pre_push_hooks_upgrades_the_old_marker(tmp_path: Path):
    """A hook installed by a pre-consolidation `rite init` carries the old
    marker ("# rite: publish gate") — it must be upgraded, not refused as
    foreign."""
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-push").write_text(
        "#!/bin/sh\n# rite: publish gate — installed by `rite init`.\n"
        "exec rite publish check\n"
    )

    installed = scaffold.install_pre_push_hooks(tmp_path, [])
    assert installed == [str(tmp_path)]
    hook = (hooks_dir / "pre-push").read_text()
    assert "rite publish pre-push" in hook


def test_config_yaml_key_order_matches_spec(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    path = scaffold.write_config(rite_dir, ProjectConfig())
    data = yaml.safe_load(path.read_text())
    assert list(data.keys()) == [
        "ticket_backend",
        "credentials",
        "expertise",
        "publish_gate",
        "spec",
        "heartbeat",
        "watchdog",
        "pool",
        "sandbox",
        "budget",
        "coordination",
        "schedule",
        # ⚠ APPENDED, not inserted. This list is the documented key order, and
        # a new section goes at the END so an existing project's config.yaml
        # is not reordered the next time anything rewrites it — a reordered
        # file is a diff nobody asked for in a file people hand-edit.
        "slack",
        "checkins",
        "github_app",
    ]


def test_install_pre_push_hooks_skips_repos_whose_hooks_are_redirected(
    tmp_path: Path,
):
    """`core.hooksPath` means git never reads `.git/hooks`, so a hook written
    there is not installed in any sense that matters — `init` must not count
    it. It warns about this instead; counting it produced the "✓ Created
    .git/hooks/pre-push" that a cold rehearsal caught lying, on a machine
    where a push carrying planted secrets then sailed through the gate."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", str(elsewhere)],
        cwd=tmp_path,
        check=True,
    )

    installed = scaffold.install_pre_push_hooks(tmp_path, [])

    assert installed == []
    assert not (tmp_path / ".git" / "hooks" / "pre-push").exists()
