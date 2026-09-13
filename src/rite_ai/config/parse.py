"""Parse and validate rite config files from a project directory."""

from __future__ import annotations

import dataclasses
import difflib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import (
    BudgetConfig,
    CredentialsConfig,
    ExpertiseEntry,
    HeartbeatConfig,
    Module,
    PoolConfig,
    ProjectBrief,
    ProjectConfig,
    PublishGateConfig,
    RecordedCommands,
    RiteProject,
    SandboxConfig,
    ScanPattern,
    ScheduleConfig,
    ScheduleWindow,
    SpecConfig,
    TicketBackendConfig,
    WatchdogConfig,
    WorkerManifest,
)


@dataclass
class ParseError:
    file: str
    message: str


def parse_brief(path: Path) -> ProjectBrief | ParseError:
    if not path.exists():
        return ParseError(str(path), "file not found")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return ParseError(str(path), f"invalid YAML: {e}")

    if not isinstance(raw, dict):
        return ParseError(str(path), "expected a YAML mapping at top level")

    project = raw.get("project", {})
    if not isinstance(project, dict):
        return ParseError(str(path), "'project' must be a mapping")

    name = project.get("name", "")
    if not name:
        return ParseError(str(path), "'project.name' is required")

    role = project.get("role", "owner")
    if role not in ("owner", "manager"):
        return ParseError(
            str(path),
            f"'project.role' must be 'owner' or 'manager', got '{role}'",
        )

    what = raw.get("what", {})
    if not isinstance(what, dict):
        what = {}

    tech = raw.get("technology", {})
    if not isinstance(tech, dict):
        tech = {}

    return ProjectBrief(
        name=name,
        role=role,
        root_branch=project.get("root_branch", "main"),
        kind=what.get("kind", ""),
        features=what.get("features", ""),
        platform=tech.get("platform", ""),
        languages=_str_list(tech.get("languages", [])),
        frameworks=_str_list(tech.get("frameworks", [])),
        architecture=tech.get("architecture", ""),
    )


# The keys a module entry may carry, taken from the dataclasses so a new field
# needs no second edit here. `name` is the mapping key, not a key inside it.
#
# Unknown keys are REFUSED, not ignored. An ignored key is a setting its author
# believes they made, and in `commands:` it is worse than that: `tests:` for
# `test:` leaves a Worker with no way to check its work while the file looks
# configured. `test: swift test` written under a module used to be read,
# discarded, and dropped from the file by the next `rite add module` — the
# silent partial read CFG-1 describes for config.yaml and brief.yaml.
#
# This file can be strict first at no cost to anyone upgrading: a v0.1.0
# modules.yaml carries no key outside this set (measured against a pristine
# `rite init --yes`), so there is nothing to retire. Every writer re-parses the
# file and stops on a ParseError before writing, so a refused key is never
# overwritten.
_MODULE_KEYS = frozenset(f.name for f in dataclasses.fields(Module)) - {"name"}
_COMMAND_KEYS = frozenset(f.name for f in dataclasses.fields(RecordedCommands))


def _unknown_key(entry: dict, known: frozenset[str]) -> str:
    """A message naming the first key not in `known`, or "" when there is none."""
    for key in entry:
        if key in known:
            continue
        if known is _MODULE_KEYS and key in _COMMAND_KEYS:
            hint = f" — commands go under 'commands:', as commands: {{{key}: ...}}"
        else:
            close = difflib.get_close_matches(str(key), sorted(known), n=1)
            hint = f" — did you mean '{close[0]}'?" if close else ""
        return f"unknown key '{key}'{hint} (known: {', '.join(sorted(known))})"
    return ""


def _parse_recorded_commands(raw: object) -> RecordedCommands | str:
    """The `commands:` mapping, or a message saying what is wrong with it."""
    if raw is None:
        return RecordedCommands()
    if not isinstance(raw, dict):
        known = ", ".join(sorted(_COMMAND_KEYS))
        return f"'commands' must be a mapping of {known} to a command"
    unknown = _unknown_key(raw, _COMMAND_KEYS)
    if unknown:
        return f"commands: {unknown}"
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            return f"commands.{key} must be a non-empty string"
    return RecordedCommands(**{key: value.strip() for key, value in raw.items()})


