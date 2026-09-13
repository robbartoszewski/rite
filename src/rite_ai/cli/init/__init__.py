"""`rite init` — the questionnaire and everything it generates (SPEC.md §9.3).

Public interface for the CLI entry point (`src/rite_ai/cli/main.py`, owned by a
different session — this module is deliberately import-only, no click
`@cli.command()` registration here):

    from rite_ai.cli.init import run_init

    result = run_init(
        Path.cwd(),
        config_path=Path(config) if config else None,
        yes=yes,
    )
    click.echo(result.message)
    if result.status == "error":
        raise SystemExit(1)

`run_init` never raises for expected conditions (bad config file, declined
wipe, detection finding nothing) — those are all `InitResult.status` values.
It *can* raise on truly unexpected filesystem errors; the caller may want a
try/except around it for a clean CLI error message, but that's the caller's
call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml

from rite_ai.config.models import ProjectBrief
from rite_ai.config.parse import parse_brief
from rite_ai.gate.hook import redirected_hooks_dir
from rite_ai.state import write_atomic

from . import claude_gen, scaffold, ui
from .config_file import ConfigFileError, load_preset
from .detect import run_detection
from .questionnaire import run_questionnaire, source_answers


@dataclass
class InitResult:
    # "created" | "already_initialized" | "aborted" | "error", and for a path
    # that is already a rite project, "updated" | "unchanged".
    status: str
    message: str
    created_files: list[str] = field(default_factory=list)


def run_init(
    root: Path, *, config_path: Path | None = None, yes: bool = False
) -> InitResult:
    root = root.resolve()
    interactive = not yes

    preset = load_preset(config_path)
    if isinstance(preset, ConfigFileError):
        return InitResult(
            status="error",
            message=f"Could not read config file {preset.file}: {preset.message}",
        )

    source = _existing_source(root, preset, interactive)
    if isinstance(source, InitResult):
        return source
    changes = ""
    if source is not None:
        changes = _read_changes(source, preset, interactive)
        project = _rite_project_at(source)
        if project is not None:
            return _record_changes(project, changes)

    rite_dir = root / ".rite"
    if rite_dir.exists():
        wipe = False
        if interactive:
            wipe = ui.confirm(
                "This directory is already initialised. Wipe and start over?",
                default=False,
            )
        if not wipe:
            return InitResult(
                status="already_initialized",
                message=f"{rite_dir} already exists — left untouched. "
                "Re-run interactively and confirm to wipe and start over.",
            )
        import shutil

        shutil.rmtree(rite_dir)

    rite_dir.mkdir(parents=True, exist_ok=True)

    if source is not None:
        answers = source_answers(root, preset, source, changes)
    else:
        detection = run_detection(root)
        answers = run_questionnaire(root, preset, detection, yes)

    created: list[str] = []

    write_brief_path = scaffold.write_brief(rite_dir, answers.brief)
    created.append(str(write_brief_path.relative_to(root)))

    write_modules_path = scaffold.write_modules(rite_dir, answers.modules)
    created.append(str(write_modules_path.relative_to(root)))

    write_config_path = scaffold.write_config(rite_dir, answers.config)
    created.append(str(write_config_path.relative_to(root)))

    # Record which config schema these files were written against, so a
    # future migration can tell this project apart from one created before
    # stamping existed. Unstamped reads as the oldest schema, which is
    # correct for those and would be wrong for this one.
    from rite_ai.update import stamp_schema_version

    stamp_schema_version(rite_dir)

    context_index_path = scaffold.write_context_index(rite_dir)
    created.append(str(context_index_path.relative_to(root)))

    kb_index_path, kb_entries = scaffold.write_kb(rite_dir, root, answers.kb)
    created.append(str(kb_index_path.relative_to(root)))

    checklist_path = scaffold.write_review_checklist(rite_dir)
    created.append(str(checklist_path.relative_to(root)))

    scaffold.update_gitignore(root, answers.kb.commit)
    installed_hooks = scaffold.install_pre_push_hooks(root, answers.modules)
    ci = scaffold.write_ci_workflow(root)
    if ci.status == "written":
        created.append(scaffold.CI_WORKFLOW_REL_PATH)

    claude_counts = claude_gen.install_claude_config(
        root, answers.role, answers.brief, answers.modules, answers.config
    )
    created.append("CLAUDE.md")
    created.append(".claude/agents/")
    created.append(".claude/commands/")
    preserved_claude_md = claude_counts.get("preserved") or ""

    click.echo()
    ui.created(f"{write_brief_path.relative_to(root)}")
    ui.created(f"{write_modules_path.relative_to(root)}")
    ui.created(f"{write_config_path.relative_to(root)}")
    ui.created(
        f"{context_index_path.relative_to(root)} (empty — grows as the project does)"
    )
    ui.created(f"{kb_index_path.relative_to(root)} ({kb_entries} entries)")
    ui.created(f"{checklist_path.relative_to(root)} (default)")
    # Coverage is read off the FILE'S CONTENT, never off who wrote it.
    # `already_ours` only means the marker is present, and a workflow rite
    # wrote that the user has since edited into a no-op is ours and inert;
    # a workflow someone else wrote that calls `rite publish check` is not
    # ours and covers this project completely. Claiming the first as
    # coverage is `✓ Created .git/hooks/pre-push` for a hook git will never
    # read (§11.5.1), one layer up.
    ci_ok = ci.runs_gate
    # What the user is told when the local layer failed depends on whether
    # the other one is actually armed. The README promises, in the
    # paragraph a redirected-hooks user reaches at exactly this moment,
    # that CI has them covered; saying so here when it does not would be
    # the same sentence `rite init` used to print about a hook git would
    # never read.
    ci_backstop = (
        " The CI workflow below runs the same scan over full history on "
        "every push and pull request, and no local git config can switch "
        "that off — so you are not uncovered, but the fast local check is "
        "gone."
        if ci_ok
        else " Nothing else is covering you: the publish gate does not run "
        "in CI either (see below)."
    )
    if installed_hooks:
        ui.created(f".git/hooks/pre-push ({len(installed_hooks)} repo(s))")
    else:
        # Never silently. The gate is the thing that stops a secret reaching a
        # remote, so "no hook installed" has to be as loud as a created file —
        # a cold rehearsal found a push carrying four planted secrets going
        # through with exit 0 because git was reading hooks from elsewhere.
        redirected = redirected_hooks_dir(root)
        if redirected is not None:
            ui.note(
                f"no pre-push hook installed — git reads hooks from "
                f"{redirected} (core.hooksPath), not .git/hooks. The publish "
                "gate will NOT run on push. Either add `exec rite publish "
                "pre-push` to the pre-push hook in that directory, or run "
                "`git config --local core.hooksPath .git/hooks` followed by "
                "`rite publish install-hook`." + ci_backstop
            )
        else:
            ui.note(
                "no pre-push hook installed — the publish gate will not "
                "run automatically on push. Install it with `rite publish "
                "install-hook`, or run `rite publish check` by hand." + ci_backstop
            )
    # The CI half of SPEC §11.5's "belt and braces", and per §11.5.1 the
    # load-bearing half: the hook above is disarmable by this machine's own
    # git config, with no signal that it happened, and CI is the only layer
    # local configuration cannot switch off. So its absence is reported as
    # loudly as the hook's.
    if ci.status == "written":
        ui.created(
            f"{scaffold.CI_WORKFLOW_REL_PATH} (runs the same gate on every push and PR)"
        )
    elif ci.status in ("already_ours", "foreign"):
        # Not "✓ Created" — nothing was created, and nothing was rewritten
        # either, so an edit the user made to that file is theirs and
        # survives. Whether the gate RUNS is a separate sentence because it
        # is a separate fact.
        whose = (
            "was written by rite — left exactly as it is, including any "
            "changes you made to it"
            if ci.status == "already_ours"
            else "carries no rite marker, so it was left alone"
        )
        armed = (
            "It runs `rite publish check`, so the publish gate does run in CI."
            if ci.runs_gate
            else "It does NOT run `rite publish check`, so the publish gate "
            "does not run in CI. Add that to it, or `rite publish install-ci "
            "--force` to replace it with the generated workflow."
        )
        ui.note(f"{scaffold.CI_WORKFLOW_REL_PATH} already exists and {whose}. {armed}")
    elif ci.status == "unreadable":
        ui.note(
            f"no CI workflow written — {scaffold.CI_WORKFLOW_REL_PATH} exists "
            f"but could not be read ({ci.detail}), so it was left alone. "
            "Whether the publish gate runs in CI is unknown from here; open "
            "that file and check it runs `rite publish check`."
        )
    elif ci.status == "write_failed":
        ui.note(
            f"no CI workflow written — {scaffold.CI_WORKFLOW_REL_PATH} could "
            f"not be created ({ci.detail}). The publish gate will not run in "
            "CI until it exists. Fix that, then `rite publish install-ci` — "
            "re-running `rite init` will not do it, as this project is now "
            "initialised and that offers to wipe its config instead."
        )
    elif ci.status == "not_a_repo":
        ui.note(
            "no CI workflow written — this is not a git repository, so there "
            "is nowhere for one to run. `git init` here, then `rite publish "
            f"install-ci` to get {scaffold.CI_WORKFLOW_REL_PATH} — the only "
            "layer of the publish gate a local git config cannot switch off."
        )
    else:
        # Never fabricate a cause for a state nobody mapped. The marker
        # tuple in `scaffold.py` exists to stop exactly this shape.
        ui.note(
            f"no CI workflow written ({ci.status}) — the publish gate may not "
            "run in CI. Check with `rite publish install-ci`."
        )
    ui.generated(f"CLAUDE.md ({answers.role})")
    if preserved_claude_md:
        # Named, and named loudly: the file this moved is the standing
        # instruction every session in that project loads, and a line
        # nobody reads is the same as the overwrite this replaced.
        ui.warn(
            "your existing CLAUDE.md was NOT written by rite — it has been "
            f"moved to {Path(str(preserved_claude_md)).name}, not deleted. "
            "Merge anything it said into the new one; nothing else reads "
            "the preserved copy."
        )
    ui.generated(f".claude/agents/ ({claude_counts['agents']} agents)")
    ui.generated(f".claude/commands/ ({claude_counts['commands']} commands)")
    click.echo()
    click.echo("Ready. Start a Dispatch session — it knows what to do from here.")

    return InitResult(
        status="created",
        message="rite project initialised.",
        created_files=created,
    )


__all__ = ["InitResult", "run_init"]


# --- the first question (SPEC §9.3) ----------------------------------------------

EXISTING_QUESTION = "Do you have a spec or existing code for this project? [y/N]"
PATH_PROMPT = "Path: [.]"
READING = (
    "Reading {path} — languages, structure and conventions will be taken\n"
    "from what's there."
)
CHANGES_QUESTION = (
    "Anything stale, or that you'd like changed? Free text, or Enter to skip."
)
ALREADY_A_PROJECT = (
    "This is already a rite project — I'll apply your changes rather than "
    "starting over."
)


def _display(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _resolve_source(root: Path, text: str) -> Path:
    path = Path(text).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _existing_source(
    root: Path, preset, interactive: bool
) -> Path | InitResult | None:
    """Where the existing spec or code is, or None to start from scratch.

    A path is asked for again until it exists. A typo that fell through to
    the from-scratch flow would ask nineteen questions of someone who said
    they had already answered them, so that never happens — in a preset
    either, where a missing path is an error."""
    preset_path = preset.get("source.path")
    if preset_path is not None:
        path = _resolve_source(root, str(preset_path))
        if not path.exists():
            return InitResult(
                status="error",
                message=f"source.path {preset_path!r} does not exist "
                f"(looked at {path})",
            )
        return path
    if not interactive:
        return None
    if not ui.confirm(
        EXISTING_QUESTION.removesuffix(" [y/N]"), default=False, suffix=" "
    ):
        return None
    click.echo()
    while True:
        path = _resolve_source(root, ui.line(PATH_PROMPT, default="."))
        if path.exists():
            return path
        ui.warn(f"Nothing at {_display(path)} — check the path and enter it again.")


def _read_changes(source: Path, preset, interactive: bool) -> str:
    click.echo()
    click.echo(READING.format(path=_display(source)))
    if _rite_project_at(source) is not None:
        click.echo()
        click.echo(ALREADY_A_PROJECT)
    preset_changes = preset.get("source.changes")
    if preset_changes is not None:
        return str(preset_changes).strip()
    if not interactive:
        return ""
    click.echo()
    click.echo(CHANGES_QUESTION)
    return ui.paragraph(">")


def _rite_project_at(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    brief = parse_brief(path / ".rite" / "brief.yaml")
    return path if isinstance(brief, ProjectBrief) else None


def _record_changes(project: Path, changes: str) -> InitResult:
    """Record the answer in an existing project's brief, and nothing else.

    Everything else in the file is kept as it was written, including sections
    `init` never writes. A request recorded earlier and not yet acted on is
    kept beside the new one rather than replaced."""
    if not changes:
        return InitResult(
            status="unchanged", message="Nothing to apply — the project is unchanged."
        )
    brief_path = project / ".rite" / "brief.yaml"
    raw = yaml.safe_load(brief_path.read_text())
    source = raw.get("source")
    if not isinstance(source, dict):
        source = {}
    earlier = str(source.get("changes") or "").strip()
    source["changes"] = f"{earlier}\n\n{changes}" if earlier else changes
    source.setdefault("path", str(project))
    raw["source"] = source
    write_atomic(
        brief_path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    )
    return InitResult(
        status="updated",
        message=f"Recorded in {brief_path}. Nothing else in the project was changed.",
    )
