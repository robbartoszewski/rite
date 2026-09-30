"""What `rite init` leaves a project with, settled the same way on every route.

`run_init` has two routes to its answers (from scratch, and "existing spec or
code") and two ways of being run (interactive, and `--yes`), and before this
each gap was closed on the route where it was found. The v0.7.0 dogfood found
the rest: an existing-code path holding only the setup's `install.sh` was never
asked for the repository (S13), and a bare `rite init --yes` never reached any
module check at all, wrote `modules: {}` and said "Ready." (the `--yes` gap,
the same root). So these steps run after the answers, whichever way they came:

* **modules** (S13): zero modules is said, and asked about while someone is
  there to answer;
* **schedule** (S21): a fresh project's schedule is a stated default, never
  left empty (0 Workers, silently) and never filled in silently either;
* **namespace** (S18): credentials stored for the same repository before a
  reset are offered back instead of orphaned;
* **Worker** (S20): offered once there is a module to give it;
* **Manager** (S15): offered, and declared through `rite add manager`'s own
  `declare_manager`;
* and the last line says what is still missing, instead of "Ready.".

Each is asked interactively, taken from `--config` when the preset has it, and
under `--yes` takes the interactive default and says what it took. That is the
rule every `--yes` answer already follows (`questionnaire.offer_modules`).
"""

from __future__ import annotations

from pathlib import Path

import click

from rite_ai.config.models import ScheduleConfig, ScheduleWindow

from . import ui

ALL_DAY = "00:00-24:00"
DEFAULT_WORKERS = 1
DEFAULT_WORKER_NAME = "w1"

NO_MODULE = (
    "No module would be registered, so this project would have nothing to work "
    "on: a Worker's workspace is its modules' clones."
)
HOW_TO_ADD_A_MODULE = "`rite add module <name> <repository URL>`"


# --- modules (S13, and the --yes gap) ----------------------------------------


def settle_modules(root: Path, answers, interactive: bool) -> None:
    """Zero modules: say so, and ask for the repository if nobody has been
    asked yet. Whatever the route, and whatever else is in the directory."""
    from .detect import holds_files_but_no_repository
    from .questionnaire import ask_for_a_repository

    if answers.modules or answers.link is not None:
        return
    why = NO_MODULE
    if holds_files_but_no_repository(root):
        # S12's explanation, kept: the code is here, but a Worker clones.
        why += (
            f" {root} has code in it but is not a git repository, so there is "
            "nothing a Worker could clone: `git init` and commit, or name the "
            "repository it comes from."
        )
    ui.warn(why)
    if interactive and not answers.asked_for_code:
        answers.link = ask_for_a_repository()
        answers.asked_for_code = True


# --- schedule (S21) ----------------------------------------------------------


def _valid_zone(name: str) -> bool:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return bool(name)


def proposed_schedule() -> ScheduleConfig:
    """1 Worker, all day, every day, in this machine's zone. The zone is
    written down (not left "machine-local"), so a project moved to another
    machine keeps the hours it was given; a zone that cannot be named is
    left empty, which `resolve_zone` reads as machine-local and says so."""
    from rite_ai.schedule import machine_zone_name

    zone = machine_zone_name()
    return ScheduleConfig(
        timezone=zone if _valid_zone(zone) else "",
        windows=[ScheduleWindow(hours=ALL_DAY, workers=DEFAULT_WORKERS)],
    )


def describe_schedule(s: ScheduleConfig) -> str:
    w = s.windows[0]
    zone = (
        f"{s.timezone} (this machine's timezone)"
        if s.timezone
        else "this machine's clock (its zone could not be named)"
    )
    hours = "all day" if w.hours == ALL_DAY else w.hours
    return f"{w.workers} Worker(s) at a time, {hours}, every day, in {zone}"