def parse_modules(path: Path) -> list[Module] | ParseError:
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return ParseError(str(path), f"invalid YAML: {e}")

    if not isinstance(raw, dict):
        return ParseError(str(path), "expected a YAML mapping at top level")

    modules_raw = raw.get("modules", {})
    if not isinstance(modules_raw, dict):
        return ParseError(str(path), "'modules' must be a mapping")

    modules: list[Module] = []
    seen: set[str] = set()
    for name, entry in modules_raw.items():
        if not isinstance(entry, dict):
            return ParseError(str(path), f"module '{name}' must be a mapping")
        if name in seen:
            return ParseError(str(path), f"duplicate module name: '{name}'")
        seen.add(name)

        unknown = _unknown_key(entry, _MODULE_KEYS)
        if unknown:
            return ParseError(str(path), f"module '{name}': {unknown}")

        mod_path = entry.get("path", "")
        if not mod_path:
            return ParseError(str(path), f"module '{name}' requires 'path'")

        commands = _parse_recorded_commands(entry.get("commands"))
        if isinstance(commands, str):
            return ParseError(str(path), f"module '{name}': {commands}")

        modules.append(
            Module(
                name=name,
                path=mod_path,
                url=entry.get("url"),
                branch=entry.get("branch", "main"),
                description=entry.get("description", ""),
                commands=commands,
            )
        )
    return modules


def parse_config(path: Path) -> ProjectConfig | ParseError:
    if not path.exists():
        return ProjectConfig()
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return ParseError(str(path), f"invalid YAML: {e}")

    if not isinstance(raw, dict):
        return ParseError(str(path), "expected a YAML mapping at top level")

    tb_raw = raw.get("ticket_backend", {})
    ticket_backend = (
        TicketBackendConfig(
            type=tb_raw.get("type", "none"),
            site=tb_raw.get("site", ""),
            repo=tb_raw.get("repo", ""),
            projects=tb_raw.get("projects", {}),
            credential=tb_raw.get("credential", ""),
        )
        if isinstance(tb_raw, dict)
        else TicketBackendConfig()
    )

    # A malformed `credentials` block narrows to the default (no
    # namespace -> bare keys -> the pre-namespacing layout) rather than
    # failing the parse, for the reason the schedule block below records:
    # `parse_config` raising takes out EVERY command that reads
    # config.yaml, including the ones that would repair it. `rite
    # credential list` is the surfacing point instead.
    cred_raw = raw.get("credentials", {})
    credentials = (
        CredentialsConfig(namespace=str(cred_raw.get("namespace", "") or ""))
        if isinstance(cred_raw, dict)
        else CredentialsConfig()
    )

    expertise: list[ExpertiseEntry] = []
    exp_raw = raw.get("expertise", {})
    if isinstance(exp_raw, dict):
        for name, entry in exp_raw.items():
            if isinstance(entry, dict):
                tags = _str_list(entry.get("tags", []))
                if not tags:
                    return ParseError(str(path), f"expertise '{name}' has empty tags")
                expertise.append(ExpertiseEntry(name=name, tags=tags))

    pg_raw = raw.get("publish_gate", {})
    patterns: list[ScanPattern] = []
    if isinstance(pg_raw, dict):
        for p in pg_raw.get("scan_patterns", []):
            if isinstance(p, dict):
                patterns.append(
                    ScanPattern(
                        type=p.get("type", "regex"),
                        pattern=p.get("pattern", ""),
                        description=p.get("description", ""),
                    )
                )

    hb_raw = raw.get("heartbeat", {})
    heartbeat = (
        HeartbeatConfig(
            interval_minutes=hb_raw.get("interval_minutes", 10),
            stall_threshold=hb_raw.get("stall_threshold", 3),
        )
        if isinstance(hb_raw, dict)
        else HeartbeatConfig()
    )

    wd_raw = raw.get("watchdog", {})
    watchdog = WatchdogConfig(
        interval_minutes=wd_raw.get("interval_minutes", 5)
        if isinstance(wd_raw, dict)
        else 5
    )

    pool_raw = raw.get("pool", {})
    pool = (
        PoolConfig(
            coordinator_standby=pool_raw.get("coordinator_standby", 2),
            warn_threshold=pool_raw.get("warn_threshold", 0.5),
            lease_expiry_minutes=pool_raw.get("lease_expiry_minutes", 15),
            archive_after_minutes=pool_raw.get("archive_after_minutes", 30),
        )
        if isinstance(pool_raw, dict)
        else PoolConfig()
    )

    sb_raw = raw.get("sandbox", {})
    spec_raw = raw.get("spec", {})
    spec = (
        SpecConfig(
            paths=_str_list(spec_raw.get("paths", [])),
            convention=str(spec_raw.get("convention", "") or ""),
        )
        if isinstance(spec_raw, dict)
        else SpecConfig()
    )

    sandbox = (
        SandboxConfig(
            enabled=sb_raw.get("enabled", True),
            backend=sb_raw.get("backend", "seatbelt"),
            token_permissions=_str_list(
                sb_raw.get("token_permissions", ["contents", "pull_requests"])
            ),
            max_concurrent_workers=sb_raw.get("max_concurrent_workers", 5),
        )
        if isinstance(sb_raw, dict)
        else SandboxConfig()
    )

    budget_raw = raw.get("budget", {})
    budget = (
        BudgetConfig(
            weekly_quota_pct=budget_raw.get("weekly_quota_pct", 95),
            weekly_token_budget=budget_raw.get("weekly_token_budget"),
            week_start_day=budget_raw.get("week_start_day", "monday"),
        )
        if isinstance(budget_raw, dict)
        else BudgetConfig()
    )

    # A missing schedule.timezone with windows present is a real problem
    # (D-48) but is deliberately NOT a ParseError here: `parse_config`
    # failing means EVERY command that touches config.yaml breaks, not
    # just scheduling ones — including `rite schedule set-timezone`
    # itself, which would then have no way to read the file it exists to
    # fix. This exact self-lockout was measured: `rite schedule set` can
    # itself produce a window-before-timezone config.yaml, and a hard
    # parse error here made the very next command (even the one meant to
    # fix it) refuse to run. `rite_ai.schedule.validate_schedule` is the
    # actual enforcement point (surfaced by `rite schedule set` and,
    # eventually, `rite doctor`) — a warning a human can act on, not a
    # lockout nothing can act through.
    sched_raw = raw.get("schedule", {})
    windows: list[ScheduleWindow] = []
    schedule_timezone = ""
    if isinstance(sched_raw, dict):
        schedule_timezone = sched_raw.get("timezone", "") or ""
        for w in sched_raw.get("windows", []):
            if not isinstance(w, dict):
                continue
            hours = w.get("hours", "")
            if not hours:
                return ParseError(str(path), "schedule window missing 'hours'")
            windows.append(
                ScheduleWindow(hours=hours, workers=int(w.get("workers", 0)))
            )
    schedule = ScheduleConfig(timezone=schedule_timezone, windows=windows)

    return ProjectConfig(
        ticket_backend=ticket_backend,
        credentials=credentials,
        expertise=expertise,
        publish_gate=PublishGateConfig(
            scan_patterns=patterns,
            gitleaks_config=pg_raw.get("gitleaks_config", ".rite/gitleaks.toml")
            if isinstance(pg_raw, dict)
            else ".rite/gitleaks.toml",
        ),
        heartbeat=heartbeat,
        watchdog=watchdog,
        pool=pool,
        sandbox=sandbox,
        budget=budget,
        schedule=schedule,
        spec=spec,
    )


