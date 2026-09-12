from pathlib import Path

from rite_ai.config.models import (
    ProjectBrief,
    ProjectConfig,
    RiteProject,
    WorkerManifest,
)
from rite_ai.config.parse import (
    ParseError,
    load_project,
    parse_brief,
    parse_config,
    parse_modules,
    parse_worker,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- brief.yaml ---


def test_parse_brief_minimal(tmp_path: Path):
    _write(tmp_path / "brief.yaml", "project:\n  name: test-proj\n")
    result = parse_brief(tmp_path / "brief.yaml")
    assert isinstance(result, ProjectBrief)
    assert result.name == "test-proj"
    assert result.role == "owner"
    assert result.root_branch == "main"


def test_parse_brief_full(tmp_path: Path):
    _write(
        tmp_path / "brief.yaml",
        """\
project:
  name: my-app
  role: manager
  root_branch: develop
what:
  kind: full-stack
  features: "A web app"
technology:
  platform: linux
  languages: [python, typescript]
  frameworks: [fastify]
  architecture: "event sourcing"
""",
    )
    result = parse_brief(tmp_path / "brief.yaml")
    assert isinstance(result, ProjectBrief)
    assert result.name == "my-app"
    assert result.role == "manager"
    assert result.root_branch == "develop"
    assert result.kind == "full-stack"
    assert result.languages == ["python", "typescript"]
    assert result.architecture == "event sourcing"


def test_parse_brief_missing_name(tmp_path: Path):
    _write(tmp_path / "brief.yaml", "project:\n  role: owner\n")
    result = parse_brief(tmp_path / "brief.yaml")
    assert isinstance(result, ParseError)
    assert "name" in result.message


def test_parse_brief_bad_role(tmp_path: Path):
    _write(tmp_path / "brief.yaml", "project:\n  name: x\n  role: admin\n")
    result = parse_brief(tmp_path / "brief.yaml")
    assert isinstance(result, ParseError)
    assert "role" in result.message


def test_parse_brief_missing_file(tmp_path: Path):
    result = parse_brief(tmp_path / "nonexistent.yaml")
    assert isinstance(result, ParseError)
    assert "not found" in result.message


def test_parse_brief_invalid_yaml(tmp_path: Path):
    _write(tmp_path / "brief.yaml", ":\n  :\n  ][")
    result = parse_brief(tmp_path / "brief.yaml")
    assert isinstance(result, ParseError)
    assert "YAML" in result.message


# --- modules.yaml ---


def test_parse_modules_basic(tmp_path: Path):
    _write(
        tmp_path / "modules.yaml",
        """\
modules:
  backend:
    path: backend/
    url: git@github.com:org/backend.git
    description: "API server"
  frontend:
    path: frontend/
""",
    )
    result = parse_modules(tmp_path / "modules.yaml")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].name == "backend"
    assert result[0].url == "git@github.com:org/backend.git"
    assert result[1].name == "frontend"
    assert result[1].url is None


def test_parse_modules_missing_path(tmp_path: Path):
    _write(tmp_path / "modules.yaml", "modules:\n  bad:\n    description: oops\n")
    result = parse_modules(tmp_path / "modules.yaml")
    assert isinstance(result, ParseError)
    assert "path" in result.message


def test_parse_modules_missing_file(tmp_path: Path):
    result = parse_modules(tmp_path / "nonexistent.yaml")
    assert isinstance(result, list)
    assert len(result) == 0


# --- config.yaml ---


def test_parse_config_full(tmp_path: Path):
    _write(
        tmp_path / "config.yaml",
        """\
ticket_backend:
  type: jira
  site: team.atlassian.net
  projects:
    board: PROJ
  credential: jira_token
expertise:
  alice:
    tags: [engineering, infra]
publish_gate:
  scan_patterns:
    - type: regex
      pattern: "VI [A-Z]+"
      description: "Record reference"
heartbeat:
  interval_minutes: 5
  stall_threshold: 2
""",
    )
    result = parse_config(tmp_path / "config.yaml")
    assert isinstance(result, ProjectConfig)
    assert result.ticket_backend.type == "jira"
    assert result.ticket_backend.site == "team.atlassian.net"
    assert len(result.expertise) == 1
    assert result.expertise[0].tags == ["engineering", "infra"]
    assert len(result.publish_gate.scan_patterns) == 1
    assert result.heartbeat.interval_minutes == 5


def test_parse_config_defaults(tmp_path: Path):
    result = parse_config(tmp_path / "nonexistent.yaml")
    assert isinstance(result, ProjectConfig)
    assert result.ticket_backend.type == "none"
    assert result.heartbeat.interval_minutes == 10
    assert result.watchdog.interval_minutes == 5
    assert result.pool.coordinator_standby == 2
    assert result.pool.lease_expiry_minutes == 15
    assert result.pool.archive_after_minutes == 30
    assert result.sandbox.enabled is False
    assert result.sandbox.backend == "seatbelt"
    assert result.budget.weekly_quota_pct == 95
    assert result.schedule.timezone == ""
    assert result.schedule.windows == []