def settle_schedule(answers, preset, interactive: bool) -> None:
    """Set a fresh project's schedule to a stated default, never leave it
    empty: an empty schedule authorises 0 Workers at every hour, which `rite
    start` found out only by refusing (S21). Said on every run, interactive
    or not, with how to change it; not asked, because one command changes it
    and init already asks enough. `--config` sets `schedule.timezone`,
    `schedule.workers` and `schedule.hours`."""
    schedule = answers.config.schedule
    if schedule.windows:
        return
    proposed = proposed_schedule()
    zone = preset.get("schedule.timezone")
    workers = preset.get("schedule.workers")
    hours = preset.get("schedule.hours")
    if zone is not None:
        proposed.timezone = str(zone)
    if workers is not None:
        proposed.windows[0].workers = int(workers)
    if hours is not None:
        proposed.windows[0].hours = str(hours)
    said = describe_schedule(proposed)
    how = "--config" if (zone, workers, hours) != (None, None, None) else "default"
    click.echo()
    click.echo(
        f"Schedule ({how}): {said}. Change the hours and Workers with `rite "
        "schedule set`, and the timezone in .rite/config.yaml."
    )
    answers.config.schedule = proposed


# --- the credential namespace (S18) -------------------------------------------


def remember_existing(rite_dir: Path) -> None:
    """Before a wipe: record which namespace the project being wiped used, by
    its modules' remotes, so the re-init can offer it back. Never raises."""
    from rite_ai.config.parse import ParseError, parse_config, parse_modules
    from rite_ai.credentials.store import remember_namespace

    config = parse_config(rite_dir / "config.yaml")
    modules = parse_modules(rite_dir / "modules.yaml")
    if isinstance(config, ParseError) or isinstance(modules, ParseError):
        return
    remember_namespace(config.credentials.namespace, [m.url for m in modules if m.url])


def settle_namespace(root: Path, answers, interactive: bool) -> None:
    """Offer the namespace credentials were stored under for this project's
    repository before, instead of a fresh one that orphans them (S18). The
    same project is the same module remote: the one identity that survives
    `rm -rf` and a fresh clone. Only a namespace that still holds a
    credential is offered; nothing is reused unless offered and taken."""
    from rite_ai.credentials.store import namespaces_for

    remotes = [m.url for m in answers.modules if m.url]
    matches = namespaces_for(remotes)
    if not matches:
        return
    best = matches[0]
    held = ", ".join(best.keys)
    others = (
        f" ({len(matches) - 1} older one(s) for it too, not offered)"
        if len(matches) > 1
        else ""
    )
    click.echo()
    click.echo(
        f"This repository's project stored credentials before, under namespace "
        f"{best.namespace}: {held}.{others} Reusing it keeps them; a new "
        "namespace starts empty and leaves those where no project reads them."
    )
    if interactive:
        reuse = ui.confirm(f"Reuse namespace {best.namespace}?", default=True)
    else:
        reuse = True
        click.echo(f"  --yes: reused namespace {best.namespace}")
    if reuse:
        answers.config.credentials.namespace = best.namespace


def remember_namespace(answers) -> None:
    from rite_ai.credentials.store import remember_namespace as remember

    problem = remember(
        answers.config.credentials.namespace,
        [m.url for m in answers.modules if m.url],
        answers.brief.name,
    )
    if problem:
        ui.note(problem)


# --- a Worker (S20) -------------------------------------------------------------


def _workers_declared(root: Path) -> list[str]:
    workers = root / "workers"
    if not workers.is_dir():
        return []
    return sorted(p.name for p in workers.iterdir() if (p / "worker.yml").is_file())


def offer_a_worker(root: Path, answers, preset, interactive: bool) -> str | None:
    """Offer to declare a Worker for the modules registered, and return its
    name, or the name of one already declared, or None. With no module there
    is nothing to give a Worker, so nothing is offered.

    ⚠ `--yes` does NOT declare one. A Worker clones every module, and an
    unattended init doing that by default would be the one `--yes` answer that
    reaches out over the network on its own; it is said instead, and
    `--config` with `workers.add: <name>` declares one."""
    from rite_ai.workspace import add_worker

    declared = _workers_declared(root)
    if declared:
        return declared[0]
    if not answers.modules:
        return None
    preset_name = preset.get("workers.add")
    if preset_name is not None:
        name = str(preset_name).strip() if preset_name is not False else ""
    elif interactive:
        click.echo()
        if not ui.confirm(
            "Declare a Worker now, so `rite start` has one to hand work to?",
            default=True,
        ):
            return None
        name = ui.text("Worker name?", default=DEFAULT_WORKER_NAME).strip()
    else:
        return None
    if not name:
        return None
    result = add_worker(root, name)
    if not result.ok:
        ui.warn(f"Worker '{name}' was NOT declared: {result.message}")
        return None
    ui.created(
        f"workers/{name}/ (Worker '{name}', {len(result.cloned_modules)} module(s))"
    )
    for module, why in result.failed_modules:
        ui.warn(f"  {module} was not cloned into workers/{name}/: {why}")
    return name