def parse_worker(path: Path) -> WorkerManifest | ParseError:
    if not path.exists():
        return ParseError(str(path), "file not found")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return ParseError(str(path), f"invalid YAML: {e}")

    if not isinstance(raw, dict):
        return ParseError(str(path), "expected a YAML mapping at top level")

    worker = raw.get("worker", {})
    if not isinstance(worker, dict):
        return ParseError(str(path), "'worker' must be a mapping")

    name = worker.get("name", "")
    if not name:
        return ParseError(str(path), "'worker.name' is required")

    return WorkerManifest(
        name=name,
        manager=worker.get("manager", ""),
        modules=_str_list(worker.get("modules", [])),
        claude_instructions=worker.get("claude_instructions", ""),
    )


def load_project(root: Path) -> RiteProject | list[ParseError]:
    """Load a full rite project from the given root directory."""
    rite_dir = root / ".rite"
    errors: list[ParseError] = []

    brief = parse_brief(rite_dir / "brief.yaml")
    if isinstance(brief, ParseError):
        errors.append(brief)

    modules = parse_modules(rite_dir / "modules.yaml")
    if isinstance(modules, ParseError):
        errors.append(modules)

    config = parse_config(rite_dir / "config.yaml")
    if isinstance(config, ParseError):
        errors.append(config)

    if errors:
        return errors

    assert isinstance(brief, ProjectBrief)
    assert isinstance(modules, list)
    assert isinstance(config, ProjectConfig)

    # Validate module names are unique (already checked in parse_modules)
    module_names = {m.name for m in modules}

    # Load workers
    workers: list[WorkerManifest] = []
    workers_dir = root / "workers"
    if workers_dir.is_dir():
        worker_names: set[str] = set()
        for worker_dir in sorted(workers_dir.iterdir()):
            manifest_path = worker_dir / "worker.yml"
            if manifest_path.exists():
                worker = parse_worker(manifest_path)
                if isinstance(worker, ParseError):
                    errors.append(worker)
                    continue
                if worker.name in worker_names:
                    errors.append(
                        ParseError(
                            str(manifest_path),
                            f"duplicate worker name: '{worker.name}'",
                        )
                    )
                    continue
                worker_names.add(worker.name)
                # Validate worker modules reference existing modules
                for mod in worker.modules:
                    if mod not in module_names:
                        errors.append(
                            ParseError(
                                str(manifest_path),
                                f"worker '{worker.name}' references "
                                f"unknown module '{mod}'",
                            )
                        )
                workers.append(worker)

    if errors:
        return errors

    return RiteProject(
        root=root,
        brief=brief,
        modules=modules,
        config=config,
        workers=workers,
    )


def _str_list(val: object) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    return []
