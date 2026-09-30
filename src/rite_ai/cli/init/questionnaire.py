"""The seven-section `rite init` questionnaire (SPEC.md §9.3).

Produces an `InitAnswers` bundle. Every question resolves in this order:

1. A value from the `--config` preset file, if present — never prompted.
2. If `--yes`: a stated, documented default — never prompted, never invented.
3. Otherwise: an interactive prompt (SPEC.md §9.2 conventions), pre-filled
   with the same default shown in brackets.

This ordering is what makes `rite init --config <file> --yes` fully
deterministic and what makes the interactive path testable through
`click.testing.CliRunner` (see tests/test_init_questionnaire.py).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import click

from rite_ai.config.models import (
    CredentialsConfig,
    Module,
    ProjectBrief,
    ProjectConfig,
    SandboxConfig,
    SpecConfig,
    TicketBackendConfig,
)
from rite_ai.credentials.store import make_namespace
from rite_ai.tickets.scope import label_for

from . import ui
from .config_file import Preset
from .detect import (
    ROOT_MODULE_PATH,
    DetectedRepo,
    DetectionSummary,
    detect_repos,
    detect_root_branch,
    holds_files_but_no_repository,
    root_has_nothing_committed,
)

_KIND_OPTIONS = [
    ("full-stack", "Full-stack"),
    ("backend", "Backend"),
    ("frontend", "Frontend"),
    ("mobile", "Mobile"),
    ("library", "Library"),
    ("other", "Other"),
]


def _kind_index(kind: str | None) -> int:
    """The option detection supports, else the first — the old fixed default."""
    values = [value for value, _ in _KIND_OPTIONS]
    return values.index(kind) if kind in values else 0


_TICKET_OPTIONS = [
    ("jira", "JIRA"),
    ("github", "GitHub Issues"),
    ("none", "None for now"),
]


@dataclass
class KbAnswers:
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    commit: bool = True


@dataclass
class InitAnswers:
    role: str
    brief: ProjectBrief
    modules: list[Module]
    config: ProjectConfig
    kb: KbAnswers
    # question key -> where the value came from: "typed", "--config",
    # "detected" or "--yes default". Populated by `run_questionnaire`; used
    # to say what a non-interactive run decided on the user's behalf.
    sources: dict[str, str] = field(default_factory=dict)
    # A repository the person named when the path they gave held nothing
    # (`ask_for_the_code`): `(module name, URL)`, registered and cloned by
    # `run_init` once `.rite/` exists, exactly as `rite add module` does.
    link: tuple[str, str] | None = None


def _resolve_spec(
    preset: dict, interactive: bool, ui, root: Path, modules: list[Module]
) -> SpecConfig:
    """Point Workers at the spec this project already has.

    Detect and propose, never ask blank. "Where is your spec?" put to
    someone who has one is a question they have to answer by typing a path
    rite could have found, and put to someone who has none it is a question
    with no good answer.

    Nothing is copied and nothing is read beyond the one scan that decides
    whether to propose a citation convention. A spec is thousands of lines
    and it changes; the worker gets the path and reads what its ticket
    needs.
    """
    from rite_ai.cli.init.detect import (
        SPEC_DIRS,
        SPEC_FILES,
        detect_decision_convention,
        detect_spec_paths,
    )

    preset_paths = preset.get("spec.paths")
    if preset_paths is not None:
        paths = (
            [str(v) for v in preset_paths]
            if isinstance(preset_paths, list)
            else [s.strip() for s in str(preset_paths).split(",") if s.strip()]
        )
        return SpecConfig(
            paths=paths,
            convention=str(preset.get("spec.convention", ""))
            or detect_decision_convention(root, paths),
        )

    found = detect_spec_paths(root, [m.path for m in modules])
    if not found:
        if interactive:
            looked_for = ", ".join([*SPEC_FILES, *(d + "/" for d in SPEC_DIRS)])
            ui.note(
                f"No spec found (looked for {looked_for} in the project root "
                "and each module). Add one later with `rite spec add <path>` "
                "so Workers can read it."
            )
        return SpecConfig()

    if not interactive:
        # Said, not silent. `--yes` takes the interactive default here
        # (confirm(…, default=True)), so the decision is right — but it
        # points every Worker at a file nobody was shown, and adopting a
        # spec is the one `--yes` answer with a consequence outside
        # `.rite/`. The line is what makes it correctable.
        ui.note(
            "Pointing Workers at the spec found here: "
            + ", ".join(f"`{p}`" for p in found)
            + " (--yes took the default; `rite spec remove <path>` undoes it)"
        )
        return SpecConfig(
            paths=found, convention=detect_decision_convention(root, found)
        )

    ui.note("Found a spec: " + ", ".join(f"`{p}`" for p in found))
    if not ui.confirm("Point Workers at it?", default=True):
        ui.note("Not recorded — add it later with `rite spec add <path>`.")
        return SpecConfig()

    convention = detect_decision_convention(root, found)
    if convention:
        ui.note(f"Tickets appear to cite decisions: {convention}")
        if not ui.confirm("Tell Workers that convention?", default=True):
            convention = ""
    return SpecConfig(paths=found, convention=convention)


def _resolve_sandbox(preset: dict, interactive: bool, ui) -> tuple[bool, str]:
    """Ask whether Workers should run sandboxed.

    `install.sh` deliberately never asks anything: piping it to a shell
    binds stdin to the pipe, so a prompt there either hangs or reads the
    script as its own answer. The question belongs at the first moment a
    human is certainly at a terminal, which is here.

    Three situations, and only one of them is a yes/no:

    1. **A verified backend is available** — ask, default Yes.
    2. **yoloAI is not installed** — offer to install it, then ask. The
       offer is skipped if it was declined before on this machine (see
       `prefs`), or if rite does not know how to install it here.
    3. **No backend rite has verified works here** — do not ask. There is
       no answer that would make it work, so say why in one line and
       continue with sandboxing off.

    Branch 3 is decided BEFORE branch 2, from the platform alone. With
    yoloAI absent there is nothing to ask about backends, and offering to
    install it where no verified backend can exist would be offering
    something that cannot help.
    """
    from rite_ai.cli.init import prefs
    from rite_ai.sandbox import (
        CountUnavailable,
        choose_backend,
        install_yoloai,
        is_installed,
        platform_can_sandbox,
        yoloai_install_command,
    )

    default_backend = SandboxConfig().backend
    preset_value = preset.get("operations.sandbox")

    def _answer(backend: str, *, default: bool = True) -> tuple[bool, str]:
        if preset_value is not None:
            return bool(preset_value), backend
        if not interactive:
            return default, backend
        return ui.confirm("Run Workers in sandboxes?", default=default), backend

    # --- Branch 3: nothing rite has verified can run here ---
    if not platform_can_sandbox():
        ui.note(
            "Workers will not be sandboxed: the only backend rite has "
            "verified keeps claims working is macOS-only, and `flock` is a "
            "no-op inside a docker sandbox. Everything else works normally."
        )
        return False, default_backend

    # --- Branch 2: yoloAI absent ---
    if not is_installed():
        if interactive and preset_value is None:
            _offer_yoloai_install(ui, prefs, install_yoloai, yoloai_install_command)
        if not is_installed():
            # Still absent — declined, unavailable, or the install did not
            # take. The setting can still be turned on; `rite doctor` then
            # reports the sandbox as not working until yoloAI is there,
            # which is the honest state rather than a silent No.
            return _with_login_note(ui, _answer(default_backend))

    # --- Branch 1 (or branch 3 discovered late, once yoloAI can answer) ---
    choice = choose_backend()
    if isinstance(choice, CountUnavailable):
        if interactive:
            ui.note(f"could not ask yoloAI which backends work here: {choice.reason}")
        return _with_login_note(ui, _answer(default_backend))
    if not choice.usable:
        ui.note(
            f"Workers will not be sandboxed: {choice.reason}. "
            "Everything else works normally."
        )
        return False, default_backend
    return _with_login_note(ui, _answer(choice.name))


def _with_login_note(ui, answer: tuple[bool, str]) -> tuple[bool, str]:
    """Say, at setup, what a sandboxed Worker needs that init does not ask.

    The token is a secret, and init does not take secrets: the secret path
    stays in `rite credential set`. Without it a sandboxed Worker starts and
    does nothing, which is a bad thing to discover at 2am."""
    if answer[0]:
        ui.note(
            "A sandboxed Worker cannot use your Claude login from the keychain. "
            "Before starting one, run `claude setup-token`, then "
            "`rite credential set claude` to store the token it prints."
        )
    return answer


def _offer_yoloai_install(ui, prefs, install_yoloai, yoloai_install_command) -> None:
    """Offer to install yoloAI, run it, and say what actually happened.

    Every outcome here is one someone will hit: no installer rite knows
    about, a decline, a failed install, and an install that reports
    success while leaving nothing on PATH. None of them stop `rite init` —
    the sandbox question is still asked afterwards either way.
    """
    command = yoloai_install_command()
    if command is None:
        ui.note(
            "yoloAI is not installed, and rite does not know how to install "
            "it here — get it from https://yoloai.dev. Sandboxing can still "
            "be turned on now; it starts working once yoloAI is there."
        )
        return

    if prefs.yoloai_install_declined():
        ui.note(
            "yoloAI is not installed (you declined installing it before). "
            "Get it from https://yoloai.dev; sandboxing can still be turned "
            "on now."
        )
        return

    ui.note(
        "yoloAI is what runs Workers in sandboxes. It is a separate binary, "
        "not a Python dependency."
    )
    if not ui.confirm(f"Install it now with `{' '.join(command)}`?", default=True):
        prefs.record_yoloai_install_declined()
        ui.note(
            "Not installing — and not asking again on this machine. Get it "
            "from https://yoloai.dev whenever you want it."
        )
        return

    ui.note(f"running `{' '.join(command)}` — this can take a minute")
    outcome = install_yoloai()
    if outcome.ok:
        ui.note("yoloAI installed.")
    else:
        ui.warn(outcome.detail)
        ui.note(
            "Sandboxing can still be turned on now; it starts working once "
            "yoloAI is there, and `rite doctor` will say so until then."
        )


def _say_what_was_decided(sources: dict[str, str], values: dict[str, str]) -> None:
    """Print every answer a non-interactive run resolved, and where it came
    from.

    `--yes` answered eleven questions and printed one line about one of them.
    Each answer was defensible on its own — the audit checked every field
    against the interactive default and found one real divergence — but a
    project was configured out of detection and defaults with nothing said,
    and `.rite/` is not where someone thinks to look for what they did not
    know was decided. Nothing here asks anything: this is a report, not the
    review screen the interactive redesign is parked on.
    """
    key_width = max(len(k) for k in values)
    shown = {k: (v if v else "—") for k, v in values.items()}
    value_width = min(max(len(v) for v in shown.values()), 40)
    ui.note("Resolved without asking (rite init --yes):")
    for key, value in shown.items():
        source = sources.get(key, "--yes default")
        ui.plain(f"  {key.ljust(key_width)}  {value.ljust(value_width)}  [{source}]")
    ui.plain("  Change any of these in .rite/, or re-run `rite init` in an")
    ui.plain("  empty directory to be asked.")


def run_questionnaire(
    root: Path, preset: Preset, detected: DetectionSummary, yes: bool
) -> InitAnswers:
    interactive = not yes
    sources: dict[str, str] = {}

    def _source(key: str, from_preset: bool, detected_default: bool) -> None:
        if from_preset:
            sources[key] = "--config"
        elif interactive:
            sources[key] = "typed"
        elif detected_default:
            sources[key] = "detected"
        else:
            sources[key] = "--yes default"

    def resolve_text(
        key: str,
        question: str,
        default: str = "",
        required: bool = False,
        detected_default: bool = False,
    ) -> str:
        val = preset.get(key)
        _source(key, val is not None, detected_default and bool(default))
        if val is not None:
            return str(val)
        if not interactive:
            return default
        return ui.text(question, default=default, required=required)

    def resolve_list(
        key: str,
        question: str,
        default: list[str] | None = None,
        detected_default: bool = False,
    ) -> list[str]:
        default = default or []
        val = preset.get(key)
        _source(key, val is not None, detected_default and bool(default))
        if val is not None:
            if isinstance(val, list):
                return [str(v) for v in val]
            return [s.strip() for s in str(val).split(",") if s.strip()]
        if not interactive:
            return default
        return ui.text_list(question, default=default)

    def resolve_select(
        key: str,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
        fallback: str | None = None,
        detected_default: bool = False,
    ) -> str:
        val = preset.get(key)
        valid = {v for v, _ in options}
        _source(key, val is not None and val in valid, detected_default)
        if val is not None and val in valid:
            return val
        if not interactive:
            if fallback is not None:
                # The one place `--yes` does not take the interactive default,
                # so the line says which default it did not take.
                interactive_default = options[default_index][0]
                sources[key] = (
                    f"--yes fallback, not the interactive "
                    f"default ({interactive_default})"
                )
                return fallback
            return options[default_index][0]
        return ui.select(question, options, default=default_index)

    def resolve_bool(key: str, question: str, default: bool = True) -> bool:
        val = preset.get(key)
        _source(key, val is not None, False)
        if val is not None:
            return bool(val)
        if not interactive:
            return default
        return ui.confirm(question, default=default)

    # --- Section 1: Role ---
    ui.section("Role", 1, 7)
    role = resolve_select(
        "project.role",
        "Is this the Owner machine or a Manager machine?",
        ROLE_OPTIONS,
        default_index=0,
    )
    borrowed_config: ProjectConfig | None = None
    if role == "manager":
        owner_ref = resolve_text(
            "project.owner_ref", "Owner's project URL or config path?", default=""
        )
        if owner_ref:
            borrowed_config = _borrow_owner_config(owner_ref)

    # --- Section 2: Project ---
    ui.section("Project", 2, 7)
    default_name = root.name or "my-project"
    name = resolve_text(
        "project.name", "Project name?", default=default_name, detected_default=True
    )
    # Offer the branch detection found, not a fixed `main`. Section 3 writes
    # each module's detected branch into modules.yaml; a hardcoded default here
    # meant pressing Enter produced a root branch that disagreed with the
    # modules registered beside it — PRs aimed at a line the project is not on.
    if (
        detected.root_branch is None
        and len({r.branch for r in detected.repos}) > 1
        and interactive
        and preset.get("project.root_branch") is None
    ):
        ui.note(
            "Repositories are on different branches ("
            + ", ".join(f"{r.path} {r.branch}" for r in detected.repos)
            + ") — not guessing which is the project's line."
        )
    root_branch = resolve_text(
        "project.root_branch",
        "Root branch?",
        default=detected.root_branch or "main",
        detected_default=detected.root_branch is not None,
    )

    # --- Section 3: Modules ---
    ui.section("Modules", 3, 7)
    modules = _resolve_modules(preset, interactive, detected.repos, root)

    # --- Section 4: What's being built ---
    title = (
        "What is this?"
        if detected.has_language_markers or modules
        else "What will this be?"
    )
    ui.section(title, 4, 7)
    # Kind and description come from the project's manifests when they say.
    # Both were fixed (Full-stack, blank) and a package that installs a command
    # was offered Full-stack — accepted by anyone pressing Enter.
    kind = resolve_select(
        "what.kind",
        "What kind of project is this?",
        _KIND_OPTIONS,
        default_index=_kind_index(detected.kind),
        detected_default=detected.kind is not None,
    )
    features = resolve_text(
        "what.features",
        "Describe what this project does:",
        default=detected.description or "",
        detected_default=bool(detected.description),
    )

    # --- Section 5: Technology ---
    ui.section("Technology", 5, 7)
    platform = resolve_text(
        "technology.platform",
        "Platform?",
        default=detected.platform,
        detected_default=bool(detected.platform),
    )
    languages = resolve_list(
        "technology.languages",
        "Languages?",
        default=list(detected.languages),
        detected_default=True,
    )
    frameworks = resolve_list(
        "technology.frameworks",
        "Frameworks?",
        default=list(detected.frameworks),
        detected_default=True,
    )
    architecture = resolve_text("technology.architecture", "Architecture?", default="")

    # --- Section 6: Operations ---
    ui.section("Operations", 6, 7)
    ticket_type = resolve_select(
        "operations.ticket_backend",
        "Ticket backend?",
        _TICKET_OPTIONS,
        default_index=0,
        # The one place `--yes` deliberately differs from the interactive
        # default (JIRA), and the only `fallback` in this file. JIRA needs a
        # site and a project key that `--yes` cannot supply, so defaulting to
        # it would write a backend that refuses every board command with a
        # missing-credential error. `none` is the only answer that is not
        # broken, and `rite credential set jira` turns it into a real board
        # in one command. Recorded here because nothing recorded it before.
        fallback="none",
    )
    jira_site = ""
    if ticket_type == "jira":
        jira_site = resolve_text(
            "operations.jira_site", "JIRA site? (e.g. myteam.atlassian.net)", default=""
        )

    sandbox_enabled, sandbox_backend = _resolve_sandbox(preset, interactive, ui)
    spec = _resolve_spec(preset, interactive, ui, root, modules)

    # --- Section 7: Knowledge ---
    ui.section("Knowledge", 7, 7)
    click.echo(
        "Reference material to include? Links, documents, coding standards,\n"
        "or domain knowledge. You can add more later with 'rite kb add'."
    )
    click.echo()
    kb_links = _resolve_kb_list(
        preset,
        interactive,
        "knowledge.links",
        "Add a link?  (URL — will be fetched and cached)",
    )
    kb_files = _resolve_kb_list(
        preset,
        interactive,
        "knowledge.files",
        "Add a file?  (path — will be copied into .rite/kb/)",
    )
    ui.note(
        "Authored knowledge (principles, patterns, notes) is a team asset — "
        "versioned, reviewable, new members inherit it. Fetched link caches "
        "are always gitignored regardless."
    )
    kb_commit = resolve_bool(
        "knowledge.commit", "Commit the knowledge base to git?", default=True
    )

    ticket_backend = TicketBackendConfig(
        type=ticket_type,
        site=jira_site,
        projects={},
        credential="jira_token" if ticket_type == "jira" else "",
        # Scoped from the start, whatever the board (Robert, 2026-09-29): a
        # board set up later with `rite credential set jira` is then already
        # this project's alone (v0.7.0 dogfood S1, `tickets.scope`).
        scope_label=label_for(name),
    )
    # Generated HERE, once, and committed with the rest of config.yaml
    # (§10.2). Doing it at init rather than lazily on the first
    # `rite credential set` is what lets a fresh clone run
    # `rite credential list` and be told the real account names straight
    # away, instead of "(none yet)" until somebody stores something.
    #
    # Deliberately NOT borrowed below, however much else is: a borrowed
    # namespace would mean two projects silently sharing one set of
    # credentials, which is the defect this exists to remove.
    config = ProjectConfig(
        ticket_backend=ticket_backend,
        credentials=CredentialsConfig(namespace=make_namespace(name)),
        sandbox=SandboxConfig(enabled=sandbox_enabled, backend=sandbox_backend),
        spec=spec,
    )
    if borrowed_config is not None:
        config.expertise = borrowed_config.expertise
        if ticket_type == "none" and borrowed_config.ticket_backend.type != "none":
            config.ticket_backend = borrowed_config.ticket_backend

    brief = ProjectBrief(
        name=name,
        role=role,
        root_branch=root_branch,
        kind=kind,
        features=features,
        platform=platform,
        languages=languages,
        frameworks=frameworks,
        architecture=architecture,
    )

    if not interactive:
        _say_what_was_decided(
            sources,
            {
                "project.role": role,
                "project.name": name,
                "project.root_branch": root_branch,
                "what.kind": kind,
                "what.features": features,
                "technology.platform": platform,
                "technology.languages": ", ".join(languages),
                "technology.frameworks": ", ".join(frameworks),
                "technology.architecture": architecture,
                # The key the resolver recorded, not the config field it
                # ends up in — a mismatch here silently relabels the one
                # answer that actually diverges as an ordinary default.
                "operations.ticket_backend": ticket_type,
                "kb.commit": "yes" if kb_commit else "no",
            },
        )

    return InitAnswers(
        role=role,
        brief=brief,
        modules=modules,
        config=config,
        kb=KbAnswers(links=kb_links, files=kb_files, commit=kb_commit),
        sources=sources,
    )


def _resolve_kb_list(
    preset: Preset, interactive: bool, key: str, question: str
) -> list[str]:
    val = preset.get(key)
    if val is not None:
        if isinstance(val, list):
            return [str(v) for v in val]
        return [s.strip() for s in str(val).split(",") if s.strip()]
    if not interactive:
        return []
    return ui.repeat_until_blank(question)


def _resolve_modules(
    preset: Preset,
    interactive: bool,
    detected_repos: list[DetectedRepo],
    root: Path | None = None,
) -> list[Module]:
    if preset.has_modules():
        result: list[Module] = []
        for mod_name, entry in preset.raw_modules().items():
            if not isinstance(entry, dict):
                continue
            result.append(
                Module(
                    name=mod_name,
                    path=entry.get("path", f"{mod_name}/"),
                    url=entry.get("url"),
                    branch=entry.get("branch", "main"),
                    description=entry.get("description", ""),
                )
            )
        return result

    if detected_repos:
        return offer_modules(
            [
                Module(
                    name=r.name, path=r.path, url=r.url, branch=r.branch, description=""
                )
                for r in detected_repos
            ],
            interactive,
        )
    offer_modules([], interactive, root)  # says why an uncommitted root is not offered
    if not interactive:
        return []

    click.echo("No repositories found. Add a module?")
    modules: list[Module] = []
    while True:
        mname = ui.text("Module name?", default="")
        if not mname:
            break
        murl = ui.text("Git URL?", default="")
        modules.append(
            Module(
                name=mname,
                path=f"{mname}/",
                url=murl or None,
                branch="main",
                description="",
            )
        )
    return modules


def _borrow_owner_config(ref: str) -> ProjectConfig | None:
    """Best-effort fetch of an Owner's config.yaml to align ticket backend
    and expertise tags. Never blocks or invents — a failure is silently
    skipped and reported as a note, not an error."""
    from rite_ai.config.parse import ParseError, parse_config

    text: str | None = None
    candidate = Path(ref)
    if candidate.exists():
        try:
            text = candidate.read_text()
        except OSError:
            text = None
    elif ref.startswith(("http://", "https://")):
        try:
            import urllib.request

            with urllib.request.urlopen(ref, timeout=5) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
        except Exception:
            text = None

    if not text:
        ui.note(f"Could not read '{ref}' — continuing without borrowed config.")
        return None

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    try:
        tmp.write(text)
        tmp.close()
        parsed = parse_config(Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    if isinstance(parsed, ParseError):
        ui.note(f"Could not parse config from '{ref}' — continuing without it.")
        return None
    return parsed


# What `rite init` may NEVER derive, on any path: these are facts about
# this person and this machine, and neither a spec nor a codebase contains
# them. Everything else about the project — its kind, languages,
# frameworks, modules, root branch — is derived from the source when there
# is one, and asked only when there is not.
#
# Measured: the existing-source path shipped with `role="owner"` hardcoded,
# so a tester who copied a project's `.rite/` to a second machine was never
# asked, and that machine's session believed it owned the board.
ABOUT_THIS_PERSON_AND_MACHINE = ("project.role", "sandbox.enabled")

ROLE_OPTIONS = [
    ("owner", "Owner    — owns the board, assigns work, one per project"),
    ("manager", "Manager  — receives work from an Owner, runs its own workers"),
]


def _ask_role(preset: Preset, interactive: bool) -> str:
    """The role question, asked the same way on every path."""
    val = preset.get("project.role")
    if val is not None and val in {v for v, _ in ROLE_OPTIONS}:
        return str(val)
    if not interactive:
        return "owner"
    return ui.select("Is this the Owner machine or a Manager machine?", ROLE_OPTIONS)


__all__ = [
    "InitAnswers",
    "KbAnswers",
    "portable_source_path",
    "run_questionnaire",
    "source_answers",
]


def portable_source_path(root: Path, source: Path) -> str:
    """How `source.path` is written into the brief: never with a home path in it.

    `brief.yaml` is committed, and rite's own publish gate refuses a
    `/Users/<name>/` or `/home/<name>/` path in any pushed file. Measured in
    the v0.6.0 dogfood (F1): init wrote the resolved absolute path, so the
    first `git push` after `rite init` was blocked by rite, and the only ways
    past were a throwaway suppression or `--no-verify`.

    Inside the project it is relative to the root (`.` for the default
    answer), elsewhere under home it is `~/…`, and only outside home is it
    absolute. `_resolve_source` reads all three forms back.
    """
    root = root.resolve()
    if source.is_relative_to(root):
        return source.relative_to(root).as_posix() or "."
    home = Path.home().resolve()
    if source.is_relative_to(home):
        return f"~/{source.relative_to(home).as_posix()}"
    return str(source)


NO_MODULE_YET = (
    "No module is registered, so this project has nothing to work on yet: a "
    "Worker's workspace is its modules' clones. Add the code with `rite add "
    "module <name> <repository URL>`."
)


def holds_nothing(path: Path) -> bool:
    """No code and no spec at `path`: a directory with nothing in it but hidden
    entries (`.git`, and rite's own `.rite/` and `.claude/`). A file is a spec,
    so a path to one never holds nothing."""
    if not path.is_dir():
        return False
    return not any(not p.name.startswith(".") for p in path.iterdir())


def module_name_for(url: str) -> str:
    """The module name a repository URL suggests: its last path part, without
    `.git` (`https://github.com/o/yoloai.git` -> `yoloai`)."""
    last = url.strip().rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    return last.removesuffix(".git") or "code"


def looks_like_a_repository(answer: str) -> bool:
    """A URL (`https://…`, `ssh://…`, `file://…`), an scp-style `user@host:path`,
    or a local path that is a git repository. Anything else is a sentence typed
    into the wrong prompt, not a repository to clone."""
    import re

    answer = answer.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://\S+$", answer):
        return True
    if re.match(r"^[\w.-]+@[\w.-]+:\S+$", answer):
        return True
    local = Path(answer).expanduser()
    return local.is_dir() and ((local / ".git").exists() or (local / "HEAD").is_file())


def ask_for_the_code(base: Path, interactive: bool) -> tuple[str, str] | None:
    """The existing-code route found nothing at `base`: ask where the code is.

    ⚠ **Not a silent empty shell** (v0.7.0 dogfood S11). Measured on 0.7.0a2:
    `rite init` in an empty directory, "existing spec or code", path `.`,
    printed that languages, structure and conventions "will be taken from
    what's there", then wrote an empty brief, registered no module and said
    "Ready". Robert's design: when the path holds no code and no spec, ask for
    a repository and offer to add it as a module. Declining, or `--yes` with
    nobody to ask, says what is missing and how to add it."""
    ui.warn(
        f"{base} has no code and no spec in it, so there is nothing to read "
        "and nothing a Worker could work on."
    )
    if not interactive:
        click.echo(f"  {NO_MODULE_YET}")
        return None
    while True:
        url = ui.text(
            "Where is the code? A repository URL to add as a module (Enter to skip)",
            default="",
        ).strip()
        if not url or looks_like_a_repository(url):
            break
        ui.warn(
            f"{url!r} is not a repository URL (https://…, git@host:owner/repo.git, "
            "or a local repository's path). Enter one, or Enter to skip."
        )
    if url:
        name = module_name_for(url)
        if ui.confirm(f"Add {url} as module '{name}'?", default=True):
            return name, url
    click.echo(f"  {NO_MODULE_YET}")
    return None


def source_answers(
    root: Path, preset: Preset, source: Path, changes: str, interactive: bool = True
) -> InitAnswers:
    """The answers for someone who already has a spec or code.

    Nothing about the PROJECT is asked past the first question: the brief
    records where the source is and what in it should change, and everything
    else about the project is left for whatever reads that source.

    `ABOUT_THIS_PERSON_AND_MACHINE` is still asked, here as on every other
    path, because no source document answers it: which role this machine
    takes, and whether its Workers are sandboxed. Everything else is filled
    in the way `--yes` takes it.
    """
    base = source if source.is_dir() else source.parent
    name = root.name or "my-project"
    link = ask_for_the_code(base, interactive) if holds_nothing(source) else None
    role = _ask_role(preset, interactive)
    borrowed_config: ProjectConfig | None = None
    if role == "manager":
        owner_ref = (
            ui.text("Owner's project URL or config path?", default="")
            if interactive
            else str(preset.get("project.owner_ref") or "")
        )
        if owner_ref:
            borrowed_config = _borrow_owner_config(owner_ref)
    sandbox_enabled, sandbox_backend = _resolve_sandbox(preset, interactive, ui)
    config = ProjectConfig(
        ticket_backend=TicketBackendConfig(scope_label=label_for(name)),
        credentials=CredentialsConfig(namespace=make_namespace(name)),
        sandbox=SandboxConfig(enabled=sandbox_enabled, backend=sandbox_backend),
    )
    if borrowed_config is not None:
        config.expertise = borrowed_config.expertise
        config.ticket_backend = borrowed_config.ticket_backend
    return InitAnswers(
        role=role,
        brief=ProjectBrief(
            name=name,
            role=role,
            root_branch=detect_root_branch(base, detect_repos(base)) or "main",
            source_path=portable_source_path(root, source),
            source_changes=changes,
        ),
        modules=(
            []
            if holds_nothing(source)
            else offer_modules(_source_modules(root, base), interactive, base)
        ),
        config=config,
        kb=KbAnswers(),
        link=link,
    )


def _module_label(m: Module) -> str:
    where = "this directory (./)" if m.path == ROOT_MODULE_PATH else m.path
    return f"{where}  {m.url or 'local only'}"


def doubling_as_root(m: Module) -> str:
    """The warning for a repository that is about to be its own project root.

    ⚠ **Said before the answer, while nothing is written.** Measured in the
    v0.7.0 dogfood: `rite init` inside a clone of someone else's project (a
    fork contributed from) registered it as its own module, `path: ./`, and
    then rewrote its CLAUDE.md and `.gitignore` and added `.rite/`, `.claude/`
    and a CI workflow, all as uncommitted changes in the repository the
    contribution goes out from. Nothing said that the repository was doubling
    as the project root. Only an empty `.rite/` exists when this is asked
    (`run_init` writes the rest after the questions), so stopping here is
    still clean."""
    return (
        "this repository is also becoming the project root. rite writes its own "
        "files into it: CLAUDE.md (an existing one is moved aside), .gitignore, "
        ".rite/, .claude/ and .github/workflows/publish-gate.yml, as uncommitted "
        "changes in the same working tree Workers' work is delivered from, and "
        "Workers share its claims. That is fine for a repository of yours that "
        "should carry rite. For someone else's project, or a fork you "
        "contribute from, stop now (Ctrl-C: only an empty .rite/ has been made) "
        f"and use a separate root: `mkdir ../{m.name}-rite && cd "
        f"../{m.name}-rite && rite init`, then `rite add module {m.name} "
        f"{m.url or '<url>'}`."
    )


def offer_modules(
    candidates: list[Module], interactive: bool, root: Path | None = None
) -> list[Module]:
    """Ask about each repository, and register the ones confirmed.

    Asked one at a time, the project root first (Robert, 2026-09-29).

    ⚠ **`--yes` means yes to these too, and says so, one line per module.**
    Asking is meaningless with nobody there, and the other choice — add
    nothing unless confirmed — is how a `--yes` run ends up with an empty
    `modules.yaml` and Workers with nothing to clone (dogfood F2), with no
    difference on screen from an interactive run that added them. Every
    other `--yes` answer is the interactive default, and this one's default
    is yes.
    """
    if not candidates:
        if root is not None and root_has_nothing_committed(root):
            ui.note(
                "This directory is a git repository with nothing committed, so "
                "it is not offered as a module: a Worker could not clone it. "
                "Commit, then add it to .rite/modules.yaml as `path: ./`."
            )
        elif root is not None and holds_files_but_no_repository(root):
            # ⚠ Said, not silent (0.7.0a2 dogfood assessment, beside S11):
            # code with no repository registered nothing, and init went on to
            # "Ready" with no word that a Worker would have nothing to clone.
            ui.warn(
                f"{root} has files in it but is not a git repository, so no "
                "module is registered and a Worker would have nothing to work "
                "on: a Worker's workspace is its modules' clones. Either `git "
                "init` here, commit, and add it to .rite/modules.yaml as `path: "
                "./`, or register the repository it comes from with `rite add "
                "module <name> <repository URL>`."
            )
        return []
    plural = "y" if len(candidates) == 1 else "ies"
    click.echo(f"Found {len(candidates)} repositor{plural}:")
    for m in candidates:
        click.echo(f"  {_module_label(m)}")
    click.echo()
    chosen: list[Module] = []
    for m in candidates:
        where = "this directory (./)" if m.path == ROOT_MODULE_PATH else m.path
        if m.path == ROOT_MODULE_PATH:
            ui.warn(doubling_as_root(m))
        if interactive:
            if ui.confirm(f"Add {where} as module '{m.name}'?", default=True):
                chosen.append(m)
        else:
            click.echo(
                f"  --yes: added {where} as module '{m.name}' ({m.url or 'local only'})"
            )
            chosen.append(m)
    return chosen


def _source_modules(root: Path, base: Path) -> list[Module]:
    """The repositories in the source, as modules, when the source is in the
    project. A Worker clones its modules, so a source registered as nothing
    gives every Worker an empty workspace (dogfood F2).

    A source outside the project is not registered: a module's path is
    relative to the project, and `rite add module` is how to add one from
    elsewhere."""
    root, base = root.resolve(), base.resolve()
    if not base.is_relative_to(root):
        return []
    modules = []
    for r in detect_repos(base):
        rel = (base / r.path).resolve().relative_to(root).as_posix()
        path = ROOT_MODULE_PATH if rel == "." else f"{rel}/"
        modules.append(
            Module(name=r.name, path=path, url=r.url, branch=r.branch, description="")
        )
    return modules