# --- a Manager (S15) ------------------------------------------------------------

DEFAULT_MANAGER = {"owner": ("lead", "lead"), "manager": ("executor", "executor")}
"""(name, preset) offered by default, by this machine's role: the Owner machine
runs the Manager that owns the board; a Manager machine one that executes."""


def offer_a_manager(answers, preset, interactive: bool) -> str | None:
    """Offer to declare a Manager, so `rite start <name>` has one to start
    (S15). Declared through S16's `declare_manager`, the one writer of
    `coordination.managers` and `manager_roles` and the parser's own
    validation, never a second copy of either. Returns the name declared, or
    one already declared, or None.

    Default yes interactively. ⚠ **`--yes` declares none**, and the last line
    says one is missing and how to add it: a declared Manager makes `rite
    doctor` report this one-machine project as uncoordinated (no `remote`, no
    `.rite/machine`), so declaring one by default would fail every scripted
    init's first doctor. `--config` sets `managers.add: <name>` (or `false`)
    and `managers.preset`, and declares it."""
    from rite_ai.config.managers import PRESETS, declare_manager

    coordination = answers.config.coordination
    if coordination.managers:
        return coordination.managers[0]
    name, chosen = DEFAULT_MANAGER.get(answers.role, DEFAULT_MANAGER["owner"])
    asked_name = preset.get("managers.add")
    asked_preset = preset.get("managers.preset")
    if asked_name is not None or asked_preset is not None:
        if asked_name is False:
            return None
        name = str(asked_name or name).strip()
        chosen = str(asked_preset or chosen).strip()
        how = "--config"
    elif interactive:
        click.echo()
        what = ", ".join(PRESETS[chosen])
        if not ui.confirm(
            f"Declare Manager '{name}' (preset {chosen}: {what}), so `rite start "
            f"{name}` has one to run?",
            default=True,
        ):
            name = ui.text(
                "Another Manager's name, or Enter to declare none", default=""
            ).strip()
            if not name:
                return None
            chosen = _ask_preset()
        how = ""
    else:
        return None
    declared = declare_manager(
        coordination.managers, coordination.manager_roles, name, preset=chosen
    )
    if declared.error:
        ui.warn(f"Manager '{name}' was NOT declared: {declared.error}")
        return None
    coordination.managers = declared.names
    coordination.manager_roles = declared.roles
    said = f"Manager '{name}' (preset {chosen})"
    click.echo(f"  {how}: declared {said}" if how else f"  Declared {said}.")
    return name


def _ask_preset() -> str:
    from rite_ai.config.managers import PRESETS

    names = sorted(PRESETS)
    while True:
        answer = ui.text(f"Its preset ({', '.join(names)})", default="lead").strip()
        if answer in PRESETS:
            return answer
        ui.warn(f"{answer!r} is not a preset: one of {', '.join(names)}.")


# --- the last line ------------------------------------------------------------


def what_is_missing(root: Path, answers, worker: str | None) -> list[str]:
    missing = []
    if not answers.config.coordination.managers:
        missing.append(
            "no Manager is declared, so `rite start` has nothing to start: "
            "`rite add manager lead --preset lead`"
        )
    if not answers.modules:
        missing.append(f"no module is registered: add one with {HOW_TO_ADD_A_MODULE}")
    elif worker is None:
        missing.append(
            f"no Worker is declared, so `rite start` would wait for one forever: "
            f"`rite add worker {DEFAULT_WORKER_NAME}`"
        )
    return missing


def not_ready(missing: list[str]) -> str:
    return "Initialised, but NOT ready for work: " + "; ".join(missing) + "."