def test_parse_config_new_sections(tmp_path: Path):
    _write(
        tmp_path / "config.yaml",
        """\
watchdog:
  interval_minutes: 5
pool:
  coordinator_standby: 3
  warn_threshold: 0.4
  lease_expiry_minutes: 20
  archive_after_minutes: 45
sandbox:
  enabled: true
  backend: docker
  token_permissions: [contents]
  max_concurrent_workers: 8
budget:
  weekly_quota_pct: 90
schedule:
  timezone: Europe/Warsaw
  windows:
    - hours: "09:00-18:00"
      workers: 3
    - hours: "18:00-09:00"
      workers: 0
""",
    )
    result = parse_config(tmp_path / "config.yaml")
    assert isinstance(result, ProjectConfig)
    assert result.pool.coordinator_standby == 3
    assert result.pool.lease_expiry_minutes == 20
    assert result.pool.archive_after_minutes == 45
    assert result.sandbox.enabled is True
    assert result.sandbox.backend == "docker"
    assert result.sandbox.token_permissions == ["contents"]
    assert result.sandbox.max_concurrent_workers == 8
    assert result.budget.weekly_quota_pct == 90
    assert result.schedule.timezone == "Europe/Warsaw"
    assert len(result.schedule.windows) == 2
    assert result.schedule.windows[0].hours == "09:00-18:00"
    assert result.schedule.windows[0].workers == 3
    assert result.schedule.windows[1].workers == 0


def test_parse_config_schedule_window_without_timezone_parses_but_is_flagged(
    tmp_path: Path,
):
    """D-48 (missing timezone with windows present) is a real problem, but
    deliberately NOT a ParseError: `parse_config` failing breaks every
    command that touches config.yaml, including `rite schedule
    set-timezone` — the one command that would need to read this exact
    file to fix it. `rite_ai.schedule.validate_schedule` is the real
    enforcement point; this just confirms parsing itself still succeeds."""
    _write(
        tmp_path / "config.yaml",
        "schedule:\n  windows:\n    - hours: '09:00-18:00'\n      workers: 3\n",
    )
    result = parse_config(tmp_path / "config.yaml")
    assert isinstance(result, ProjectConfig)
    assert result.schedule.timezone == ""
    assert len(result.schedule.windows) == 1

    from rite_ai.schedule import validate_schedule

    problems = validate_schedule(result.schedule, max_concurrent_workers=5)
    assert any("timezone" in p for p in problems)


def test_parse_config_schedule_window_missing_hours_is_an_error(tmp_path: Path):
    _write(
        tmp_path / "config.yaml",
        "schedule:\n  timezone: UTC\n  windows:\n    - workers: 3\n",
    )
    result = parse_config(tmp_path / "config.yaml")
    assert not isinstance(result, ProjectConfig)


def test_parse_config_empty_expertise_tags(tmp_path: Path):
    _write(
        tmp_path / "config.yaml",
        "expertise:\n  alice:\n    tags: []\n",
    )
    result = parse_config(tmp_path / "config.yaml")
    assert isinstance(result, ParseError)
    assert "empty tags" in result.message


# --- worker.yml ---


def test_parse_worker(tmp_path: Path):
    _write(
        tmp_path / "worker.yml",
        """\
worker:
  name: alpha
  manager: alice
  modules:
    - backend
    - frontend
  claude_instructions: "You are Worker alpha."
""",
    )
    result = parse_worker(tmp_path / "worker.yml")
    assert isinstance(result, WorkerManifest)
    assert result.name == "alpha"
    assert result.manager == "alice"
    assert result.modules == ["backend", "frontend"]


def test_parse_worker_missing_name(tmp_path: Path):
    _write(tmp_path / "worker.yml", "worker:\n  manager: bob\n")
    result = parse_worker(tmp_path / "worker.yml")
    assert isinstance(result, ParseError)
    assert "name" in result.message


# --- load_project ---


def test_load_project_minimal(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    _write(rite_dir / "brief.yaml", "project:\n  name: test\n")
    result = load_project(tmp_path)
    assert isinstance(result, RiteProject)
    assert result.brief.name == "test"
    assert result.modules == []
    assert result.workers == []


def test_load_project_with_workers(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    _write(rite_dir / "brief.yaml", "project:\n  name: test\n")
    _write(
        rite_dir / "modules.yaml",
        "modules:\n  backend:\n    path: backend/\n",
    )
    workers_dir = tmp_path / "workers" / "alpha"
    _write(
        workers_dir / "worker.yml",
        "worker:\n  name: alpha\n  modules: [backend]\n",
    )
    result = load_project(tmp_path)
    assert isinstance(result, RiteProject)
    assert len(result.workers) == 1
    assert result.workers[0].name == "alpha"


def test_load_project_worker_unknown_module(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    _write(rite_dir / "brief.yaml", "project:\n  name: test\n")
    _write(
        rite_dir / "modules.yaml",
        "modules:\n  backend:\n    path: backend/\n",
    )
    workers_dir = tmp_path / "workers" / "alpha"
    _write(
        workers_dir / "worker.yml",
        "worker:\n  name: alpha\n  modules: [nonexistent]\n",
    )
    result = load_project(tmp_path)
    assert isinstance(result, list)
    assert any("unknown module" in e.message for e in result)


def test_load_project_missing_brief(tmp_path: Path):
    result = load_project(tmp_path)
    assert isinstance(result, list)
    assert any("not found" in e.message for e in result)
