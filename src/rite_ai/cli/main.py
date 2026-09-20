import os
import time
from contextlib import contextmanager
from pathlib import Path

import click

from rite_ai import __version__
from rite_ai.cli.help import RiteGroup
from rite_ai.state import exclusion_holds

# The files that make a directory a rite PROJECT, as opposed to a directory
# that merely has a `.rite/`. `rite init` writes both and nothing else does,
# and neither is present in rite's own repository — which has a `.rite/` for
# the gate's suppressions and the review checklist, and is not a project.
#
# Either satisfies it: a tree may legitimately carry one without the other
# (a project declared but with no modules registered yet), and requiring both
# would make the answer depend on how far through setup someone is.
PROJECT_MARKERS = (
    Path(".rite") / "brief.yaml",
    Path(".rite") / "modules.yaml",
)


def _is_project(path: Path) -> bool:
    return any((path / m).is_file() for m in PROJECT_MARKERS)


# Explicit override, checked before any walk. Two reasons it exists, and the
# second is why it is not just a convenience:
#
#  1. A module that is itself a rite project. `rite prepare` clones modules
#     UNDER the project root, so `workers/w1/rite/` sits inside the very tree
#     being coordinated. Walking up from cwd finds the INNER project first,
#     and every worker then gets a private claims ledger. Nothing is shared,
#     so nothing ever collides — the run looks flawless and the exclusion
#     guarantee is simply absent. `PROJECT_MARKERS` fixes the case where the
#     inner repo is not a scaffolded project; this fixes the case where it is.
#  2. Test isolation currently rests on 142 hand-written
#     `monkeypatch.chdir(tmp_path)` calls and no autouse fixture. An env var
#     the suite can set is a property, not a convention each test re-observes.
PROJECT_ROOT_ENV = "RITE_PROJECT_ROOT"


def _find_project_root() -> Path:
    """The rite project root: the override if set, else the nearest ancestor
    holding `PROJECT_MARKERS`, else cwd.

    The marker is a FILE, not the `.rite/` directory. `.rite/` alone is not
    the property: rite's own repository has one — it tracks
    `.rite/gitleaksignore` and `.rite/review-checklist.md` for the gate and
    the review convention — and is not a rite project. Neither is any clone
    of a project that ships its committed `.rite/` config, which is every
    one of them, because `scaffold.AUTHORED_CONFIG` re-includes nine paths
    under `.rite/` in the `.gitignore` that `rite init` writes.
    """
    override = os.environ.get(PROJECT_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if _is_project(parent):
            return parent
    return cwd


def _require_project_root() -> Path:
    """The project root, or refuse — for every command that WRITES.

    `_find_project_root` falls back to cwd when no `.rite/` exists
    anywhere above, which is right for the read-only commands (`rite
    status` aggregates, `rite budget` is machine-wide) and wrong for
    anything that persists state: the writer then CREATES `.rite/`
    wherever it was run, and reports success.

    That is not cosmetic. `rite claim src/ --worker alpha` typed one
    directory too high printed "claimed 1 path(s)" and exited 0 against a
    ledger no other session reads — the exclusion guarantee this tool
    exists to provide, silently absent, with the same output as a real
    claim. The phantom `.rite/` then captures every directory BELOW it
    through the walk-up, so unrelated later commands adopt it: measured
    on the author's machine, a stray `/private/tmp/.rite` left by an
    earlier session made `rite doctor` report `project: /private/tmp`
    from a scratch directory six levels down.

    Round 2 found the same shape in `rite pool status` and fixed it
    there, in that one command. This is the general form, and the
    message is `board`'s, which already had it right.
    """
    root = _find_project_root()
    if _is_project(root):
        return root
    # `_find_project_root` returns the real root when it found one and
    # cwd when it did not, and those two are told apart by exactly this:
    # whether the thing it returned has a `.rite/` in it. Asking IT the
    # question rather than re-walking here also means the commands stay
    # testable the way the rest of them are — the suite patches
    # `_find_project_root`, and a second private walk-up would quietly
    # ignore that.
    click.echo(
        f"not a rite project ({root}) — no .rite/brief.yaml or "
        ".rite/modules.yaml here or in any parent. Run `rite init` here "
        "first, change to a project directory, or set "
        f"{PROJECT_ROOT_ENV} to name one explicitly.",
        err=True,
    )
    raise SystemExit(1)


def _resolved_sandbox(root: Path):
    """The project's sandbox setting, and a line to show when it could not be
    read. The setting decides which flags Swift commands carry, so falling back
    to the default is said out loud rather than done quietly."""
    from rite_ai.config.models import ProjectConfig, SandboxConfig
    from rite_ai.config.parse import parse_config

    config = parse_config(root / ".rite" / "config.yaml")
    if isinstance(config, ProjectConfig):
        return config.sandbox, ""
    return SandboxConfig(), (
        f"config.yaml could not be read ({config.message}); commands assume the "
        "default sandbox setting"
    )


def _echo_command_rows(module, root: Path, sandbox, indent: str) -> str | None:
    """Print where each of `module`'s commands came from and what it resolved
    to. Returns the test command, or None when there is not one."""
    from rite_ai.cli.init.detect import command_rows, module_commands

    cmds = module_commands(module, root, sandbox)
    for key, origin, value in command_rows(cmds):
        shown = f"`{value}`" if value else "—"
        click.echo(f"{indent}{key:<8} {origin:<10} {shown}")
    if cmds.note:
        click.echo(f"{indent}note: {cmds.note}")
    return cmds.test


def _tracked_runtime_state(root: Path) -> list[str]:
    """Runtime state this repo is committing, if any.

    `.rite/` holds two different kinds of file, and SPEC §8's own layout
    draws the line: configuration `rite init` writes, and "runtime state
    ... written by rite as it runs". The second kind is meaningful only
    on the machine that wrote it — a claims ledger naming this machine's
    workers, a heartbeat that is a local clock reading, an undelivered
    outbox, one machine's cron log — so committing it puts one machine's
    ephemera in a shared history and produces a conflict on every pull.

    Checked against what git ACTUALLY tracks rather than against
    `.gitignore`, because those answer different questions: adding an
    ignore rule does not untrack a file that is already committed, so a
    project that adopted the rule later still carries the old ephemera
    and would otherwise read as fixed.

    Reports; never removes. Untracking is `git rm --cached`, which
    destroys no content but is still the user's call in their own repo —
    the same reasoning that makes `rite init` refuse to write into a
    shared hooks directory rather than surprise anyone.
    """
    import subprocess

    from rite_ai.cli.init.scaffold import AUTHORED_CONFIG

    if not (root / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            # Ask for everything rite writes, then subtract what is meant to
            # be shared. Asking for a list of runtime patterns instead meant
            # the check could only find what someone had remembered to add
            # to that list — it was already missing `.rite/handover/`.
            ["git", "ls-files", "--", ".rite", "workers"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    def _is_shared(path: str) -> bool:
        # Three AUTHORED_CONFIG entries are DIRECTORIES (`.rite/context/`,
        # `.rite/kb/`, `.rite/spec/`), and a prefix match calls everything
        # inside them
        # shared. That is right for their contents and wrong for the one
        # thing in them that is not content: the `<name>.lock` sidecar
        # `rite_ai.state.locked()` flocks. Without this, a project that had
        # already committed `.rite/context/INDEX.md.lock` read as clean —
        # the exact "adding an ignore rule does not untrack" case this
        # function exists to catch, missed because the file sat under a
        # shared directory.
        if path.endswith(".lock"):
            return False
        return any(path == entry or path.startswith(entry) for entry in AUTHORED_CONFIG)

    tracked = sorted(
        {
            line.strip()
            for line in proc.stdout.splitlines()
            if line.strip() and not _is_shared(line.strip())
        }
    )
    if not tracked:
        return []
    shown = ", ".join(tracked[:4]) + ("…" if len(tracked) > 4 else "")
    return [
        f"{len(tracked)} file(s) ({shown}) — one machine's ephemera in a "
        "shared history, and a merge conflict on every pull. Stop tracking "
        "them with `git rm --cached` and add them to .gitignore (`rite init` "
        "does this for new projects)."
    ]


def _gate_root() -> Path:
    """Where the publish gate scans.

    The gate is a REPOSITORY operation — it lists tracked files and walks
    history — so its root is the git worktree, not the rite project. Those
    are usually the same directory and are not always: rite's own repo has a
    `.rite/` holding the gate's suppressions and the review checklist, and is
    not a rite project (no `PROJECT_MARKERS`). Resolving the gate through the
    project marker alone would fall back to cwd there, so running
    `rite publish check` from a subdirectory would scan that subdirectory and
    silently miss `.rite/gitleaksignore` — a gate that reports clean about
    the wrong tree.

    Project root wins when there is one, so a scanned project still uses its
    own config; otherwise the git toplevel; cwd only if neither answers.
    """
    import subprocess

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if _is_project(parent):
            return parent
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return cwd
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    return cwd


def _has_project_in_scope() -> bool:
    """Unlike `_find_project_root`, which always returns SOMETHING (falls
    back to cwd), this distinguishes "found a real .rite/" from "found
    nothing" — the distinction `rite status`'s aggregation mode needs to
    decide whether to show one project's detail or the cross-project
    summary (SPEC §8.9). Same marker as `_find_project_root`, and for the
    same reason — a directory with a `.rite/` in it is not necessarily a
    project."""
    if os.environ.get(PROJECT_ROOT_ENV):
        return True
    cwd = Path.cwd()
    return any(_is_project(parent) for parent in [cwd, *cwd.parents])


def _claims_path() -> Path:
    root = _find_project_root()
    return root / ".rite" / "claims.json"


@click.group(cls=RiteGroup)
@click.version_option(__version__, "-v", "--version", prog_name="rite")
def cli() -> None:
    """Multi-session Claude coordination for teams."""


@cli.command()
@click.argument("directory", default=".", type=click.Path(exists=True))
@click.option(
    "--config",
    "config_file",
    default=None,
    type=click.Path(exists=True),
    help="Preset file answering some or all questions (SPEC §9.3).",
)
@click.option(
    "--yes",
    "yes",
    is_flag=True,
    default=False,
    help="Never prompt — use the preset where given, documented defaults "
    "otherwise. Makes init fully non-interactive and scriptable.",
)
def init(directory: str, config_file: str | None, yes: bool) -> None:
    """Initialise a rite project — interactive questionnaire.

    Creates `.rite/` (brief, modules, config, the context and KB indexes,
    and a review checklist) alongside a role-appropriate CLAUDE.md and
    `.claude/` agents and commands. `--config` answers some or all of the
    questions from a preset file; `--yes` takes documented defaults for
    whatever the preset leaves open. Together they make init fully
    non-interactive, so it can run from a script or a provisioning step.

    Examples:
      rite init
      rite init --config team-defaults.yaml
      rite init --config team-defaults.yaml --yes
    """
    from rite_ai.cli.init import run_init

    result = run_init(
        Path(directory).resolve(),
        config_path=Path(config_file) if config_file else None,
        yes=yes,
    )
    if result.status == "error":
        click.echo(result.message, err=True)
        raise SystemExit(1)
    if result.status in ("updated", "unchanged"):
        click.echo(result.message)
        return
    if result.status == "already_initialized":
        # Nothing was created. Exiting 0 told every script that init had
        # succeeded, and `rite init --yes` in particular has no other way
        # to report the refusal — it never prompts.
        click.echo(result.message, err=True)
        raise SystemExit(1)


def _tool_runs(binary: str, probe: list[str]) -> tuple[bool, str]:
    """Whether an external tool actually executes, and what it reports.

    A checksum proves a file has not changed; running it proves it works.
    Bounded and non-fatal: a probe that times out or cannot be spawned is
    reported as broken rather than raised, because `rite doctor` exists to
    describe problems, not to become one."""
    import subprocess

    try:
        proc = subprocess.run(
            [binary, *probe],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out after 15s"
    except OSError as e:
        return False, f"could not execute: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        first = detail[0][:120] if detail else "no output"
        return False, f"exited {proc.returncode}: {first}"
    out = (proc.stdout or proc.stderr).strip().splitlines()
    if not out:
        return True, "runs"
    # Elided visibly. A hard cut mid-word reads as part of the sentence
    # that follows it rather than as a truncated version string.
    first = out[0].strip()
    return True, first if len(first) <= 48 else _elide(first, 47)


# Brackets the elision below has to leave balanced.
_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _elide(text: str, keep: int) -> str:
    """`text` cut to `keep` characters, marked, and left readable.

    The mark alone is not enough when the cut falls INSIDE a bracket,
    because the caller then puts its own parenthetical after it. Measured
    against the installed yoloAI, whose version line is `yoloai version
    0.11.0 (commit: 95a6b8ee…)`, in `rite doctor`:

        tool yoloai: yoloai version 0.11.0 (commit: 95a6b8ee…
            (sandbox.enabled is false — not required)      <- one line

    Two opening brackets, one closing one, and the second parenthetical
    reads as though it were still inside the first — so the line says the
    commit is "sandbox.enabled is false". Closing what was opened costs
    one character per bracket and the line reads as what it is: a
    truncated version, then a note.
    """
    kept = text[:keep]
    unclosed: list[str] = []
    for char in kept:
        if char in _BRACKETS:
            unclosed.append(_BRACKETS[char])
        elif unclosed and char == unclosed[-1]:
            unclosed.pop()
    return kept + "…" + "".join(reversed(unclosed))


def api_key_notice(environ) -> str | None:
    """What `doctor` says when `ANTHROPIC_API_KEY` is exported, or None.

    Nothing else in rite mentions it, and every session rite starts inherits
    it: `rite pool fill` spawns with this environment and sandboxed Workers
    get a copy of it. Claude Code uses the key ahead of a subscription login
    and of CLAUDE_CODE_OAUTH_TOKEN, and without asking in non-interactive
    mode — so an overnight run can be on the key without anyone choosing it.

    A row, not a problem: a key exported on purpose is a legitimate setup,
    the same line `sandbox_environment` draws for a venv put on PATH. The
    value is never printed, only that it is set.
    """
    if not environ.get("ANTHROPIC_API_KEY"):
        return None
    return (
        "env ANTHROPIC_API_KEY: set — every Claude session rite starts from "
        "this shell inherits it (`rite pool fill`, sandboxed Workers), and "
        "Claude Code uses it instead of your Claude subscription login and "
        "CLAUDE_CODE_OAUTH_TOKEN, without asking in non-interactive sessions "
        "(https://code.claude.com/docs/en/authentication"
        "#authentication-precedence). If Workers should run on your "
        "subscription, `unset ANTHROPIC_API_KEY` before starting them."
    )


@contextmanager
def _doctor_check(label: str, problems: list[str]):
    """Run one check; a raise becomes a reported problem, never a crash.

    `rite doctor` is the first command a new user runs and the one meant to
    explain a broken machine to them. A traceback explains nothing and
    stops every later check. Measured on a tester's machine: yoloai on PATH
    but built for another architecture raised `OSError: [Errno 8] Exec
    format error` out of the sandbox round-trip and killed the command
    mid-report.

    SystemExit and KeyboardInterrupt pass through: doctor's own exit and
    the user's, not a failing check.
    """
    try:
        yield
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        click.echo(f"{label}: CHECK FAILED — {type(e).__name__}: {e}")
        problems.append(f"{label} check failed: {type(e).__name__}: {e}")


@cli.command()
def doctor() -> None:
    """Is this healthy? (SPEC §9.8) Token presence, external tool
    availability, `.rite/` integrity, module sync state, git remote
    reachability, and schedule validation — as distinct from `rite
    status`'s "what's happening?"."""
    problems: list[str] = []
    # Structural, not one call site: each section runs inside a guard, and
    # this outer one catches whatever a future check adds outside them, so
    # the command cannot end in a traceback.
    with _doctor_check("doctor", problems):
        _doctor_report(problems)
    if problems:
        click.echo(f"\n{len(problems)} problem(s) found")
        raise SystemExit(1)
    click.echo("\nok")


def _doctor_report(problems: list[str]) -> None:
    """Every check doctor runs, appending to `problems`."""
    import shutil

    from rite_ai.update import install_origin

    # The version alone does not say which build this is: `install.sh`
    # installs from a git tag, and a tag can be re-pointed (one was).
    origin = install_origin()
    click.echo(f"rite {__version__}" + (f" ({origin})" if origin else ""))

    from rite_ai.config.parse import ParseError as ParseErrorType

    root = _find_project_root()
    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        # Non-zero, settling SPEC §9.11's open question. The case for 0 was
        # that nothing was checked so nothing is unhealthy; the case for
        # non-zero is that "not set up" is exactly what a guard wants to
        # catch, and §9.11's own settled convention decides it — "a command
        # that answers a question answers it in the exit code, not only in
        # prose", the rule that came from `rite credential check` printing
        # `not_found` and exiting 0. `rite doctor && rite claim ...`
        # succeeded in a directory where rite was never set up.
        #
        # Exit 1 rather than 2: this is doctor's own "not healthy enough to
        # proceed", not a Click usage error about the arguments given.
        click.echo(
            f"no .rite/ directory found under {root} — this is not a rite "
            "project. Run `rite init` here, or run doctor from inside one.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"project: {root}")

    # PARSED, not stat'd. "found" answered whether a file was on disk,
    # which is not the question — an unparseable brief.yaml, or one
    # missing the required `project.name`, reported "found" and doctor
    # went on to print "ok", while every command that calls
    # `load_project` failed on that same file.
    from rite_ai.config.parse import parse_brief

    brief = rite_dir / "brief.yaml"
    parsed_brief = parse_brief(brief)
    if isinstance(parsed_brief, ParseErrorType):
        if brief.exists():
            click.echo(f"brief.yaml: UNUSABLE — {parsed_brief.message}")
            problems.append(f"brief.yaml: {parsed_brief.message}")
        else:
            click.echo("brief.yaml: missing")
            problems.append("brief.yaml missing")
    else:
        click.echo(f"brief.yaml: ok ({parsed_brief.name})")
        # Reported, not a problem: the project works. But answers a session
        # wrote there under the old first-session instruction reach nobody.
        from rite_ai.project_spec import orphaned_enrichment_notice

        orphan = orphaned_enrichment_notice(root)
        if orphan:
            click.echo(f"brief.yaml: {orphan}")

    from rite_ai.credentials.store import resolve as cred_resolve

    creds = _project_credentials()
    for name in ["jira_token", "jira_email", "github_token"]:
        click.echo(f"credential {name}: {cred_resolve(name, creds).describe()}")
    api_key = api_key_notice(os.environ)
    if api_key:
        click.echo(api_key)

    # RUN them. Being on PATH is not the same as working: a shim, a
    # half-finished install or an incompatible build all answer
    # `shutil.which` and then fail the moment anything depends on them.
    # The publish gate depends on gitleaks, so a gitleaks that is present
    # and broken degrades the gate silently.
    from rite_ai.gate import gitleaks_runner

    missing_tools: set[str] = set()
    with _doctor_check("tools", problems):
        for tool, probe in (("gitleaks", ["version"]), ("gh", ["--version"])):
            found = shutil.which(tool)
            if not found:
                missing_tools.add(tool)
                if tool == "gitleaks":
                    # Unconditionally load-bearing, unlike `gh`. `rite init`
                    # installs a pre-push hook and a CI job that both run the
                    # gate, and the gate refuses to run without gitleaks
                    # rather than scan partially — so "not found" means this
                    # project has no secret scanning at all.
                    #
                    # It used to print this line and stop there: `not found`
                    # was never appended to `problems`, so doctor exited 0
                    # and reported a healthy project. Present-and-broken was
                    # a problem and absent was not, which is backwards —
                    # absent is the commoner case and disables just as much.
                    #
                    # The remedy names the release binaries, not only a
                    # package manager. Measured on a tester's machine: no
                    # gitleaks AND no Homebrew, which made "brew install
                    # gitleaks" a dead end; she hand-grepped the staged
                    # files instead and said so.
                    # Which of the two outcomes applies is the fact that
                    # decides what the reader does next, and doctor knows it
                    # — `gate_hook_status` is what the "publish gate hook"
                    # check below prints. Asked here rather than hedged.
                    from rite_ai.gate.hook import gate_hook_status

                    click.echo(
                        "tool gitleaks: not found — "
                        + gitleaks_runner.missing_gitleaks_consequence(
                            gate_hook_status(root).active
                        )
                        + ". "
                        + gitleaks_runner.HOW_TO_INSTALL
                    )
                    problems.append(
                        "gitleaks is not installed — the publish gate cannot run"
                    )
                    continue
                # Whether this is a PROBLEM depends on the project: `gh` is
                # load-bearing only once Workers are sandboxed, which is
                # known further down, where the config is parsed.
                click.echo(f"tool {tool}: not found")
                continue
            ok, detail = _tool_runs(found, probe)
            if ok:
                click.echo(f"tool {tool}: {detail} ({found})")
            else:
                click.echo(f"tool {tool}: BROKEN at {found} — {detail}")
                problems.append(f"tool {tool} is on PATH but does not run: {detail}")

    from rite_ai.config.parse import ParseError, load_project, parse_modules
    from rite_ai.context import check_integrity

    for issue in check_integrity(root):
        click.echo(f"context: {issue.kind} — {issue.detail}")
        problems.append(f"context {issue.kind}: {issue.detail}")

    modules = parse_modules(rite_dir / "modules.yaml")
    if isinstance(modules, ParseError):
        click.echo(f"modules.yaml: {modules.message}")
        problems.append(f"modules.yaml: {modules.message}")
        modules = []

    from rite_ai.workspace import git_ops

    module_sandbox, sandbox_warning = _resolved_sandbox(root)
    for m in modules:
        module_dir = root / m.path
        if not git_ops.is_git_repo(module_dir):
            click.echo(f"module {m.name}: not a git repository at {m.path}")
            problems.append(f"module {m.name} is not a git repository")
            continue
        # A module that is ITSELF a rite project resolves to itself, and
        # `rite prepare` clones it UNDER this project root — so a session
        # started in `workers/<w>/<module>/` walks up, finds that clone's own
        # marker first, and writes claims into a private ledger. Nothing is
        # shared, nothing ever collides, and the run reports zero refusals:
        # the exclusion guarantee absent, presenting as a flawless run.
        #
        # Hardening the marker to a FILE fixed the case where the inner repo
        # merely carries a committed `.rite/` (rite's own repo is that case).
        # It cannot fix this one: a scaffolded project tracks brief.yaml and
        # modules.yaml by design, because `scaffold.AUTHORED_CONFIG`
        # re-includes them. Only the explicit override answers it, and
        # nothing else tells anyone — which is what this row is for.
        if _is_project(module_dir):
            click.echo(
                f"module {m.name}: is itself a rite project "
                f"({m.path}/.rite/). A session started inside it resolves to "
                f"IT, not to this project, so its claims go to a private "
                f"ledger and never collide with anyone else's. Set "
                f"{PROJECT_ROOT_ENV}={root} in that session's environment."
            )
            problems.append(
                f"module {m.name} is itself a rite project — claims made "
                f"inside it would not be shared"
            )
        clean = git_ops.is_clean(module_dir)
        if isinstance(clean, git_ops.GitError):
            click.echo(f"module {m.name}: {clean.message}")
            problems.append(f"module {m.name}: {clean.message}")
        elif not clean:
            click.echo(f"module {m.name}: uncommitted changes")
        else:
            click.echo(f"module {m.name}: clean")
        if m.url:
            fetch_err = git_ops.fetch(module_dir)
            if isinstance(fetch_err, git_ops.GitError):
                if fetch_err.kind == "network":
                    click.echo(f"module {m.name}: remote unreachable (offline?)")
                else:
                    click.echo(f"module {m.name}: remote error — {fetch_err.message}")
                    problems.append(
                        f"module {m.name} remote error: {fetch_err.message}"
                    )
            else:
                click.echo(f"module {m.name}: remote reachable")
        # Which commands a session will run for this module, and where each
        # came from. A Worker with no test command cannot check its own work,
        # and the loop rite exists to run breaks there silently, at whatever
        # hour the Worker reaches that step — so a missing `test` is a
        # problem, not a note, and the row says exactly what to add.
        click.echo(f"module {m.name}: commands")
        if sandbox_warning:
            click.echo(f"  note: {sandbox_warning}")
        if not _echo_command_rows(m, root, module_sandbox, "  "):
            click.echo(
                "  no test command — add one to .rite/modules.yaml:\n"
                f"    {m.name}:\n"
                "      commands:\n"
                "        test: <the command that runs this module's tests>"
            )
            problems.append(f"module {m.name} has no test command")

    # The WORKERS' checkouts, which the loop above never looked at. Every
    # module line it prints is about `<root>/<module>` — the project's own
    # copy, which nothing works in. The work happens in
    # `workers/<name>/<module>`, and that is the one place in a rite
    # project whose contents can be irreplaceable.
    #
    # Measured on a worker killed between claim and commit: `rite doctor`
    # reported `module reviewer: clean` while `workers/w1/reviewer` held a
    # modified README and a new file on the ticket branch, committed
    # nowhere. Not one of `start`, `status`, `watchdog`, `doctor` or
    # `handover show` mentioned it — so the most valuable thing the dead
    # session left was the one thing the next session could not discover.
    #
    # Local git only: no fetch, no network, one `git status` and one `git
    # rev-list` per checkout that exists.
    from rite_ai.workspace import unsaved_work

    workers_dir = root / "workers"
    if workers_dir.is_dir():
        for worker_dir in sorted(p for p in workers_dir.iterdir() if p.is_dir()):
            for item in unsaved_work(worker_dir):
                click.echo(
                    f"worker {worker_dir.name}: unsaved work — {item.describe()}"
                )
                problems.append(
                    f"worker {worker_dir.name} has unsaved work in "
                    f"{item.module} — it exists only in "
                    f"workers/{worker_dir.name}/{item.module}"
                )

    # The publish gate is only a guarantee while git will actually run it,
    # and that stops being true without anyone touching the project —
    # `core.hooksPath` can be set globally, long after `rite init` checked.
    # `init` reports this once, at creation; nothing asked again until now.
    from rite_ai.gate.hook import gate_hook_status

    with _doctor_check("publish gate hook", problems):
        for label, repo_dir in [("project root", root)] + [
            (f"module {m.name}", root / m.path) for m in modules
        ]:
            status = gate_hook_status(repo_dir)
            if status.state == "not_a_repo":
                continue
            if status.active:
                click.echo(f"publish gate hook ({label}): active")
            else:
                click.echo(f"publish gate hook ({label}): {status.detail}")
                problems.append(f"publish gate does not run on push from {label}")

    # The other half, and per §11.5.1 the load-bearing half: the hook above is
    # disarmable by this machine's own git config with no signal, and CI is
    # the only layer local configuration cannot switch off. Doctor checked the
    # disarmable layer and not this one — the same asymmetry that let the hook
    # defect exist, since nobody asked the question.
    #
    # Project root only, and said so: `rite init` writes the workflow there
    # alone, because the gate reads its scan patterns and suppressions from
    # `.rite/`, which lives there. A bare "active" would otherwise read as
    # "every repo is covered".
    from rite_ai.gate.ci import ci_workflow_status

    with _doctor_check("publish gate CI", problems):
        ci_gate = ci_workflow_status(root)
        if ci_gate.state != "not_a_repo":
            if ci_gate.active:
                click.echo(
                    "publish gate CI (project root): active — a job runs the gate"
                )
            else:
                click.echo(f"publish gate CI (project root): {ci_gate.detail}")
                problems.append("publish gate does not run in CI")

    # The property every claim rests on, measured rather than assumed.
    with _doctor_check("file locking", problems):
        if not exclusion_holds(root / ".rite"):
            click.echo(
                f"file locking: DOES NOT WORK under {root / '.rite'} — two "
                "workers can be granted the same path here and both told "
                "'claimed'. A network mount or a VM shared folder looks like "
                "this. Move the project to a local disk, or run one worker."
            )
            problems.append("file locking does not exclude in this project")
        else:
            click.echo("file locking: excludes (claims can be relied on)")

    for tracked in _tracked_runtime_state(root):
        click.echo(f"git tracks runtime state: {tracked}")
        problems.append("git tracks .rite/ runtime state")

    project = load_project(root)
    if isinstance(project, list):
        # Previously this branch did nothing at all: a project whose
        # config would not load simply skipped the schedule and sandbox
        # rows, so a broken config made doctor QUIETER rather than
        # louder, and it still reached "ok".
        for err in project:
            # brief.yaml and modules.yaml each already have their own row
            # above. Repeating them here counted one broken file as two
            # problems and printed it twice.
            if Path(err.file).name in ("brief.yaml", "modules.yaml"):
                continue
            click.echo(f"config: {err.file}: {err.message}")
            problems.append(f"config {err.file}: {err.message}")
    else:
        from rite_ai.coordination.config_check import coordination_problems
        from rite_ai.coordination.identity import (
            enrolment,
            hosted_managers,
            this_manager,
        )
        from rite_ai.sandbox import is_installed, verify_sandbox
        from rite_ai.schedule import validate_schedule

        # Only a PROBLEM when `sandbox.enabled` is set — a project that
        # never asked for sandboxing is not unhealthy for lacking yoloAI.
        #
        # Sandboxing is verified by running a sandbox, not by finding a
        # file. `shutil.which` measured "something is installed under that
        # name", which is what let a present-but-broken yoloAI switch the
        # worker cap off while reporting success. The round trip costs
        # ~2s, so it runs only when this project actually asked for
        # sandboxing; with the feature off, the row says what it checked
        # and does not claim more.
        if project.config.sandbox.enabled:
            # A sandboxed session cannot use the Claude login in the
            # keychain. Without a stored token it starts, reports that its
            # login has expired, and does nothing — the kind of failure
            # that looks like a Worker quietly working.
            from rite_ai.credentials.store import NOT_FOUND

            # Its row is printed here, not with the credentials above: it is
            # needed only when this project sandboxes its Workers.
            if "gh" in missing_tools:
                # Same treatment as the missing Claude login: a sandboxed
                # Worker's pushes authenticate through `gh`, so without it
                # every push from a sandbox fails and the work is lost with
                # the sandbox.
                click.echo(
                    "tool gh: not found — sandbox.enabled is true and a "
                    "sandboxed Worker's pushes authenticate through `gh`, so "
                    "every push from a sandbox fails. Install GitHub's `gh` "
                    "CLI (https://cli.github.com); it needs no login of its "
                    "own, rite passes the token in."
                )
                problems.append("gh is not installed but Workers are sandboxed")
            claude_login = cred_resolve("claude_token", creds)
            if claude_login.tier == NOT_FOUND:
                # Doctor prints rows and only counts problems, so the
                # guidance belongs in the row.
                exported = (
                    " CLAUDE_CODE_OAUTH_TOKEN is exported in this shell, but "
                    "that reaches only sandboxes started from this shell."
                    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
                    else ""
                )
                click.echo(
                    f"credential claude_token: {claude_login.describe()} — "
                    "sandbox.enabled is true but no Claude login is stored for "
                    "sandboxes, so a sandboxed Worker starts and does nothing. "
                    "Run `claude setup-token`, then `rite credential set claude`."
                    + exported
                )
                problems.append("no Claude login stored for sandboxed Workers")
            else:
                click.echo(f"credential claude_token: {claude_login.describe()}")
            check = verify_sandbox(project.config.sandbox.backend)
            if check.ok:
                click.echo(
                    f"sandbox ({check.backend}): verified — {check.detail} "
                    f"in {check.elapsed_ms} ms"
                )
            elif not check.installed:
                click.echo(f"sandbox ({check.backend}): {check.detail}")
                problems.append("sandbox.enabled is true but yoloai is not installed")
            else:
                click.echo(f"sandbox ({check.backend}): NOT WORKING — {check.detail}")
                problems.append(
                    f"sandbox.enabled is true but no sandbox could be "
                    f"started: {check.detail}"
                )
        elif is_installed():
            click.echo(
                "sandbox: yoloai is installed, not verified (sandbox.enabled is false)"
            )
        else:
            click.echo(
                "sandbox: yoloai not installed (sandbox.enabled is false — "
                "not required)"
            )

        # Lists what is configured AND checks it resolves — which is why
        # `rite spec` has no `list`. rite cannot tell whether a spec is
        # CURRENT, and says so in the Worker's own CLAUDE.md; what it can
        # tell is that a path has been renamed or deleted out from under
        # the pointer, which turns "read the design here" into a dead end.
        if project.config.spec.paths:
            for rel in project.config.spec.paths:
                target = root / rel
                if target.exists():
                    kind = "dir" if target.is_dir() else "file"
                    click.echo(f"spec {rel}: found ({kind})")
                else:
                    click.echo(f"spec {rel}: MISSING — nothing at that path")
                    problems.append(
                        f"spec path '{rel}' does not exist — Workers are "
                        f"pointed at it and will find nothing"
                    )
            if project.config.spec.convention:
                click.echo(f"spec convention: {project.config.spec.convention}")

        # Asked in both branches, because it has something to say in both: what
        # to do when no spec is registered, and — once one is — that a spec too
        # big to hold has a command for loading part of it. It says nothing the
        # rest of the time.
        # Same wording as `rite start`: reported, never a problem — a project
        # without a spec yet is not misconfigured, and one with a large spec
        # and no digest is not either.
        from rite_ai.project_spec import spec_notices

        for notice in spec_notices(root, project.config, [m.path for m in modules]):
            click.echo(notice)

        # Nothing else tells anyone the refresh exists, and a project whose
        # CLAUDE.md predates a fix cannot report the fix it is missing. A
        # NOTE, not a problem: being behind is normal between upgrades, and
        # what to do about an edited section is the user's call.
        with _doctor_check("generated files", problems):
            from rite_ai.update.refresh import pending

            plan = pending(root)
            if plan:
                parts = []
                if plan.behind:
                    parts.append(f"{len(plan.behind)} out of date")
                if plan.contested:
                    parts.append(
                        f"{len(plan.contested)} changed by you or an older rite"
                    )
                click.echo(
                    f"generated files: {', '.join(parts)} — "
                    "`rite update --files-only --dry-run` shows what would change"
                )
            else:
                click.echo("generated files: current")

        # EXC-2. The generated CLAUDE.md tells every Worker to run these "as
        # written rather than inventing one", and warns in the next sentence
        # that a command wrong in a way that still exits 0 is
        # indistinguishable from a passing suite — while nothing checked
        # these for exactly that. A recorded `pytest | tail -1` reports a
        # green suite for ever, on every Worker, honestly.
        with _doctor_check("recorded commands", problems):
            from rite_ai.verdicts import command_problems

            blind: list[str] = []
            for module in modules:
                recorded = {
                    key: getattr(module.commands, key, "") or ""
                    for key in ("build", "test", "lint")
                }
                blind.extend(command_problems(recorded, f"module {module.name}"))
            for line in blind:
                click.echo(line)
                problems.append(line)
            if not blind:
                click.echo("recorded commands: each one can still fail")

        # A machine that can never run a loop should find that out here
        # rather than by trying. `rite loop start` refuses clearly when tmux
        # is missing, but only once somebody reaches for it — and the loop is
        # the one thing in 0.5.0 a reader is most likely to reach for last.
        with _doctor_check("loop", problems):
            from rite_ai.loop.session import _tmux
            from rite_ai.loop.session import status as loop_status

            if _tmux() is None:
                click.echo(
                    "loop: tmux is not installed, so `rite loop start` cannot "
                    "run one here — `rite loop run` still works in a terminal "
                    "you leave open"
                )
            else:
                live = loop_status(root)
                click.echo(
                    f"loop: running as {live.session}"
                    if live.running
                    else "loop: not running (`rite loop start`)"
                )

        # Sandbox litter. The cap counts this project's sandboxes now, so
        # leftovers no longer present as capacity — but they are still there,
        # holding disk and, in four of the six measured, somebody's
        # uncommitted changes. Named so the housekeeping is visible; never
        # destroyed, because `agent: idle` does not distinguish abandoned
        # from between-turns and a sandbox is the only copy of its own work.
        if project.config.sandbox.enabled:
            with _doctor_check("sandboxes", problems):
                from rite_ai.label import project_digest
                from rite_ai.sandbox import CountUnavailable, list_rite_sandboxes

                found = list_rite_sandboxes()
                if isinstance(found, CountUnavailable):
                    click.echo(f"sandboxes: could not be listed — {found.reason}")
                else:
                    digest = project_digest(root)
                    mine = [s for s in found if f"-{digest}-" in s.name]
                    others = [s for s in found if s not in mine]
                    click.echo(
                        f"sandboxes: {len(mine)} for this project, "
                        f"{len(others)} other `rite-` sandbox(es) on this machine"
                    )
                    # Say the bound even when there isn't one. An absent
                    # limit and a limit nothing reads produce identical
                    # behaviour — no refusals — and telling them apart is
                    # the whole subject of this week's defects.
                    from rite_ai.machine import max_sandboxes

                    bound = max_sandboxes()
                    click.echo(
                        f"sandboxes: machine bound {bound}, {len(found)} running"
                        if bound is not None
                        else "sandboxes: no machine bound set — this box will "
                        "start as many as every project's cap allows "
                        '(set "max_sandboxes" in ~/.rite/machine.json)'
                    )
                    for entry in others:
                        where = f" ({entry.workdir})" if entry.workdir else ""
                        click.echo(
                            f"sandboxes:   {entry.name}{where} — "
                            + (
                                "holds unapplied changes, do NOT destroy"
                                if entry.has_changes
                                else "no changes; `yoloai destroy "
                                f"{entry.name}` frees it"
                            )
                        )

        # Phase 2. Settings that cannot work are reported whether or not a
        # remote is set: half a `coordination:` block does not fail, it
        # silently never elects anybody.
        coordination = project.config.coordination
        for problem in coordination_problems(coordination):
            click.echo(problem)
            problems.append(problem)
        # Needs the machine, not just the config: the same committed
        # `config.yaml` is complete on one machine and not on another, which
        # is the whole reason the name is not in it.
        not_enrolled = enrolment(root, coordination)
        if not_enrolled:
            click.echo(not_enrolled)
            problems.append(not_enrolled)

        with _doctor_check("manager names", problems):
            # Reported, never refused. The Manager name is committed and
            # shared; the alias is this machine's alone. So the
            # collision exists on ONE laptop and the fix is a choice
            # between two names only its owner can make.
            from rite_ai.dispatch import default_dispatch_dir, load_registry
            from rite_ai.managers import name_collisions

            registry = load_registry(default_dispatch_dir())
            shadowed = name_collisions(
                [r.name for r in coordination.manager_roles], registry.projects
            )
            for name in shadowed:
                where = registry.projects[name].path
                # ECHOED, not merely appended. `problems` is COUNTED at the
                # end and never printed, so a remedy that goes only in there
                # is a remedy nobody reads: the user sees the collision
                # named and no way out of it. Caught by its own test.
                trouble = (
                    f"'{name}' is both a Manager here and your alias for "
                    f"{where}. `rite start {name}` starts the Manager — "
                    f"Manager names are matched first — so that alias "
                    f"never resolves. Rename the alias "
                    f"(`rite projects remove {name}`, then add it under "
                    f"another name), or rename the Manager in "
                    f"config.yaml if the team agrees."
                )
                click.echo(f"manager names: {trouble}")
                problems.append(trouble)
            if not shadowed:
                click.echo("manager names: no alias shadowed")

        # RL-42. `local:large` names a tier, not a runtime, and two projects'
        # "large" are different machines — so the endpoint is probed rather
        # than assumed. An engine that is not there fails every subtask routed
        # to it as infrastructure (RL-47): honest reports, all night, and no
        # progress.
        local_roles = [r for r in coordination.manager_roles if r.is_local]
        if local_roles:
            with _doctor_check("local engines", problems):
                from rite_ai.local.engine_probe import probe_local_engines

                for probe in probe_local_engines(local_roles):
                    click.echo(f"local engine {probe.manager}: {probe.detail}")
                    for problem in probe.problems:
                        click.echo(problem)
                        problems.append(problem)

        if coordination.remote and not not_enrolled:
            # §2.3: the Owner "detects stalled Managers and surfaces them to
            # the human". Reading is safe on any machine — it needs no
            # identity and writes nothing.
            with _doctor_check("coordination state", problems):
                from datetime import UTC, datetime

                from rite_ai.coordination.git_backend import GitStateLayer
                from rite_ai.coordination.overview import (
                    format_overview,
                    read_overview,
                    recent_events,
                )

                layer_for_overview = GitStateLayer(
                    coordination.remote,
                    root / ".rite" / "coordination-cache.git",
                    state_branch=coordination.state_branch,
                )
                overview = read_overview(
                    layer_for_overview,
                    coordination,
                    now=datetime.now(UTC),
                    heartbeat=project.config.heartbeat,
                    this_machine=this_manager(root),
                    # Every Manager this box runs, not just the primary: on
                    # rite local one machine hosts several, and the ones it
                    # hosts are the ones whose silence it can do something
                    # about (RL-60).
                    hosted=tuple(hosted_managers(root)),
                )
                for line in format_overview(overview):
                    click.echo(line)
                events = recent_events(layer_for_overview)
                for line in events:
                    click.echo(f"coordination: recently — {line}")
                for note in overview.notes:
                    click.echo(f"coordination: {note}")
                for problem in overview.problems:
                    click.echo(f"coordination: {problem}")
                    problems.append(f"coordination: {problem}")

        # P2-1e. Only once a coordination remote is configured: the probe
        # pushes, so a single-machine project must never run it.
        if coordination.remote:
            with _doctor_check("coordination remote", problems):
                from rite_ai.coordination.remote_probe import probe_force_push

                probe = probe_force_push(
                    coordination.remote, coordination.state_branch, root
                )
                if probe.ok:
                    click.echo(f"coordination remote: {probe.detail}")
                else:
                    click.echo(
                        f"coordination remote: {probe.detail}"
                        + (f" — {probe.remedy}" if probe.remedy else "")
                    )
                    problems.append(f"coordination remote: {probe.detail}")
                if probe.leftover_ref:
                    click.echo(
                        f"coordination remote: could not delete the probe branch "
                        f"`{probe.leftover_ref}` — delete it by hand"
                    )

        schedule_problems = validate_schedule(
            project.config.schedule, project.config.sandbox.max_concurrent_workers
        )
        for p in schedule_problems:
            click.echo(f"schedule: {p}")
        problems.extend(schedule_problems)

    return


def _warn_if_unregistered(worker: str) -> None:
    """Say so, at the moment it happens, when state is being recorded for
    a worker nothing will watch.

    Every command here takes `--worker <anything>` and reports success.
    But `rite watchdog` only watches workers with a
    `workers/<name>/worker.yml` manifest, so on a project where nobody
    ran `rite add worker`, a claim and a heartbeat are both accepted and
    a dead session is never detected. The watchdog reports this too, but
    only to whoever runs it — the person typing the wrong worker name is
    here, now.

    A warning, never a refusal: the claim and the beat are real records
    and refusing them would lose information. Written to stderr so it
    cannot corrupt anything parsing stdout."""
    root = _find_project_root()
    manifest = root / "workers" / worker / "worker.yml"
    if manifest.is_file():
        return
    from rite_ai.watchdog import _pool_slot_workers

    if worker in _pool_slot_workers(root):
        # A pooled coordinator claims under its slot name and never has a
        # manifest; `.rite/pool.json` is its register and `rite pool
        # status` is what watches it.
        return
    click.echo(
        f"warning: worker '{worker}' is not registered ({manifest} does not "
        f"exist). `rite watchdog` only watches registered workers, so a "
        f"stall by '{worker}' will never be reported. Register it with "
        f"`rite add worker {worker}`.",
        err=True,
    )


# --- Claims ---


def _warn_if_unpublished(ledger) -> None:
    """Say when a release did not reach the other machines.

    The local ledger and the published one cannot be updated atomically, so
    a failed publish leaves a path claimed as far as the rest of the fleet
    can see. Nothing takes that back on its own — claims expire on a lapsed
    HEARTBEAT, and this machine is healthy — so the person standing here is
    the one who can fix it.
    """
    from rite_ai.coordination.publish import NotPublished

    outcome = getattr(ledger, "last_publish", None)
    if isinstance(outcome, NotPublished):
        click.echo(
            f"released locally, but the fleet was not told: {outcome.reason} — "
            "other machines will still see these paths as claimed until this "
            "machine publishes again",
            err=True,
        )


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--worker", "-w", required=True, help="Worker name")
@click.option("--ticket", "-t", default="", help="Ticket ID")
def claim(paths: tuple[str, ...], worker: str, ticket: str) -> None:
    """Claim file/directory paths for a worker."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.coordination.identity import claims_channel

    _require_project_root()
    ledger = ClaimsLedger(_claims_path())
    # P2-5a/P2-5b: with a fleet, a claim is checked against and published to
    # the other machines. Without one, both are None and nothing changes.
    layer, machine = claims_channel(_find_project_root())
    result = ledger.claim(list(paths), worker, ticket, layer=layer, machine=machine)
    if result.ok:
        scope = "across the fleet" if layer is not None else "on this machine only"
        # WHICH MODE, every time. The same success line for both is correct
        # for one machine and wrong the day a second joins, and the day it
        # becomes wrong is the day nobody re-reads this line. `claims_channel`
        # returns nothing unless `managers` AND `remote` are both set, so the
        # local-only mode is also what a half-configured fleet gets.
        click.echo(f"claimed {len(paths)} path(s) for {worker} — {scope}")
        if result.message:
            # "claimed locally, but not published" — the two stores cannot
            # be made atomic, so the gap is said out loud rather than left
            # for another machine to discover by claiming over it.
            click.echo(result.message, err=True)
        _warn_if_unregistered(worker)
    else:
        click.echo(f"claim failed: {result.message}", err=True)
        for overlap in result.overlaps:
            click.echo(f"  {overlap}", err=True)
        if result.overlaps:
            # Real contention, not misuse (e.g. an empty path list) —
            # SPEC §2.7.2/D-45: a count of discrete refusal events, the
            # coordination-cost signal a user watches to find their
            # project's own contention "knee."
            from rite_ai.coordination_cost import record_refused_claim

            record_refused_claim(_find_project_root())
        raise SystemExit(4)


@cli.command()
@click.option(
    "--worker", "-w", default=None, help="Worker name (not used with --force)"
)
@click.option(
    "--force", "force_flag", is_flag=True, help="Force-release another session's paths"
)
@click.option("--by", default=None, help="Attribution — required with --force")
@click.option("--reason", default=None, help="Why — required with --force (SPEC §5.2)")
@click.option(
    "--history",
    "show_history",
    is_flag=True,
    help="Print the force-release audit trail and exit; releases nothing.",
)
@click.argument("paths", nargs=-1)
def release(
    worker: str | None,
    force_flag: bool,
    by: str | None,
    reason: str | None,
    show_history: bool,
    paths: tuple[str, ...],
) -> None:
    """Release claimed paths (all if no paths given).

    `--history` answers "why did my claim disappear?" — every
    `--force` release ever made against this project, with who made it
    and the reason they gave. Append-only; nothing prunes it.

    Examples:
      rite release --worker alpha
      rite release --worker alpha src/a.ts
      rite release --force src/a.ts --by ops --reason "stale, worker crashed"
      rite release --history
    """
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.coordination.identity import claims_channel

    _require_project_root()
    ledger = ClaimsLedger(_claims_path())

    if show_history:
        records = ledger.force_release_audit()
        if not records:
            click.echo("no force-releases recorded")
            return
        for record in records:
            when = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(record.get("timestamp", 0))
            )
            click.echo(f"{when}  by {record.get('by', '(unknown)')}")
            click.echo(f"  reason: {record.get('reason', '(none)')}")
            for released_claim in record.get("released", []):
                ticket = released_claim.get("ticket") or ""
                suffix = f" [{ticket}]" if ticket else ""
                paths_str = ", ".join(released_claim.get("paths", []))
                worker_name = released_claim.get("worker", "(unknown)")
                click.echo(f"  took from {worker_name}{suffix}: {paths_str}")
        return

    if force_flag:
        if not paths:
            click.echo("--force requires explicit paths", err=True)
            raise SystemExit(2)
        if not by or not reason:
            click.echo("--force requires both --by and --reason (SPEC §5.2)", err=True)
            raise SystemExit(2)
        layer, machine = claims_channel(_find_project_root())
        released = ledger.force_release(
            list(paths), by=by, reason=reason, layer=layer, machine=machine
        )
        click.echo(f"force-released {released} claim(s), by {by}: {reason}")

        # The exact/overlap gap, said out loud. `--force` matches paths
        # exactly; `claim()` refuses on overlap. So clearing `users/src` can
        # leave `users/src/auth.ts` held, and the path just "cleared" still
        # cannot be claimed — which is precisely the trap somebody hits while
        # tidying up orphans, when they are least inclined to re-read the
        # semantics.
        for holder, path in ledger.last_skipped_overlaps:
            if holder in ledger.last_released_workers:
                # The same worker holding a parent and a child: legal, since
                # `claim()` only refuses overlaps BETWEEN workers. Advising a
                # release here would nudge somebody into force-releasing their
                # own still-legitimate claim.
                click.echo(
                    f"  note: {holder} also holds {path}, which nests with "
                    "what you just released — same worker, ordinarily nothing "
                    "to do"
                )
            else:
                click.echo(
                    f"  still held: {path} (by {holder}) overlaps what you "
                    "asked for. `--force` matches paths exactly, so it was "
                    "left alone — name it directly to release it too"
                )
        _warn_if_unpublished(ledger)
        return

    if not worker:
        click.echo("--worker is required (or use --force)", err=True)
        raise SystemExit(2)
    path_list = list(paths) if paths else None
    layer, machine = claims_channel(_find_project_root())
    released = ledger.release(worker, path_list, layer=layer, machine=machine)
    click.echo(f"released {released} claim(s) for {worker}")
    _warn_if_unpublished(ledger)


@cli.command()
@click.option(
    "--no-board",
    is_flag=True,
    default=False,
    help="Skip the live ticket-backend query (the only part that touches the network).",
)
def status(no_board: bool) -> None:
    """What's happening? Workers, claims, the handover snapshot, and the
    coordination-cost counters (SPEC §9.8) — as distinct from `rite
    doctor`'s "is this healthy?". Run outside any project with a Dispatch
    directory present, aggregates across all registered projects instead
    (SPEC §8.9) — a new rendering path in the same command, not a second
    command.

    Queries the ticket backend for board state (§9.8's counts by column).
    That is the one part of this command that leaves the machine; pass
    --no-board to skip it. The aggregate view across registered projects
    never queries, so it stays one round trip per machine rather than one
    per project."""
    click.echo(f"rite {__version__}")

    if _has_project_in_scope():
        from rite_ai.reporting.status import collect_status, format_status

        root = _find_project_root()
        click.echo(format_status(collect_status(root, board=not no_board)))
        return

    from rite_ai.dispatch import default_dispatch_dir, load_registry
    from rite_ai.reporting.status import collect_status

    registry = load_registry(default_dispatch_dir())
    if not registry.projects:
        click.echo(
            "no Dispatch directory found — run `rite status` inside a "
            "project, or `rite projects add` to register one"
        )
        return

    click.echo(f"\n{len(registry.projects)} registered project(s):")
    for alias, entry in registry.projects.items():
        click.echo(f"  {alias} ({entry.role}): {_aggregate_line(entry)}")


def _short_error(error: str) -> str:
    """`"<absolute path>: <message>"` reduced to `"<filename>: <message>"`,
    on one line, short enough to sit in a table row."""
    text = " ".join(str(error).split())
    head, sep, tail = text.partition(": ")
    if sep and ("/" in head or head.endswith((".yaml", ".yml", ".json", ".md"))):
        text = f"{Path(head).name}: {tail}"
    # Marked when cut, so a sentence that stops mid-excerpt does not read
    # as a whole one — a parser's error carries its own quoted snippet and
    # lands somewhere arbitrary in it.
    return text if len(text) <= 120 else text[:119] + "…"


def _aggregate_line(entry) -> str:
    """One registered project's line in the cross-project view (§8.9).

    This was `"no .rite/ found" if s.errors else "ok"` followed by the
    claim and stalled-worker counts, unconditionally. Three different
    states therefore printed the same sentence, and one of them printed a
    false one:

      moved  (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)
      broken (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)
      bare   (manager): no .rite/ found, 0 active claim(s), 0 stalled worker(s)

    — respectively a directory that no longer exists, a real project whose
    `.rite/brief.yaml` will not parse (its `.rite/` is right there, so the
    message is simply untrue and sends someone hunting a missing directory
    that is not missing), and a path that was never a project at all.

    The zeros are the worse half. `0 stalled worker(s)` from a project
    nothing opened reads exactly like `0 stalled worker(s)` from one that
    was read and is fine — and this view exists to be swept at a glance by
    a Manager deciding where NOT to look. `CountUnavailable` in
    `rite_ai.sandbox` is the same distinction already drawn elsewhere in
    this codebase: a count nobody could take is not a count of zero.

    The single-project view already diagnoses the broken case exactly
    (`invalid YAML: while parsing a flow sequence ... line 1, column 10`);
    this path had the same information and discarded it.
    """
    from rite_ai.reporting.status import collect_status

    project_root = Path(entry.path)
    if not project_root.is_dir():
        return (
            f"PATH GONE — {entry.path} no longer exists. Nothing was read; "
            "re-add it, or `rite projects remove` this alias"
        )
    if not (project_root / ".rite").is_dir():
        return (
            f"NOT A RITE PROJECT — no .rite/ in {entry.path}. Nothing was "
            "read; run `rite init` there, or remove this alias"
        )
    status = collect_status(project_root)
    if status.errors:
        # First line only, and whitespace collapsed: a YAML error runs to
        # several lines with its own indented excerpt, and this is one row
        # of a table. `rite status` inside the project prints it in full.
        # `CollectedStatus.errors` is a list of preformatted strings
        # (`"<absolute path>: <message>"`), not parse-error objects.
        # Shortened to the FILENAME, because the diagnosis is the part
        # worth the width: truncating the string as-is spent the whole
        # budget on the project path — which this line has already named
        # — and printed `UNREADABLE — /private/tmp/.../agg/.` with the
        # reason cut off entirely.
        detail = "; ".join(_short_error(e) for e in status.errors[:2])
        return f"UNREADABLE — {detail}. Counts not read"

    # The project loaded, but something INSIDE it may still have been
    # unreadable. Round 6 keyed this line on `status.errors` alone and
    # closed three doors of four: a corrupt `.rite/pool.json` is recorded
    # in `pool_unreadable`, which `errors` never sees, so a project whose
    # pool state could not be read rendered
    #
    #   bravo (manager): ok, 0 active claim(s), 0 stalled worker(s)
    #
    # — identical to the two healthy projects either side of it. Found by
    # running this project's own review checklist against the commit that
    # introduced the field.
    #
    # The counts stay: claims and workers really were read, and dropping
    # them would lose true information to report a different failure. What
    # goes is the word "ok".
    notes = [
        f"{label} unreadable ({_short_error(value)})"
        for label, value in _partial_reads(status)
    ]
    counts = (
        f"{len(status.claims)} active claim(s), "
        f"{len(status.stalled_workers)} stalled worker(s)"
    )
    if notes:
        return f"{counts}; " + "; ".join(notes)
    return f"ok, {counts}"


# Fields on `CollectedStatus` that mean "this part could not be read",
# paired with what to call them in a one-line summary.
#
# `boards_unreached` is deliberately NOT here: the aggregate view never
# queries a ticket backend (one round trip per machine, not one per
# project), so "not reached" is the expected state for every project and
# reporting it would be noise on every line.
#
# `tests/test_rehearsal_round7.py` fails if a new `*_unreadable` field
# appears on the dataclass and is not listed here — the guard is on the
# CLASS of defect, because this one recurred inside its own fix.
UNREADABLE_FIELDS: tuple[tuple[str, str], ...] = (("pool_unreadable", "pool state"),)


def _partial_reads(status) -> list[tuple[str, str]]:
    """`(label, why)` for each part of a loaded project that could not be
    read."""
    out = []
    for attribute, label in UNREADABLE_FIELDS:
        value = getattr(status, attribute, "")
        if value:
            out.append((label, str(value)))
    return out


# --- Credentials ---


@cli.group()
def credential() -> None:
    """Manage credentials."""


def _project_credentials():
    """This project's `CredentialsConfig`, or None outside a project.

    Best-effort in the same sense `_configured_credential_names` is:
    every `rite credential` subcommand has to keep working outside a
    project and against a config that will not parse, so a failure here
    narrows behaviour to the un-namespaced (machine-wide) layout rather
    than failing the command."""
    try:
        from rite_ai.config.parse import ParseError, parse_config

        root = _find_project_root()
        if not (root / ".rite").is_dir():
            return None
        config = parse_config(root / ".rite" / "config.yaml")
        if isinstance(config, ParseError):
            return None
        return config.credentials
    except Exception:
        return None


def _project_label() -> str:
    """The project's own name for display, falling back to the directory."""
    try:
        from rite_ai.config.parse import ParseError, parse_brief

        root = _find_project_root()
        brief = parse_brief(root / ".rite" / "brief.yaml")
        if not isinstance(brief, ParseError) and brief.name:
            return brief.name
        return root.name
    except Exception:
        return _find_project_root().name


def _keys_this_project_needs(config=None) -> list[str]:
    """The credential keys THIS project actually uses — the question
    `rite credential list` exists to answer.

    Driven by what the project is configured to do, not by the full
    catalogue: a project on the GitHub ticket backend is not missing a
    JIRA token, and telling it so is how a "missing" column becomes
    noise nobody reads.
    """
    from rite_ai.credentials.store import SANDBOX_TOKEN_PREFIX

    if config is None:
        from rite_ai.config.models import ProjectConfig
        from rite_ai.config.parse import ParseError, parse_config

        parsed = parse_config(_find_project_root() / ".rite" / "config.yaml")
        config = parsed if not isinstance(parsed, ParseError) else ProjectConfig()

    keys: list[str] = []
    backend = getattr(config.ticket_backend, "type", "none")
    if backend == "jira":
        # `ticket_backend.credential` RENAMES the token key, and it is the
        # name `create_backend` actually reads. Hardcoding `jira_token`
        # here told a project that had renamed its token that it was
        # missing a credential it holds, while saying nothing about the
        # one it really uses — wrong in both directions at once, on the
        # command whose whole job is answering "what does this project
        # need?".
        keys.append("jira_email")
        keys.append(getattr(config.ticket_backend, "credential", "") or "jira_token")
    elif backend == "github":
        keys.append("github_token")

    # A sandboxed worker is handed its token by `--env`, but the token
    # still has to be PROVISIONED on the host, so it is a thing this
    # project needs.
    if getattr(config.sandbox, "enabled", False):
        # The login a sandboxed session needs: it cannot read the keychain.
        keys.append("claude_token")
        root = _find_project_root()
        workers_dir = root / "workers"
        if workers_dir.is_dir():
            from rite_ai.credentials.store import keychain_is_readable, resolve

            creds = getattr(config, "credentials", None)
            for worker_dir in sorted(workers_dir.iterdir()):
                if not (worker_dir / "worker.yml").exists():
                    continue
                key = f"{SANDBOX_TOKEN_PREFIX}{worker_dir.name}"
                # ONLY IF IT EXISTS. A per-Worker token is `rite add worker
                # --scoped-token`'s opt-in, and §5.3.4.1 is explicit that it
                # is "an option a person chooses, not the model" — §5.3.4
                # retired per-Worker scoping and gives every Worker the
                # project's credentials. So a project that has not opted in
                # is not MISSING one, and listing it under "missing — set
                # each with:" told a new user to provision the thing the
                # spec had just retired, one line per Worker.
                #
                # Listing it when it does exist is the other half: somebody
                # who did opt in still needs its status and its rotation.
                # This function's own rule, applied to itself — "driven by
                # what the project is configured to do".
                # `or not keychain_is_readable()`: `.found` is False both
                # for "never provisioned" and for "this process cannot
                # look", and dropping the row in the second case deletes
                # the very line the sandboxed-process note below exists to
                # qualify. `keychain_is_readable` states the rule for
                # itself — "a failure to CHECK is not a negative result".
                if resolve(key, creds).found or not keychain_is_readable():
                    keys.append(key)
    return keys


def _configured_credential_names() -> tuple[str, ...]:
    """Credential keys knowable only from the project's own config.

    Best-effort: `rite credential set` is a machine-level command that
    must keep working outside any project, so a missing or unparseable
    config narrows validation rather than failing it."""
    try:
        from rite_ai.config.parse import ParseError, parse_config

        config = parse_config(_find_project_root() / ".rite" / "config.yaml")
        if isinstance(config, ParseError):
            return ()
        name = getattr(config.ticket_backend, "credential", "")
        return (name,) if name else ()
    except Exception:
        return ()


def _echo_known_credentials(extra: tuple[str, ...]) -> None:
    from rite_ai.credentials.services import describe_services
    from rite_ai.credentials.store import KNOWN_CREDENTIALS, SANDBOX_TOKEN_PREFIX

    # SERVICES first, and keys second, because the service is what the
    # user actually has and the key is rite's name for part of it. Leading
    # with the key list is what sent someone looking for the name of their
    # own email address in it.
    click.echo("\nServices rite can set up for you:", err=True)
    for line in describe_services():
        click.echo(line, err=True)
    click.echo(
        "\n  e.g. `rite credential set jira` — asks for each field in turn", err=True
    )

    click.echo("\nOr one individual key:", err=True)
    for key, description in KNOWN_CREDENTIALS.items():
        click.echo(f"  {key:<22} {description}", err=True)
    for key in extra:
        click.echo(f"  {key:<22} this project's configured JIRA token key", err=True)
    click.echo(
        f"  {SANDBOX_TOKEN_PREFIX + '<worker>':<22} per-worker sandbox token", err=True
    )


def _how_to_set(key: str) -> str:
    """The argument to suggest for a missing key: its service when one
    owns it, otherwise the key itself (a per-worker sandbox token is not
    a service and never will be)."""
    from rite_ai.credentials.services import SERVICES, service_key

    for svc in SERVICES.values():
        if any(service_key(svc.name, f.name) == key for f in svc.fields):
            return svc.name
    return key


def _echo_credential_status(extra: tuple[str, ...], just_set: str = "") -> None:
    """Answer "am I done?" — the question a person actually has after
    setting one credential of several. Reports the TIER each key
    resolved at, because "set" alone hid the case this redesign is
    about: a project silently using the machine-wide entry while its
    owner believed it had its own.

    Never empty. Narrowing to "what this project needs" is right for the
    listing, but here it could print NOTHING at all — a project on no
    ticket backend, having just stored a credential, was answered with
    silence. The catalogue is the floor."""
    from rite_ai.credentials.store import KNOWN_CREDENTIALS, resolve

    creds = _project_credentials()
    keys = list(
        dict.fromkeys(
            [
                *([just_set] if just_set else []),
                *_keys_this_project_needs(),
                *KNOWN_CREDENTIALS,
                *extra,
            ]
        )
    )
    click.echo("\ncredential status:")
    for key in keys:
        r = resolve(key, creds)
        if r.found:
            click.echo(f"  {key:<24} {r.describe()}")
        else:
            # Suggest the SERVICE where one owns this key. Telling someone
            # to `set github_token` is telling them a key name when
            # `set github` is the thing they can act on without knowing
            # rite's vocabulary — which is the whole point of §10.5.
            click.echo(f"  {key:<24} not set — rite credential set {_how_to_set(key)}")


def _ensure_namespace(root, config) -> str:
    """This project's credential namespace, generating and PERSISTING one
    on first use.

    Written back to `config.yaml` immediately, BEFORE any secret is
    stored. A namespace held only in memory would name a keychain account
    that the next command — regenerating a different random namespace —
    never looks up again: a credential stored under a name nobody can
    rederive. Persisting first is what makes the name survive, and
    `config.yaml` is committed, so it survives a fresh clone too."""
    from rite_ai.cli.init.scaffold import write_config
    from rite_ai.credentials.store import is_valid_namespace, make_namespace

    namespace = config.credentials.namespace
    if namespace and is_valid_namespace(namespace):
        return namespace

    namespace = make_namespace(_project_label())
    config.credentials.namespace = namespace
    rite_dir = root / ".rite"
    rite_dir.mkdir(parents=True, exist_ok=True)
    write_config(rite_dir, config)
    click.echo(f"recorded credential namespace '{namespace}' in .rite/config.yaml")
    click.echo(
        "  commit that file — it records the credential NAMESPACE (never a "
        "value), so a fresh clone knows what to set"
    )
    return namespace


def _store_one(name: str, value: str, global_: bool, root, config) -> str:
    """Store one key, scoped unless `--global`. Returns the account it
    landed under. Shared by the service flow and the single-key flow so
    the two cannot drift on where a secret goes."""
    from rite_ai.credentials.store import namespaced, store

    account = name
    if not global_ and config is not None:
        account = namespaced(_ensure_namespace(root, config), name)
    result = store(account, value)
    if result != "keychain":
        click.echo(f"failed to store '{name}' — keyring not available", err=True)
        click.echo(f"set RITE_{name.upper()} as an environment variable instead")
        raise SystemExit(1)
    return account


def _apply_config_field(config, dotted: str, value: str) -> None:
    """Set one dotted path on a `ProjectConfig`. Two levels is all any
    service needs (`ticket_backend.projects.workers` is the deepest), and
    a general setter would be more machinery than the field list can
    justify."""
    parts = dotted.split(".")
    target = config
    for part in parts[:-1]:
        nxt = getattr(target, part, None)
        if isinstance(nxt, dict):
            target = nxt
        elif nxt is None:
            raise KeyError(dotted)
        else:
            target = nxt
    last = parts[-1]
    if isinstance(target, dict):
        target[last] = value
    else:
        setattr(target, last, value)


def _set_service(service_name: str, global_: bool, root, config) -> None:
    """Ask for every field a service has, in order, and store each.

    The prompts are the service's own words. Nothing here asks the user
    to know a key name — that is the whole point of taking a service
    rather than a key."""
    from rite_ai.credentials.services import SERVICES, service_key

    svc = SERVICES[service_name]
    click.echo(f"{svc.label}")
    if svc.note:
        click.echo(f"  note: {svc.note}")

    stored: list[tuple[str, str]] = []
    configured: list[tuple[str, str]] = []
    for field in svc.fields:
        value = click.prompt(
            f"  {field.prompt}",
            hide_input=field.secret,
            confirmation_prompt=field.secret,
            err=True,
        )
        if field.config_path:
            # CONFIGURATION, not a credential. It is the same for everyone
            # on the team, it is not a secret, and putting it in the
            # committed config.yaml is what lets the next person clone the
            # project and need only their own token.
            if config is None:
                click.echo(
                    f"  (skipped {field.name}: not inside a rite project, so "
                    f"there is no config.yaml to record it in)",
                    err=True,
                )
                continue
            _apply_config_field(config, field.config_path, value)
            configured.append((field.config_path, value))
            continue
        key = service_key(svc.name, field.name)
        stored.append((key, _store_one(key, value, global_, root, config)))

    if configured:
        from rite_ai.cli.init.scaffold import write_config

        write_config(root / ".rite", config)

    where = "machine-wide (global fallback)" if global_ else "for this project"
    if stored:
        click.echo(f"\nstored {len(stored)} secret(s) for '{svc.name}' {where}:")
        for key, account in stored:
            click.echo(f"  {key:<20} -> keychain '{account}'")
    if configured:
        click.echo(
            "\nrecorded in .rite/config.yaml (commit it — not secret, "
            "and it saves the next person finding it out):"
        )
        for path, value in configured:
            click.echo(f"  {path:<32} {value}")


@credential.command("set")
@click.argument("name")
@click.option(
    "--value",
    default=None,
    help="Credential value. Omit it — you will be prompted, and it stays out of argv.",
)
@click.option(
    "--allow-unknown",
    is_flag=True,
    default=False,
    help="Store under a key rite does not recognise (deliberate, not a typo).",
)
@click.option(
    "--global",
    "global_",
    is_flag=True,
    default=False,
    help=(
        "Store machine-wide under the bare key, as the fallback every project "
        "uses when it has no scoped entry of its own."
    ),
)
def credential_set(
    name: str, value: str | None, allow_unknown: bool, global_: bool
) -> None:
    """Store credentials for a SERVICE — `rite credential set jira` — or
    for one individual key. Secrets are prompted for, never passed as an
    argument.

    NAME is normally a service (`jira`, `github`). rite validates it,
    then asks for the fields that service actually has, in order and in
    its own words: JIRA asks for an account email and then an API token;
    GitHub asks for a token and no username, because a fine-grained PAT
    does not have one.

    NAME may also still be a single KEY (`jira_token`) when you want to
    replace exactly one field. Anything that is neither is refused rather
    than stored, because storing it silently succeeds while leaving the
    credential you meant still unset.

    Run inside a project, this stores THIS PROJECT's credential: the
    secret goes to the keychain under a scoped name, and the name is
    recorded in `.rite/config.yaml` (§10.2). That is the default because
    the point of scoping is that a worker only ever gets a token you
    gave for the project it is working on.

    `--global` stores it machine-wide under the bare key instead, as the
    fallback used by any project with no scoped entry of its own. That
    is also what happens automatically outside a project.

    \b
    Examples:
      rite credential set jira                    # email, then token
      rite credential set github --global         # machine-wide fallback
      rite credential set claude                  # token from `claude setup-token`
      rite credential set jira_token              # just the one field
      rite credential set sandbox_token_alpha
    """
    from rite_ai.credentials.services import canonical_service, suggest_service
    from rite_ai.credentials.store import (
        is_known_name,
        looks_like_email,
        store,
        suggest_name,
    )

    extra = _configured_credential_names()

    # A SERVICE is the ordinary case, so it is checked first. Outside a
    # project there is no namespace to scope to, which is exactly what
    # `--global` means, so it is what happens rather than an error.
    root_for_scope = _find_project_root()
    config_for_scope = None
    if not global_ and (root_for_scope / ".rite").is_dir():
        from rite_ai.config.models import ProjectConfig
        from rite_ai.config.parse import ParseError, parse_config

        parsed = parse_config(root_for_scope / ".rite" / "config.yaml")
        if isinstance(parsed, ParseError):
            click.echo(f"config error: {parsed.message}", err=True)
            click.echo(
                "  fix .rite/config.yaml, or use --global to store "
                "machine-wide without it",
                err=True,
            )
            raise SystemExit(1)
        config_for_scope = parsed if isinstance(parsed, ProjectConfig) else None

    service = canonical_service(name)
    if service is not None:
        # Normalised: `JIRA`, `Jira` and `jira` are the same service, and
        # the stored keys are always the lowercase form.
        name = service
        if value is not None:
            click.echo(
                f"'{name}' is a service with several fields — --value can "
                f"only set one.\n  Run `rite credential set {name}` and answer "
                f"the prompts, or name a single key.",
                err=True,
            )
            raise SystemExit(2)
        _set_service(service, global_, root_for_scope, config_for_scope)
        _echo_credential_status(extra)
        return

    if not is_known_name(name, extra) and not allow_unknown:
        if looks_like_email(name):
            # The exact shape of the reported defect: an email address is
            # what someone reaching for `jira_email` types, and it is a
            # VALUE — no key rite reads could look like one.
            click.echo(
                f"'{name}' looks like a credential value, not a credential key.",
                err=True,
            )
            click.echo(
                "\n`rite credential set` takes the NAME of the credential to "
                "store; the value is prompted for separately.",
                err=True,
            )
            click.echo(
                "\nTo set up JIRA, which will ask for that address "
                "and then your API token:",
                err=True,
            )
            click.echo("  rite credential set jira", err=True)
        else:
            svc_suggestion = suggest_service(name)
            if svc_suggestion:
                # A near-miss on a SERVICE is the likelier typo now that a
                # service is what people type, and it is the more useful
                # correction: it names one command that finishes the job.
                click.echo(f"'{name}' is not a service rite knows.", err=True)
                click.echo(
                    f"\nDid you mean:\n  rite credential set {svc_suggestion}",
                    err=True,
                )
            else:
                click.echo(f"unknown credential key '{name}'.", err=True)
                suggestion = suggest_name(name, extra)
                if suggestion:
                    click.echo(
                        f"\nDid you mean:\n  rite credential set {suggestion}",
                        err=True,
                    )
        _echo_known_credentials(extra)
        click.echo(
            f"\nTo store under '{name}' anyway:\n"
            f"  rite credential set {name} --allow-unknown",
            err=True,
        )
        raise SystemExit(2)

    # Prompt only AFTER the key is known to be good. Validating second
    # made the reporter type their secret twice and confirm it before
    # being told the key was wrong — work thrown away, and it reads as
    # though the store were the thing that failed.
    if value is None:
        value = click.prompt(
            "Value", hide_input=True, confirmation_prompt=True, err=True
        )

    # Resolve the STORED NAME before storing. Outside a project there is
    # no scope to apply and `--global` is the only meaningful behaviour,
    # so it is what happens rather than an error.
    account = name
    scoped = False
    root = _find_project_root()
    if not global_ and (root / ".rite").is_dir():
        from rite_ai.config.models import ProjectConfig
        from rite_ai.config.parse import ParseError, parse_config
        from rite_ai.credentials.store import namespaced

        parsed = parse_config(root / ".rite" / "config.yaml")
        if isinstance(parsed, ParseError):
            click.echo(f"config error: {parsed.message}", err=True)
            click.echo(
                "  fix .rite/config.yaml, or use --global to store "
                "machine-wide without it",
                err=True,
            )
            raise SystemExit(1)
        config = parsed if isinstance(parsed, ProjectConfig) else ProjectConfig()
        account = namespaced(_ensure_namespace(root, config), name)
        scoped = True

    result = store(account, value)
    if result != "keychain":
        click.echo(f"failed to store '{name}' — keyring not available", err=True)
        click.echo(f"set RITE_{name.upper()} as an environment variable instead")
        raise SystemExit(1)

    # Name the argument as the KEY explicitly. "stored 'X' in keychain"
    # read equally well as "your secret X is stored", which is how a
    # mistyped key went unnoticed. The stored name is printed alongside
    # it because with scoping the two are no longer the same string, and
    # the stored name is what `rite credential remove` takes.
    where = "for this project" if scoped else "machine-wide (global fallback)"
    click.echo(
        f"stored credential under key '{name}' {where}, in keychain as '{account}'"
    )
    _echo_credential_status(extra, just_set=name)


@credential.command("check")
@click.argument("name")
def credential_check(name: str) -> None:
    """Check if a credential is available. Exits non-zero when it is not,
    so `rite credential check X && ...` means what it looks like.

    Examples:
      rite credential check jira_token
    """
    from rite_ai.credentials.store import resolve

    r = resolve(name, _project_credentials())
    click.echo(f"{name}: {r.describe()}")
    if not r.found:
        # A check that reports failure through exit code 0 is not a check —
        # every script guarding on it proceeds straight into the failure it
        # was written to prevent.
        from rite_ai.credentials.store import keychain_is_readable

        if not keychain_is_readable():
            # "not found" here means "could not look". Advising `credential
            # set` would be wrong: this process cannot read the keychain
            # back either way. Same distinction as `CountUnavailable`.
            click.echo(
                "  this process cannot read the keychain at all (sandboxed?) — "
                f"that is\n  'cannot check', not 'missing'. Set RITE_"
                f"{name.upper()} in the environment;\n  a sandboxed Worker "
                "receives its token through --env (D-31).",
                err=True,
            )
        else:
            click.echo(
                f"  run `rite credential set {name}`, "
                f"or set RITE_{name.upper()} in the environment",
                err=True,
            )
        raise SystemExit(1)


@credential.command("list")
def credential_list() -> None:
    """What THIS PROJECT needs, what it resolves to, and what is missing.

    This is the answer to "how do I give rite a GitHub token?" — a
    question that had no discoverable answer, because the only place that
    ever asked for one was a prompt buried inside `rite add worker`.

    The ACCOUNT column is the keychain entry each key lives under,
    composed from the namespace recorded in `.rite/config.yaml`. That
    file is committed on purpose and holds a name, never a value: a fresh
    clone runs this command and sees exactly what to set, without a
    secret having been shared — the `.env.example` pattern.

    The machine-wide listing follows it — every credential rite has
    stored on this machine, project or not — because an entry that
    belongs to no project is exactly what a value typed where a key
    belongs looks like after the fact.

    Read-only, and it prompts for nothing.

    \b
    Examples:
      rite credential list
    """
    from rite_ai.credentials.store import (
        ENV,
        GLOBAL,
        SCOPING_IS_NOT_A_SANDBOX_SHORT,
        is_known_name,
        keychain_is_readable,
        list_for_rotation,
        resolve,
    )

    root = _find_project_root()
    in_project = (root / ".rite").is_dir()
    creds = _project_credentials()
    keys = _keys_this_project_needs() if in_project else []

    missing: list[str] = []
    fellback: list[str] = []
    if in_project:
        namespace = getattr(creds, "namespace", "") or ""
        click.echo(f"project:   {_project_label()}")
        click.echo(
            f"namespace: {namespace or '(none yet — recorded on first scoped set)'}"
        )
        click.echo("recorded:  .rite/config.yaml  (commit it: a name, never a value)")

        if not keys:
            click.echo(
                "\nthis project needs no credentials — ticket_backend.type is "
                "'none' and sandbox.enabled is false"
            )
        else:
            click.echo("")
            click.echo(f"  {'KEY':<22} {'KEYCHAIN ACCOUNT':<38} STATUS")
            for key in keys:
                r = resolve(key, creds)
                shown = r.account if r.tier == ENV else r.project_account
                click.echo(f"  {key:<22} {shown:<38} {r.describe()}")
                if not r.found:
                    missing.append(key)
                elif r.tier == GLOBAL:
                    fellback.append(key)

        if missing:
            click.echo("\nmissing — set each with:")
            for key in missing:
                click.echo(f"  rite credential set {_how_to_set(key)}")
            click.echo(
                "\n  (add --global to store one machine-wide instead, as the "
                "fallback\n   any project without its own entry will use)"
            )
        if fellback:
            # Never silent. A machine-global entry used by a project that
            # believes it has its own is the flat namespace surviving
            # under a new name.
            click.echo("\nusing machine-global credentials for: " + ", ".join(fellback))
            click.echo(
                "  those entries are shared with every other project on this "
                "machine.\n  move one into this project with:"
            )
            for key in fellback:
                click.echo(f"  rite credential migrate {key}")
        if keys and not missing and not fellback:
            click.echo("\nnothing missing.")
    else:
        click.echo("not inside a rite project — machine-wide credentials only")

    if True:
        entries = list_for_rotation()
        click.echo("\nstored on this machine:")
        if not entries:
            click.echo("  no credentials stored by rite on this machine")
        else:
            extra = _configured_credential_names()
            unknown = []
            namespace = getattr(creds, "namespace", "") or ""
            for entry in entries:
                # THREE states, not two. `or "/" in entry.name` collapsed
                # the last two and took the marker with it: every account
                # `credential set` writes inside a project is namespaced,
                # so every one of them contained a "/" and was waved
                # through as recognised. The marker is the thing that
                # tells someone a `you@example.com` entry is a
                # VALUE typed where a key belongs — it exists because
                # that exact mistake was made, and silencing it for
                # everything the command now writes retires the feature
                # while leaving it looking present.
                #
                #   1. this project's namespace, or un-namespaced -> we
                #      know this project's keys, so validate the bare key
                #      and mark it when it is not one.
                #   2. ANOTHER project's namespace -> we cannot validate
                #      it (we do not have that project's config) and must
                #      not call it wrong. Say whose it is instead.
                bare = entry.name
                foreign = False
                if namespace and entry.name.startswith(namespace + "/"):
                    bare = entry.name[len(namespace) + 1 :]
                elif "/" in entry.name:
                    foreign = True

                if foreign:
                    other = entry.name.split("/", 1)[0]
                    recognised = True
                    mark = f"   <- another project ({other})"
                else:
                    recognised = is_known_name(bare, extra)
                    mark = "" if recognised else "   <- not a credential key rite reads"
                if entry.last_set is not None:
                    when = time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(entry.last_set)
                    )
                    click.echo(
                        f"  {entry.name}  (last set {when}, {entry.source}){mark}"
                    )
                else:
                    click.echo(f"  {entry.name}  ({entry.source}){mark}")
                if not recognised:
                    unknown.append(entry.name)

            if unknown:
                click.echo(
                    "\nUnrecognised keys are usually a value typed where a key "
                    "belongs.\nRemove one with:"
                )
                for name in unknown:
                    click.echo(f"  rite credential remove {name}")

    if not keychain_is_readable():
        # The §5 defect: in here, "not set" means "cannot look", and
        # telling someone to run `credential set` is wrong advice —
        # setting it changes nothing, because this process cannot read it
        # back either way.
        click.echo("")
        click.echo(
            "NOTE: this process cannot read the keychain at all (sandboxed?), "
            "so\n  every 'not set' above means 'cannot check', not 'missing'. "
            "A sandboxed\n  Worker receives its token through --env; setting a "
            "credential in here\n  would not change what it can read."
        )

    # Printed every time, not behind a flag. An operator reading a
    # per-project listing will read isolation into it; saying plainly
    # that this is a naming convention is cheaper than the belief that it
    # is a boundary.
    click.echo("")
    click.echo(SCOPING_IS_NOT_A_SANDBOX_SHORT.rstrip())


@credential.command("migrate")
@click.argument("name")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation.")
def credential_migrate(name: str, yes: bool) -> None:
    """Copy a machine-global credential into THIS project's namespace.

    The migration path for a setup that predates §10.2: `jira_token` set
    globally keeps working through the fallback tier, and this moves it
    under `<namespace>/jira_token` so the project stops depending on a
    shared entry.

    COPIES — the global entry is left alone, because other projects are
    still resolving through it. Remove it yourself, once you have
    checked nothing else needs it:
    `rite credential remove <name>`.

    \b
    Examples:
      rite credential migrate jira_token
    """
    from rite_ai.credentials.store import (
        GLOBAL,
        get_account,
        is_valid_namespace,
        namespaced,
        resolve,
        store,
    )

    root = _find_project_root()
    if not (root / ".rite").is_dir():
        click.echo("not inside a rite project — nothing to migrate into", err=True)
        raise SystemExit(1)

    root, config = _load_config_for_write()
    r = resolve(name, config.credentials)
    if r.tier != GLOBAL:
        click.echo(f"{name}: {r.describe()}", err=True)
        click.echo(
            "  migrate moves a MACHINE-GLOBAL entry into this project; "
            "this key is not resolving through that tier",
            err=True,
        )
        raise SystemExit(1)

    # Read the global account explicitly. `get_scoped` would work today,
    # but only because the project entry is absent — which is the thing
    # being changed.
    value = get_account(r.global_account)
    if not value:
        click.echo(f"could not read '{r.global_account}' from the keychain", err=True)
        raise SystemExit(1)

    # ⚠ Nothing is WRITTEN before the confirm that authorises it. This
    # read `_ensure_namespace(...)` inline in the prompt's own argument
    # list, so a project with no namespace yet had one generated and
    # PERSISTED to config.yaml while the question was still on screen —
    # and answering "no" then printed "left unchanged" over a config file
    # that had just changed. Declining has to leave the disk alone; that
    # is the whole of what declining means.
    existing = config.credentials.namespace
    if existing and is_valid_namespace(existing):
        target = namespaced(existing, name)
        also = ""
    else:
        # No namespace yet, so the target cannot be named without
        # inventing one. Say that the config write is part of what is
        # being agreed to rather than performing it first and reporting
        # it afterwards.
        target = f"<new namespace>/{name}"
        also = " (this also records a new credential namespace in .rite/config.yaml)"

    if not yes and not click.confirm(
        f"copy '{r.global_account}' -> '{target}'{also}?", default=False
    ):
        click.echo("left unchanged")
        return

    # Authorised — now the namespace may be created and persisted.
    target = namespaced(_ensure_namespace(root, config), name)

    if store(target, value) != "keychain":
        click.echo("failed to store — keyring not available", err=True)
        raise SystemExit(1)

    click.echo(f"copied '{r.global_account}' -> '{target}'")
    click.echo(
        f"  the global entry is untouched; other projects still resolve "
        f"through it.\n  once nothing else needs it: rite credential remove "
        f"{r.global_account}"
    )


@credential.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation.")
def credential_remove(name: str, yes: bool) -> None:
    """Delete a stored credential from the keychain and rite's registry.

    The counterpart to `set`. Without it, a key stored by mistake — an
    email address typed where `jira_email` belonged, say — stayed in the
    keychain permanently, since `set` could create entries that no rite
    command could remove.

    Examples:
      rite credential remove sandbox_token_alpha
      rite credential remove someone@example.com --yes
    """
    from rite_ai.credentials.store import list_for_rotation, project_account, remove

    stored = {e.name for e in list_for_rotation()}

    # Accept the KEY as well as the full account: `rite credential set
    # jira_token` stores `<namespace>/jira_token`, so a remove that only
    # matched the literal argument could not delete what `set` had just
    # created — the "set could create what nothing could remove" defect
    # this command exists to fix, reintroduced by namespacing.
    #
    # ⚠ But when BOTH exist, this must REFUSE rather than choose. Measured,
    # and the reason this branch is written the way it is: with a project
    # entry and a machine-global entry both present, taking the literal
    # argument deleted the MACHINE-GLOBAL one — the entry every other
    # project on the machine resolves through — while reporting the key
    # the user typed, which reads as though the project's own copy went.
    # A keychain delete is unrecoverable, so the ambiguous case is the one
    # case that must not be guessed.
    scoped = project_account(name, _project_credentials())
    candidates = [c for c in (scoped, name) if c in stored]
    candidates = list(dict.fromkeys(candidates))

    if len(candidates) > 1:
        click.echo(f"'{name}' is ambiguous — both of these are stored:", err=True)
        for c in candidates:
            which = "this project" if c == scoped else "machine-global"
            click.echo(f"  {c}   ({which})", err=True)
        click.echo(
            "\nName the one you mean in full:\n"
            f"  rite credential remove {candidates[0]}\n"
            f"  rite credential remove {candidates[1]}",
            err=True,
        )
        raise SystemExit(2)

    if candidates and candidates[0] != name:
        click.echo(f"removing this project's '{name}' ({candidates[0]})")
        name = candidates[0]

    if name not in stored:
        click.echo(f"no credential stored under key '{name}'", err=True)
        # An env var is not rite's to delete, and saying "not stored"
        # without that distinction sends someone hunting in the keychain
        # for something that lives in their shell profile.
        env_key = f"RITE_{name.upper()}"
        if os.environ.get(env_key):
            click.echo(
                f"  ({env_key} is set in this environment — "
                f"that is not stored by rite and has to be unset there)",
                err=True,
            )
        raise SystemExit(1)

    if not yes and not click.confirm(f"remove keychain entry '{name}'?", default=False):
        click.echo("left unchanged")
        return

    result = remove(name)
    if result == "failed":
        click.echo(f"failed to remove '{name}'", err=True)
        raise SystemExit(1)
    click.echo(f"removed credential '{name}'")


@credential.command("rotate")
def credential_rotate() -> None:
    """Guided rotation of every credential rite has ever stored (SPEC
    §10): shows each one's age and current source, and prompts for a
    replacement — or skip. Only covers credentials `rite credential set`
    (or a provisioning flow that stored one the same way) has actually
    written; a credential satisfied only by an env var was never
    recorded and has nothing here to rotate.

    Examples:
      rite credential rotate
    """
    from rite_ai.credentials.store import list_for_rotation, store

    entries = list_for_rotation()
    if not entries:
        click.echo("no credentials recorded — nothing to rotate")
        return

    for entry in entries:
        if entry.last_set is not None:
            age = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.last_set))
            click.echo(f"{entry.name}  (last set {age}, currently: {entry.source})")
        else:
            click.echo(f"{entry.name}  (currently: {entry.source})")
        if not click.confirm("  replace it now?", default=False):
            continue
        value = click.prompt("  new value", hide_input=True, confirmation_prompt=True)
        result = store(entry.name, value)
        if result != "keychain":
            click.echo("  failed to store — keyring not available", err=True)
            continue
        click.echo(f"  {entry.name} rotated")


# --- Add / Remove ---


@cli.group()
def add() -> None:
    """Add a module or worker."""


@add.command("module")
@click.argument("name")
@click.argument("url", default="")
@click.option(
    "--branch",
    "-b",
    default=None,
    help="Branch to track (default: the remote's own default branch).",
)
@click.option("--description", "-d", default="", help="One-line description")
def add_module_cmd(name: str, url: str, branch: str, description: str) -> None:
    """Register (and optionally clone) a module.

    Examples:
      rite add module backend
      rite add module frontend git@github.com:org/frontend.git
    """
    from rite_ai.workspace import add_module

    root = _find_project_root()
    result = add_module(
        root, name, url=url or None, branch=branch, description=description
    )
    if result.ok:
        click.echo(result.message)
    else:
        click.echo(result.message, err=True)
        raise SystemExit(1)


@add.command("worker")
@click.argument("name")
@click.option("--manager", "-m", default="", help="Manager name")
@click.option(
    "--modules", default="", help="Comma-separated module subset (default: all)"
)
@click.option(
    "--instructions",
    default="",
    help="Standing direction for this one Worker — stored as worker.yml's "
    "`claude_instructions` (SPEC §8.4) and rendered into its CLAUDE.md.",
)
@click.option(
    "--scoped-token",
    is_flag=True,
    default=False,
    help="Provision a GitHub token for this Worker alone, scoped to the "
    "project's repos. Off by default: Workers share the project's "
    "credentials.",
)
def add_worker_cmd(
    name: str, manager: str, modules: str, instructions: str, scoped_token: bool
) -> None:
    """Create a new worker workspace.

    With `--scoped-token`, also walks through provisioning a GitHub token
    for this one Worker, scoped to the project's repos (§5.3.3, §5.3.4) —
    never displayed in this project's chat, only typed directly into this
    terminal prompt. Without it, Workers share the credentials the project
    already holds.

    Examples:
      rite add worker alpha
      rite add worker beta --modules backend,shared
      rite add worker gamma --instructions "Ship nothing without a migration plan."
      rite add worker delta --scoped-token
    """
    from rite_ai.workspace import add_worker

    root, config = _load_config_for_write()
    module_subset = (
        [m.strip() for m in modules.split(",") if m.strip()] if modules else None
    )
    result = add_worker(
        root,
        name,
        manager=manager,
        module_subset=module_subset,
        instructions=instructions,
    )
    if not result.ok:
        click.echo(result.message, err=True)
        raise SystemExit(1)

    click.echo(result.message)
    if result.cloned_modules:
        click.echo(f"  cloned: {', '.join(result.cloned_modules)}")
    for module_name, why in result.failed_modules:
        click.echo(f"  NOT cloned: {module_name} — {why}", err=True)

    # Asked for explicitly, never implied by `sandbox.enabled`. That key
    # governs whether Workers run in a sandbox; it is not a statement about
    # how their credentials are scoped, and letting it provision a
    # per-Worker token meant turning sandboxing on silently changed the
    # credential model too.
    if scoped_token and result.worker is not None:
        _provision_worker_token(root, result.worker, config)

    if result.failed_modules:
        # The worker itself exists and is registered, so this is not a
        # failure to create it — and the sandbox token above is still
        # worth provisioning. But the worker is missing a checkout it was
        # given, and a zero exit here is exactly what let `rite add
        # worker` report a clone that never happened. Last, so nothing
        # the command still had to do is skipped by the exit.
        click.echo(
            f"worker '{name}' is registered but "
            f"{len(result.failed_modules)} module(s) have no checkout — "
            f"fix the source above, then `rite prepare --worker {name}`.",
            err=True,
        )
        raise SystemExit(1)


# GitHub's own label for each permission rite knows how to name, so the
# prompt reads like the page the user is about to fill in. An unrecognised
# value is printed verbatim rather than dropped or guessed at — the config
# key is the user's, and a permission rite has never heard of is still a
# permission they meant to ask for.
_TOKEN_PERMISSION_LABELS = {
    "contents": "Contents (read/write)",
    "pull_requests": "Pull requests (read/write)",
    "issues": "Issues (read/write)",
    "metadata": "Metadata (read)",
    "workflows": "Workflows (read/write)",
}


def _token_permission_line(permissions: list[str]) -> str:
    if not permissions:
        # §5.3.3 is a MINIMUM, so an empty list is not "no permissions
        # needed" — it is a config that forgot to say. Name the file rather
        # than printing "permissions: — nothing else", which reads like an
        # instruction to create a token that can do nothing.
        return (
            "  - permissions: none listed in .rite/config.yaml "
            "(sandbox.token_permissions) — §5.3.3 requires contents and "
            "pull requests at minimum"
        )
    labelled = ", ".join(_TOKEN_PERMISSION_LABELS.get(p, p) for p in permissions)
    return f"  - permissions: {labelled} — nothing else"


def _provision_worker_token(root, worker, config) -> None:
    """Guided sandbox-token provisioning for one Worker (§5.3.3, §5.3.4).
    Prints the exact repos and permissions the token should be scoped to,
    then — only on explicit confirmation — prompts for the value directly
    (never collected any other way) and stores it under the naming
    convention `rite_ai.sandbox.token_credential_name` and `rite credential
    rotate` both rely on.

    The permissions come from `sandbox.token_permissions`, the project's
    own config key. They used to be a hardcoded sentence one line below
    the docstring promising "the exact ... permissions" — so the key was
    parsed, serialised, round-trip-tested and documented in SPEC §8's
    config listing while the one command §5.3.4 names as its consumer
    ignored it, and a project that narrowed or widened the list was told
    the default regardless."""
    from rite_ai.config.parse import parse_modules
    from rite_ai.credentials.store import get_scoped, namespaced, store
    from rite_ai.sandbox import check_token_access, token_credential_name

    key = token_credential_name(worker.name)
    if get_scoped(key, config.credentials):
        click.echo(f"  sandbox token already provisioned ({key})")
        return
    # Scoped like every other credential: this Worker's token belongs to
    # THIS project, which is the whole reason the guided path exists.
    cred_name = namespaced(_ensure_namespace(root, config), key)

    # EVERY project module, not this Worker's subset (§5.3.4). Workers are
    # fungible: any of them may take any ticket, so a token scoped to the
    # subset one Worker happens to have cloned would make Workers differ in
    # capability and force assignment to reason about which one CAN do a
    # job. The bound that remains is the project's repos — narrower than
    # the person's account, which is the limit that matters for an agent.
    all_modules = parse_modules(root / ".rite" / "modules.yaml")
    project_modules = all_modules if isinstance(all_modules, list) else []
    repos = [m.url for m in project_modules if m.url]

    click.echo("")
    click.echo(
        f"worker '{worker.name}' will get a GitHub token scoped to THIS "
        "PROJECT's repos (§5.3.3):"
    )
    click.echo("  - fine-grained personal access token")
    click.echo(
        "  - repository access: "
        + (", ".join(repos) if repos else "(no module URLs registered — set none)")
    )
    click.echo(
        "    (every project module, not just this worker's — any worker may "
        "take any\n     ticket, so they all carry the same scope. §5.3.4)"
    )
    click.echo(_token_permission_line(config.sandbox.token_permissions))
    click.echo("  create one at https://github.com/settings/personal-access-tokens/new")
    if not click.confirm("  store the token now?", default=False):
        click.echo(f"  skipped — run `rite credential set {key}` later")
        return

    value = click.prompt("  token", hide_input=True, confirmation_prompt=True)
    result = store(cred_name, value)
    if result != "keychain":
        click.echo("  failed to store — keyring not available", err=True)
        return
    click.echo(f"  stored as '{cred_name}'")

    problems = check_token_access(value, project_modules)
    for problem in problems:
        click.echo(f"  warning: {problem}")


@cli.group()
def remove() -> None:
    """Remove a module or worker."""


@remove.command("module")
@click.argument("name")
def remove_module_cmd(name: str) -> None:
    """Deregister a module (does not delete the directory).

    Examples:
      rite remove module backend
    """
    from rite_ai.workspace import remove_module

    root = _find_project_root()
    result = remove_module(root, name)
    if result.ok:
        click.echo(result.message)
    else:
        click.echo(result.message, err=True)
        raise SystemExit(1)


@remove.command("worker")
@click.argument("name")
@click.option(
    "--force",
    is_flag=True,
    help="Delete the workspace even when it holds work that exists nowhere else.",
)
def remove_worker_cmd(name: str, force: bool) -> None:
    """Remove a worker workspace and deregister.

    This deletes that worker's checkouts, so it refuses while they hold
    work that is not committed or not pushed anywhere. `--force` deletes
    it anyway.

    Examples:
      rite remove worker alpha
      rite remove worker alpha --force
    """
    from rite_ai.workspace import remove_worker

    root = _find_project_root()
    result = remove_worker(root, name, force=force)
    if result.ok:
        click.echo(result.message)
        return
    click.echo(result.message, err=True)
    for item in result.unsaved:
        click.echo(f"  {item.describe()}", err=True)
    if result.unsaved:
        click.echo(
            f"  Commit and push it, or copy workers/{name}/ elsewhere, then "
            "re-run. `--force` deletes it.",
            err=True,
        )
    raise SystemExit(1)


# --- Context directory (SPEC §8.6) ---


@cli.group()
def context() -> None:
    """Manage the project context directory."""


@context.command("add")
@click.argument("filename")
@click.argument("trigger")
@click.argument("description")
@click.option("--file", "source_path", default=None, help="Copy an existing file in")
def context_add(
    filename: str, trigger: str, description: str, source_path: str | None
) -> None:
    """Add a context entry — a file under `.rite/context/`, indexed with a
    trigger (when to consult it) and a one-line description.

    Examples:
      rite context add database.md "Before writing migrations" "Postgres conventions"
      rite context add notes.md "Before X" "Y" --file ./my-notes.md
    """
    from rite_ai.context import add_context

    root = _require_project_root()
    err = add_context(
        root,
        filename,
        trigger,
        description,
        source_path=Path(source_path) if source_path else None,
    )
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    click.echo(f"added {filename}")


@context.command("remove")
@click.argument("filename")
def context_remove(filename: str) -> None:
    """Remove a context entry and its file.

    Examples:
      rite context remove database.md
    """
    from rite_ai.context import remove_context

    root = _require_project_root()
    err = remove_context(root, filename)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    click.echo(f"removed {filename}")


@context.command("list")
def context_list() -> None:
    """List context entries.

    Examples:
      rite context list
    """
    from rite_ai.context import list_context

    root = _find_project_root()
    entries = list_context(root)
    if not entries:
        click.echo("no context entries")
        return
    for e in entries:
        click.echo(f"  {e.file:20s} {e.trigger}")


# --- Knowledge Base ---


@cli.group()
def kb() -> None:
    """Manage the knowledge base."""


@kb.command("add")
@click.argument("source")
@click.option("--full", is_flag=True, help="Store full content (licensing warning)")
def kb_add(source: str, full: bool) -> None:
    """Add a URL (snapshot) or file to the knowledge base.

    Examples:
      rite kb add https://example.com/guide
      rite kb add coding-standards.md
      rite kb add --full https://example.com/spec
    """
    from rite_ai.kb import add_file, add_link

    root = _require_project_root()
    source_path = Path(source)
    if source_path.is_file():
        err = add_file(root, source_path)
    elif source.startswith("http://") or source.startswith("https://"):
        if full:
            click.echo("warning: --full stores complete content; check licensing")
        err = add_link(root, source, full=full)
    else:
        click.echo(f"not a file or URL: {source}", err=True)
        raise SystemExit(1)

    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    click.echo(f"added: {source}")


@kb.command("refresh")
def kb_refresh() -> None:
    """Re-fetch all link snapshots."""
    from rite_ai.kb import refresh

    root = _require_project_root()
    report = refresh(root)
    if not report.messages:
        click.echo("nothing to refresh")
        return
    for msg in report.messages:
        click.echo(msg, err=msg.startswith("error:"))
    if report.failures:
        # Errors on stderr and a non-zero exit. Every line went to stdout
        # and the command exited 0, so a refresh that failed on half the
        # KB was indistinguishable — to a cron entry, a script, or a
        # session routine — from a clean one.
        click.echo(
            f"{len(report.failures)} entr"
            f"{'y' if len(report.failures) == 1 else 'ies'} failed to fetch "
            "— see the error line(s) above.",
            err=True,
        )
        raise SystemExit(1)


@kb.command("list")
def kb_list() -> None:
    """List knowledge base entries."""
    from rite_ai.kb import list_entries

    root = _find_project_root()
    entries = list_entries(root)
    if not entries:
        click.echo("no KB entries")
        return
    for e in entries:
        click.echo(f"  {e.entry_type:10s} {e.name}")


# --- Publish Gate ---


@cli.group()
def publish() -> None:
    """Publish gate commands."""


@publish.command("install-hook")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite a pre-push hook this tool did not install.",
)
def publish_install_hook(force: bool) -> None:
    """Install the pre-push hook that runs the gate on every push.

    `rite init` does this already. This is for a project that is already
    initialised and has no working hook — because `core.hooksPath` redirected
    it, or someone removed it — where re-running `rite init` is no help: it
    offers to wipe the project's config rather than touch the hook.

    Examples:
      rite publish install-hook
      rite publish install-hook --force
    """
    from rite_ai.gate.hook import install_pre_push_hook

    result = install_pre_push_hook(_gate_root(), force=force)
    # `install_pre_push_hook` already phrases both outcomes for a human
    # ("installed <path>" / the full reason it refused) — don't re-prefix it.
    click.echo(result.message, err=not result.ok)
    raise SystemExit(0 if result.ok else 1)


@publish.command("install-ci")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Replace an existing workflow at that path, whoever wrote it.",
)
def publish_install_ci(force: bool) -> None:
    """Install the GitHub Actions workflow that runs the gate in CI.

    The exact analogue of `install-hook`, and it exists for the same reason
    SPEC §11.5 gives for that one: `rite init` does this already, and
    re-running `rite init` is not the remedy for a project that is already
    initialised — it offers to wipe the project's config rather than touch
    the workflow.

    §11.5.1 is why it matters more than the hook: `core.hooksPath` can
    disarm the local gate without the developer doing anything and with no
    signal that it happened, "which makes [CI] the load-bearing one, not
    the backup". A project with no workflow has no layer that a local git
    config cannot switch off.

    Refuses to replace a workflow already at that path — including one rite
    itself wrote, because the first thing anyone does to a generated
    workflow is edit it. `--force` is the deliberate way to say replace it.

    Examples:
      rite publish install-ci
      rite publish install-ci --force
    """
    from rite_ai.cli.init.scaffold import (
        CI_WORKFLOW_REL_PATH,
        render_ci_workflow,
        write_ci_workflow,
    )

    root = _gate_root()
    path = root / CI_WORKFLOW_REL_PATH

    if force:
        if not (root / ".git").exists():
            click.echo(
                f"{root} is not a git repository (no .git/) — there is no CI "
                "to run a workflow.",
                err=True,
            )
            raise SystemExit(1)
        # Asked BEFORE writing: `--force` on a repo with no workflow at all
        # replaces nothing, and saying "replaced what was there" would be
        # generated output stating something nobody observed.
        replaced = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_ci_workflow())
        except OSError as e:
            click.echo(f"could not write {path}: {e}", err=True)
            raise SystemExit(1) from None
        click.echo(
            f"installed {path}" + (" (replaced what was there)" if replaced else "")
        )
        raise SystemExit(0)

    result = write_ci_workflow(root)
    if result.status == "written":
        click.echo(f"installed {path}")
        raise SystemExit(0)

    # Every refusal names what is true of the file AND whether the gate
    # runs anyway — those are different questions, and a workflow someone
    # else wrote that calls `rite publish check` covers this project just
    # as well as one rite generated.
    reasons = {
        "already_ours": f"{path} already exists and was written by rite — "
        "left exactly as it is, including any changes you made to it.",
        "foreign": f"{path} already exists and carries no rite marker, so it "
        "was left alone.",
        "unreadable": f"{path} exists but could not be read "
        f"({result.detail}), so it was left alone.",
        "not_a_repo": f"{root} is not a git repository (no .git/) — there is "
        "no CI to run a workflow.",
        "write_failed": f"could not write {path}: {result.detail}",
    }
    fallback = f"unhandled state: {result.status}"
    click.echo(reasons.get(result.status, fallback), err=True)
    if result.status in ("already_ours", "foreign"):
        click.echo(
            "  the publish gate DOES run in CI from it — it calls `rite publish check`."
            if result.runs_gate
            else "  the publish gate does NOT run in CI from it — it does not "
            "call `rite publish check`. Add that to it, or re-run with "
            "--force to replace it with the generated workflow.",
            err=True,
        )
    raise SystemExit(1)


def _warn_summary(report) -> str:
    """What the warning is ABOUT, not just that there is one.

    "warnings (stale suppressions)" was printed for both causes, including
    when the cause was a pre-existing finding and no suppression was stale.
    A reader then goes looking for a suppression file that has nothing wrong
    with it.
    """
    parts = []
    if report.pre_existing:
        parts.append(f"{len(report.pre_existing)} finding(s) predating this push")
    if report.stale_suppressions:
        parts.append(f"{len(report.stale_suppressions)} stale suppression(s)")
    return ", ".join(parts) or "nothing blocking"


@publish.command("check")
@click.option("--rev-range", default=None, help="Git revision range to scan")
@click.option(
    "--strict",
    is_flag=True,
    help=(
        "Treat warnings as blocking. A warning is something nobody has "
        "looked at — a finding that predates this push, or a suppression "
        "entry matching nothing. Off by default so unrelated work is not "
        "stopped; on for CI, where an unexamined finding is worth failing."
    ),
)
def publish_check(rev_range: str | None, strict: bool) -> None:
    """Dry-run the publish gate — scan for secrets and local paths.

    Examples:
      rite publish check
      rite publish check --rev-range origin/main..HEAD
    """
    from rite_ai.gate import run_gate
    from rite_ai.gate.gate import _partial_lines

    root = _gate_root()
    from rite_ai.gate import suppression
    from rite_ai.gate.suppression import stale_hint

    report = run_gate(root, rev_range=rev_range)

    for finding in report.findings:
        click.echo(
            f"  [{finding.source}] {finding.file}:{finding.line} "
            f"{finding.rule_id}: {finding.match_preview}"
        )
        # Without this, the command a human actually types names the finding
        # and then leaves them to work out what a suppression line looks
        # like: the fingerprint was printed only by `format_report`, which
        # serves the hook and `python -m rite_ai.gate`.
        click.echo(
            f"    fingerprint: {finding.content_fingerprint or finding.fingerprint}"
        )
    if report.findings:
        click.echo(suppression.HOW_TO_SUPPRESS)

    for entry, n in suppression.covering_more_than_one(
        report.suppressed, report.suppressions
    ):
        # On a run that could not complete, the count is over the sources that
        # did — so it is a floor, not a total, and saying "covers 2" flat would
        # understate an entry that is also covering what the failed source
        # would have found.
        over = " (of what could be scanned)" if report.errors else ""
        click.echo(f"\none entry covers {n} findings{over}: {entry.fingerprint}")

    if report.stale_suppressions:
        click.echo(f"\n{len(report.stale_suppressions)} stale suppression(s):")
        for fp in report.stale_suppressions:
            # The dataclass repr, which is what this printed on its own, names
            # the fingerprint and the reason and stops there. `rite publish
            # check` is the command a human types, so whatever the other
            # report says about a stale entry has to be said here too — hence
            # one shared `stale_hint` rather than two copies that drift.
            click.echo(f"  {fp.fingerprint} — reason: {fp.reason}")
            hint = stale_hint(fp, report.findings)
            if hint:
                click.echo(hint)

    if report.errors or report.partial_findings:
        for err in report.errors:
            click.echo(f"error: {err}", err=True)
        for line in _partial_lines(report):
            click.echo(line, err=True)

    # BRANCHED ON `outcome`, NOT ON THE EXIT CODE. Now that a warning exits
    # 0, branching on the code would make this line unreachable and the gate
    # would report "clean" over a stale suppression — trading one conflation
    # for another.
    if report.outcome == "clean":
        click.echo("gate: clean")
    elif report.outcome == "warn":
        click.echo(f"gate: warnings ({_warn_summary(report)})")
        if not strict:
            click.echo("  not blocking — `--strict` makes warnings blocking")
    elif report.outcome == "fail":
        click.echo(f"gate: FAIL ({len(report.findings)} finding(s))")
    else:
        click.echo("gate: ERROR (gate could not run)")

    if report.unreadable_files:
        # The command the runbook, the CI template and `verify-and-push.sh`
        # all name. The skip report reached the hook and `python -m
        # rite_ai.gate` and not this, which is where anyone would see it.
        click.echo(
            f"⚠ {len(report.unreadable_files)} tracked file(s) could NOT be "
            "read and were not scanned — this is not the same as clean: "
            + ", ".join(sorted(report.unreadable_files)[:5])
        )

    raise SystemExit(report.exit_code_for(strict=strict))


@publish.command("pre-push")
def publish_pre_push() -> None:
    """Range-scoped scan for the `pre-push` git hook (reads stdin).

    Not meant to be typed by a human — this is what the installed
    `.git/hooks/pre-push` execs (Stage 2 #3). Reads git's pre-push protocol
    from stdin, scans each pushed range (not full history — that's what
    keeps this "seconds to run" per §11), and exits nonzero if any range
    fails.
    """
    import sys

    from rite_ai.gate import EXIT_CLEAN, run_gate
    from rite_ai.gate.gate import format_report
    from rite_ai.gate.hook import compute_pre_push_ranges

    root = _gate_root()
    lines = sys.stdin.read().splitlines()

    if not lines:
        click.echo("rite publish gate: nothing to scan")
        raise SystemExit(0)

    worst = EXIT_CLEAN
    for rev_range in compute_pre_push_ranges(lines):
        report = run_gate(root, rev_range=rev_range)
        click.echo(f"rite publish gate — {rev_range}")
        click.echo(format_report(report))
        worst = max(worst, report.exit_code)

    raise SystemExit(worst)


# --- Ticket backend (SPEC §6.1, D-40: NOT `rite ticket` — see main.py header) ---
#
# `rite board` is mechanical ticket-backend CRUD — create/move/list/query/
# label/link — never the end-to-end "claim -> work -> PR -> review" workflow,
# which is the Dispatch slash command per D-40. Built ahead of the
# questionnaire/workspace work per the sequencing override in
# docs/private/IMPLEMENTATION_PLAN.md: the owner's own ticket queue was living in a
# session's memory, which had already stalled work three times.


def _ticket_backend(board_role: str = "workers"):
    """Build a TicketBackend from this project's config.yaml (SPEC §8.3)."""
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.tickets import BackendError, create_backend_from_config

    root = _find_project_root()
    config_path = root / ".rite" / "config.yaml"
    config = parse_config(config_path)
    if isinstance(config, ParseError):
        return None, f"config error: {config.message}"

    tb = config.ticket_backend
    if tb.type == "none":
        # Pointing at a key in a file is only useful once the file exists,
        # and only actionable once the accepted values are named.
        if not config_path.is_file():
            return None, f"not a rite project ({root}) — run `rite init` first"
        return (
            None,
            f"no ticket backend configured — set ticket_backend.type in "
            f"{config_path} to 'jira' (with site + projects) or 'github' "
            f"(with repo)",
        )

    backend = create_backend_from_config(
        tb, board_role=board_role, credentials=config.credentials
    )

    if isinstance(backend, BackendError):
        return None, backend.message
    return backend, None


def _render_tickets(result) -> None:
    """Print a page of tickets, and say so when it IS only a page. A
    hundred rows with nothing after them reads as "this is the board";
    on a busy board it is the first hundred of several hundred, and the
    difference is invisible unless the listing states it."""
    for ticket in result:
        click.echo(f"  {ticket.id}  [{ticket.status}]  {ticket.title}")
    if getattr(result, "truncated", False):
        click.echo(
            f"  … showing the first {len(result)} — there are more. "
            "Narrow with --status/--label/--assignee, or use `rite board "
            "query` for a backend-native search."
        )


@cli.group()
def board() -> None:
    """Direct ticket operations — create/move/list/query/label/link.

    One command per action against the configured ticket backend
    (`ticket_backend` in .rite/config.yaml). For the end-to-end ticket
    workflow — picking work up, doing it, handing it back — use the
    Dispatch `/ticket` command instead.
    """


@board.command("create")
@click.argument("title")
@click.option("--description", "-d", default="", help="Ticket description")
@click.option("--label", "-l", "labels", multiple=True, help="Label (repeatable)")
@click.option(
    "--role",
    default="workers",
    help="Which config.yaml project this creates on: board | workers | testing",
)
def board_create(
    title: str, description: str, labels: tuple[str, ...], role: str
) -> None:
    """Create a ticket.

    Examples:
      rite board create "Fix the bug"
      rite board create "New feature" --role board --label epic
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.create(title, description=description, labels=list(labels))
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    click.echo(f"created {result.id}: {result.url or result.title}")


@board.command("move")
@click.argument("ticket_id")
@click.argument("status")
@click.option("--role", default="workers", help="board | workers | testing")
def board_move(ticket_id: str, status: str, role: str) -> None:
    """Move a ticket to a new status/column.

    Says where the ticket ACTUALLY landed when that is not the column you
    named — a backend whose board has no such column (GitHub has only
    open and closed) still does something, and reporting the request back
    as the outcome is how a move that never happened reads as success.

    Examples:
      rite board move ABC-12 "In Progress"
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.move(ticket_id, status)
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    if isinstance(result, str) and result:
        click.echo(
            f"{ticket_id} -> {result} — this board has no '{status}' column "
            f"of its own; that is where '{status}' maps"
        )
        return
    click.echo(f"{ticket_id} -> {status}")


@board.command("list")
@click.option("--status", default=None)
@click.option("--assignee", default=None)
@click.option("--label", default=None)
@click.option("--role", default="workers", help="board | workers | testing")
def board_list(
    status: str | None, assignee: str | None, label: str | None, role: str
) -> None:
    """List tickets, optionally filtered.

    Examples:
      rite board list --status "In Progress"
    """
    from rite_ai.tickets import BackendError, TicketFilter

    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    filters = TicketFilter(status=status, assignee=assignee, label=label)
    result = backend.list_tickets(filters)
    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    if not result:
        click.echo("no tickets")
        return
    _render_tickets(result)


@board.command("query")
@click.argument("raw_query")
@click.option("--role", default="workers", help="board | workers | testing")
def board_query(raw_query: str, role: str) -> None:
    """Backend-native query — JQL for JIRA, search qualifiers for GitHub.

    Examples:
      rite board query "labels = scheduled ORDER BY created DESC"
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.query(raw_query)
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    if not result:
        click.echo("no tickets")
        return
    _render_tickets(result)


@board.command("show")
@click.argument("ticket_id")
@click.option("--role", default="workers", help="board | workers | testing")
def board_show(ticket_id: str, role: str) -> None:
    """Show one ticket: title, status, labels and description.

    What a Worker reads before it starts. `list` and `query` print one line
    per ticket, without the description the ticket's scope is written in.

    Examples:
      rite board show ABC-12
      rite board show 42
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    from rite_ai.tickets import BackendError

    ticket = backend.read(ticket_id)
    if isinstance(ticket, BackendError):
        click.echo(ticket.message, err=True)
        raise SystemExit(1)
    click.echo(f"{ticket.id}  [{ticket.status}]  {ticket.title}")
    if ticket.labels:
        click.echo(f"labels: {', '.join(ticket.labels)}")
    if ticket.url:
        click.echo(ticket.url)
    click.echo("")
    click.echo(ticket.description.strip() or "(no description)")


@board.command("label")
@click.argument("ticket_id")
@click.argument("labels", nargs=-1)
@click.option(
    "--remove",
    "-r",
    "remove",
    multiple=True,
    help="Label to take off (repeatable). Removing one the ticket does not "
    "carry is not an error.",
)
@click.option("--role", default="workers", help="board | workers | testing")
def board_label(
    ticket_id: str, labels: tuple[str, ...], remove: tuple[str, ...], role: str
) -> None:
    """Add worker-name labels — the assignment mechanism (SPEC §9.10).

    LABELS are ADDED to whatever the ticket already carries; nothing is
    replaced. Reassigning therefore takes both halves — add the new
    worker, `--remove` the old one — or the ticket answers a query for
    each of them.

    Examples:
      rite board label ABC-12 alpha scheduled
      rite board label ABC-12 beta --remove alpha
      rite board label ABC-12 --remove alpha
    """
    if not labels and not remove:
        click.echo("nothing to do — give a label to add, or --remove", err=True)
        raise SystemExit(1)
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.label(ticket_id, list(labels), remove=list(remove))
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    parts = []
    if labels:
        parts.append(f"labelled {', '.join(labels)}")
    if remove:
        parts.append(f"removed {', '.join(remove)}")
    click.echo(f"{ticket_id}: {'; '.join(parts)}")


@board.command("assign")
@click.argument("ticket_id")
@click.argument("worker")
@click.option("--role", default="workers", help="board | workers | testing")
def board_assign(ticket_id: str, worker: str, role: str) -> None:
    """Set the backend's own assignee field (SPEC §6.1's `assign`).

    This is the backend's native assignee — a GitHub issue assignee, a
    JIRA assignee — which is what a human sees on the board. It is NOT
    how rite decides who owns a ticket: that is the worker-name label
    (`rite board label`, SPEC §9.10), which is what every rite query
    filters on. Set both if you want the board to read the way rite does.

    Takes a person, not a rite worker name: a display name, an email
    address, or an accountId on JIRA; a login on GitHub. `alpha` is a
    worker, and no ticket backend has ever heard of it — use `rite board
    label` for that.

    Examples:
      rite board assign ABC-12 "Ada Lovelace"
      rite board assign ABC-12 ada@example.com
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.assign(ticket_id, worker)
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    click.echo(f"{ticket_id}: assigned to {worker}")


@board.command("link")
@click.argument("ticket_id")
@click.argument("target_id")
@click.option("--type", "link_type", default="Blocks", help="Link type, e.g. Blocks")
@click.option(
    "--role",
    default="workers",
    help="Which project TICKET_ID lives on: board | workers | testing",
)
def board_link(ticket_id: str, target_id: str, link_type: str, role: str) -> None:
    """Link TICKET_ID to TARGET_ID (default: TICKET_ID is blocked by TARGET_ID).

    Examples:
      rite board link ABC-12 XYZ-4
    """
    backend, err = _ticket_backend(role)
    if err:
        click.echo(err, err=True)
        raise SystemExit(1)
    result = backend.link(ticket_id, target_id, link_type)
    from rite_ai.tickets import BackendError

    if isinstance(result, BackendError):
        click.echo(result.message, err=True)
        raise SystemExit(1)
    # Measured against a live JIRA, `POST /issueLink` reads as
    # "inwardIssue <type.outward> outwardIssue" — the opposite of what the
    # field names suggest. `link()` now sends target_id as the inward
    # issue precisely so that this sentence is true; before that fix the
    # API recorded "ABC-12 blocks XYZ-4" while this line printed "ABC-12
    # is blocked by XYZ-4", inverting the one relationship SPEC §6.2
    # says rite's dependency logic depends on.
    #
    # "is blocked by" is a property of the "Blocks" type alone, so only
    # "Blocks" may claim it — asserting it for a `--type Relates` link
    # states a relationship that was never created.
    if link_type.lower() == "blocks":
        click.echo(f"{ticket_id} is blocked by {target_id} (type: {link_type})")
    else:
        click.echo(
            f"linked {ticket_id} to {target_id} — "
            f"{target_id} is the inward issue of a '{link_type}' link, "
            f"{ticket_id} the outward one"
        )


# --- Worker scheduling (SPEC §2.7, D-44–D-48) ---


def _load_config_for_write():
    """Read config.yaml, or a fresh default `ProjectConfig` if it doesn't
    exist yet — `rite schedule` should work on a project that hasn't run
    `rite init`'s full flow, same as other config-touching commands."""
    from rite_ai.config.models import ProjectConfig
    from rite_ai.config.parse import ParseError, parse_config

    root = _find_project_root()
    config_path = root / ".rite" / "config.yaml"
    config = parse_config(config_path)
    if isinstance(config, ParseError):
        click.echo(f"config error: {config.message}", err=True)
        raise SystemExit(1)
    return root, config if isinstance(config, ProjectConfig) else ProjectConfig()


@cli.group()
def schedule() -> None:
    """This project's worker schedule (SPEC §2.7)."""


@schedule.command("show")
def schedule_show() -> None:
    """Print the current schedule and timezone.

    Examples:
      rite schedule show
    """
    _, config = _load_config_for_write()
    sched = config.schedule
    click.echo(f"timezone: {sched.timezone or '(not set)'}")
    if not sched.windows:
        click.echo("no windows configured")
        return
    for w in sched.windows:
        click.echo(f"  {w.hours}  workers={w.workers}")


@schedule.command("set")
@click.argument("hours")
@click.argument("workers", type=int)
def schedule_set(hours: str, workers: int) -> None:
    """Set the Worker count for an hour range — an upsert: splits or trims
    any window it overlaps, leaves the rest untouched.

    Examples:
      rite schedule set 09:00-18:00 3
      rite schedule set 18:00-09:00 0
    """
    from rite_ai.cli.init.scaffold import write_config
    from rite_ai.config.models import ScheduleConfig
    from rite_ai.schedule import ScheduleError, upsert_window, validate_schedule

    _require_project_root()
    root, config = _load_config_for_write()

    result = upsert_window(config.schedule.windows, hours, workers)
    if isinstance(result, ScheduleError):
        click.echo(result.message, err=True)
        raise SystemExit(1)

    new_schedule = ScheduleConfig(timezone=config.schedule.timezone, windows=result)
    problems = validate_schedule(new_schedule, config.sandbox.max_concurrent_workers)
    cap_problems = [p for p in problems if "exceeding" in p]
    if cap_problems:
        for p in cap_problems:
            click.echo(p, err=True)
        raise SystemExit(1)

    config.schedule = new_schedule
    write_config(root / ".rite", config)
    click.echo(f"{hours} -> {workers} workers")
    for p in problems:
        if p not in cap_problems:
            click.echo(f"warning: {p}")


@schedule.command("set-timezone")
@click.argument("tz")
def schedule_set_timezone(tz: str) -> None:
    """Set the schedule's timezone — required before any window is
    meaningful (D-48).

    Examples:
      rite schedule set-timezone Europe/Warsaw
    """
    from rite_ai.cli.init.scaffold import write_config

    # `_load_config_for_write` deliberately tolerates a project whose
    # config.yaml does not exist yet, and is also what the machine-wide
    # readers (`rite budget`, `rite pool status`) go through — so the
    # refusal belongs on the writers, not on the shared helper. Without
    # it this command reached `write_config` with no `.rite/` to write
    # into and died on a FileNotFoundError traceback out of pathlib.
    _require_project_root()
    root, config = _load_config_for_write()
    config.schedule.timezone = tz
    write_config(root / ".rite", config)
    click.echo(f"timezone set to {tz}")


# --- Workspace preparation (SPEC §2.1) ---


def _worker_modules_or_exit(root, worker: str):
    """`workers/<worker>/` and the modules that worker was created with, or
    exit with the reason. Shared by `rite prepare` and `rite sandbox start`,
    which prepares the workspace before the sandbox copies it."""
    from rite_ai.config.parse import ParseError, parse_modules, parse_worker

    all_modules = parse_modules(root / ".rite" / "modules.yaml")
    if isinstance(all_modules, ParseError):
        click.echo(f"cannot parse modules.yaml: {all_modules.message}", err=True)
        raise SystemExit(1)

    worker_dir = root / "workers" / worker
    manifest_path = worker_dir / "worker.yml"
    if not manifest_path.is_file():
        click.echo(
            f"no such worker: workers/{worker}/ (run `rite add worker` first)",
            err=True,
        )
        raise SystemExit(1)

    manifest = parse_worker(manifest_path)
    if isinstance(manifest, ParseError):
        click.echo(f"cannot parse worker.yml: {manifest.message}", err=True)
        raise SystemExit(1)

    # Scope to exactly the modules this worker was created with — not
    # every module in the project, which a worker may never have cloned.
    return worker_dir, [m for m in all_modules if m.name in manifest.modules]


@cli.command()
@click.option("--worker", "-w", required=True, help="Worker name")
@click.option(
    "--branch", "-b", default=None, help="Ticket branch (default: worker's own)"
)
def prepare(worker: str, branch: str | None) -> None:
    """Prepare a worker's workspace before a task — right repos, right
    branches, no residue from the previous task (SPEC §2.1). Idempotent;
    a dirty tree fails loudly rather than being discarded.

    Examples:
      rite prepare --worker alpha
      rite prepare --worker alpha --branch feature/ABC-12
    """
    from rite_ai.workspace import prepare_workspace

    root = _find_project_root()
    worker_dir, modules = _worker_modules_or_exit(root, worker)

    result = prepare_workspace(worker_dir, modules, root, branch=branch)
    click.echo(result.summary())
    if modules:
        # Resolved NOW, from modules.yaml and detection. A Worker's CLAUDE.md
        # lists them as of `rite add worker`; modules.yaml may have been
        # corrected since, and this runs before every task.
        sandbox, sandbox_warning = _resolved_sandbox(root)
        click.echo("\ncommands — run inside each module's checkout:")
        if sandbox_warning:
            click.echo(f"  note: {sandbox_warning}")
        for m in modules:
            click.echo(f"  {m.name}/")
            _echo_command_rows(m, root, sandbox, "    ")
    if not result.ok:
        # Name the blockers on their own line: `summary()` lists every
        # module, passing or not, so on a large worker the one that
        # actually stopped the run is easy to miss in the scroll.
        names = ", ".join(m.module for m in result.blocking)
        click.echo(f"blocked on: {names}", err=True)
        raise SystemExit(1)


# --- Heartbeat (SPEC §9.8) ---


@cli.command()
@click.option("--worker", "-w", required=True, help="Worker name")
@click.option("--ticket", "-t", default="", help="Ticket the worker is on")
@click.option("--message", "-m", default="", help="One line on what it's doing")
def heartbeat(worker: str, ticket: str, message: str) -> None:
    """Record a Worker's "still alive" beat (SPEC §9.8).

    Call it every `heartbeat.interval_minutes` (config.yaml, default 10)
    for as long as a Worker is working. This is what `rite status` and
    `rite watchdog` read: a Worker that misses `stall_threshold` beats in
    a row reports STALLED, and one that has never beaten but holds claims
    reports "no heartbeat ever recorded". One with neither a heartbeat nor a
    claim reads as not started, which is not a stall.

    Examples:
      rite heartbeat --worker alpha
      rite heartbeat --worker alpha --ticket ABC-12 --message "running tests"
    """
    # The only writer of `.rite/heartbeats/` anywhere in the codebase.
    # `detect_stalls` (and through it `rite status` and `rite watchdog`)
    # was reading that directory before anything wrote to it, which made
    # every registered Worker read as permanently stalled.
    from rite_ai.reporting.heartbeat import write_heartbeat

    root = _require_project_root()
    write_heartbeat(root, worker, ticket=ticket, message=message)
    click.echo(f"heartbeat recorded for '{worker}'")
    _warn_if_unregistered(worker)


# --- Watchdog ---


@cli.command()
def watchdog() -> None:
    """Cheap liveness check — no LLM (SPEC §3.5). Meant to run every ~5
    minutes from a scheduler (cron, launchd), and by a Manager on its own
    polling cadence. Zero tokens.

    \b
    EXIT CODES — three, because two conditions need different responses:
      0  nothing needs attention
      2  every finding is an ANSWERABLE QUESTION — one or more workers are
         alive, beating, and blocked on a decision. Go and reply.
      1  something may be WRONG — a stalled (probably dead) worker, an
         unregistered worker nothing is watching, a config error, or an
         outbox blocker. Go and investigate. If a project has both, this
         wins: 1 is the one you cannot resolve by typing an answer.

    Anything non-zero still means "attention", so a caller that only tests
    `|| notify` keeps working unchanged.

    Examples:
      rite watchdog
      */5 * * * * cd /path/to/project && rite watchdog || notify-manager
      rite watchdog; case $? in
        2) echo "answer a question";;
        1) echo "investigate";;
      esac
    """
    from rite_ai.label import decorate
    from rite_ai.watchdog import run_watchdog_check

    root = _find_project_root()
    result = run_watchdog_check(root)
    if not result.needs_attention:
        click.echo(decorate(root, "ok — nothing needs attention"))
        return
    for reason in result.reasons:
        # `decorate`, not a prefix concatenation: a watchdog reason can run
        # to several lines (a YAML parse error quotes the offending line),
        # and a continuation line scrolling into view unattributed is the
        # exact problem this labelling exists to fix.
        click.echo(decorate(root, reason))
    # A blocked worker is answerable; everything else is an investigation.
    # `len(reasons) == len(blocked)` is the test for "answerable only" —
    # counting rather than re-deriving, so a condition added to
    # `run_watchdog_check` later cannot silently fall into the wrong
    # bucket: a new reason nobody mapped makes this 1, which is the safe
    # side.
    only_questions = bool(result.blocked) and len(result.reasons) == len(result.blocked)
    raise SystemExit(2 if only_questions else 1)


# --- Continuous handover snapshot (SPEC §9.10.1) ---


@cli.group()
def handover() -> None:
    """The continuous "what's the state right now" snapshot — distinct
    from `rite stop`'s transition-triggered handover comment."""


@handover.command("write")
@click.option("--ticket", default="", help="Current ticket ID")
@click.option("--progress", default="", help="Short progress summary")
@click.option("--next-step", "next_step", default="", help="What happens next")
@click.option("--blocker", "blockers", multiple=True, help="Open blocker (repeatable)")
@click.option(
    "--worker",
    "-w",
    default="",
    help="Whose snapshot to write. Every worker keeps its own; omit for a "
    "single-coordinator project.",
)
@click.option(
    "--clear",
    "clear",
    is_flag=True,
    help="Deliberately record an empty snapshot, discarding whatever the "
    "previous one held. Without it, a write carrying no content is "
    "refused rather than silently erasing the previous one.",
)
@click.option(
    "--spec-fallback",
    "spec_fallback",
    default="",
    metavar="UNIT",
    help="The spec unit whose slice was not enough, so the whole spec had to "
    "be read. Counted once for the insufficiency rate, however often the "
    "snapshot is rewritten.",
)
def handover_write(
    ticket: str,
    progress: str,
    next_step: str,
    blockers: tuple[str, ...],
    worker: str,
    clear: bool,
    spec_fallback: str,
) -> None:
    """Overwrite one worker's handover snapshot — call this on a schedule
    (every few minutes), not only when stopping. Ephemeral state: each call
    replaces that worker's previous snapshot entirely, and leaves every
    other worker's alone.

    A write with nothing in it is refused, because "replaces entirely"
    means an empty write is a deletion. Pass `--clear` to mean it.

    Examples:
      rite handover write --ticket ABC-12 --progress "wiring the CLI" \\
        --next-step "add tests" --blocker "waiting on JIRA credentials"
    """
    from rite_ai.handover import has_content, write_snapshot

    root = _require_project_root()
    spec_fallback = spec_fallback.strip()
    if not clear and not has_content(
        ticket, progress, next_step, list(blockers), spec_fallback
    ):
        # Refused, not accepted-as-empty. Every call replaces that
        # session's snapshot entirely, so an argument-less call is a
        # deletion that printed "handover snapshot written" and exited 0
        # — measured against a snapshot holding an open blocker, from the
        # command whose whole purpose is carrying that blocker across a
        # session's death.
        click.echo(
            "nothing to record — pass at least one of --ticket, --progress, "
            "--next-step, --blocker or --spec-fallback. Every write REPLACES "
            "this session's "
            "previous snapshot, so an empty one would discard whatever it "
            "was holding. Use --clear if discarding it is what you mean.",
            err=True,
        )
        raise SystemExit(2)
    write_snapshot(
        root,
        ticket=ticket,
        progress=progress,
        next_step=next_step,
        blockers=list(blockers),
        worker=worker,
        spec_fallback=spec_fallback,
    )
    if spec_fallback:
        # After the snapshot is written, so a write that fails and is retried
        # does not count its fallback twice. Deduplicated against the log, not
        # the snapshot — see `record_fallback_once`.
        from rite_ai.spec.telemetry import record_fallback_once

        if record_fallback_once(root, spec_fallback, worker=worker):
            click.echo(f"spec fallback recorded for {spec_fallback}")
    click.echo(f"handover snapshot written for {worker or 'the coordinator'}")


@handover.command("show")
def handover_show() -> None:
    """Print the current handover snapshot — read this at session
    startup, before evaluating `rite start`'s orientation table.

    Examples:
      rite handover show
    """
    from rite_ai.handover import read_snapshots

    root = _find_project_root()
    snapshots = read_snapshots(root)
    if not snapshots:
        click.echo("no handover snapshot recorded yet")
        return
    # Every session's, not just the newest. A fresh session reads this to
    # reconstruct where work stands (§9.10.1); showing one of three
    # workers told it the other two never existed.
    for i, snapshot in enumerate(snapshots):
        if i:
            click.echo("")
        if len(snapshots) > 1 or snapshot.worker:
            click.echo(f"worker:     {snapshot.worker or '(unnamed session)'}")
        if snapshot.unreadable:
            click.echo(f"UNREADABLE: {snapshot.unreadable}", err=True)
            click.echo(
                "            Whatever that session recorded is still in that "
                "file. This is\n"
                '            NOT "nothing recorded" — read or repair it '
                "before assuming the\n"
                "            work it describes is not in flight.",
                err=True,
            )
            click.echo(f"written:    {snapshot.describe_age()}")
            continue
        click.echo(f"ticket:     {snapshot.ticket or '(none)'}")
        click.echo(f"progress:   {snapshot.progress or '(none)'}")
        click.echo(f"next step:  {snapshot.next_step or '(none)'}")
        if snapshot.blockers:
            click.echo("blockers:")
            for b in snapshot.blockers:
                click.echo(f"  - {b}")
        else:
            click.echo("blockers:   (none)")
        if snapshot.spec_fallback:
            click.echo(f"spec fallback: {snapshot.spec_fallback}")
        # Relative age beside the clock time. This command's own help says
        # "read this at session startup", so its whole audience is someone
        # who does not know what time the previous session stopped.
        click.echo(f"recorded:   {snapshot.describe_age()}")
    if any(s.unreadable for s in snapshots):
        raise SystemExit(1)


# --- Scheduler (SPEC §3.5, §2.7.3, §9.10) ---
#
# `scheduler-tick` is a single top-level command name, not a group
# subcommand — the cron line and launchd plist built by `rite_ai.scheduler`
# invoke it literally as `rite scheduler-tick`, so the two must stay in
# sync (there is no click machinery tying them together).


@cli.command("scheduler-tick")
def scheduler_tick() -> None:
    """One scheduler cycle: the watchdog check, plus the schedule
    window-boundary check that hands over any active Worker when the
    schedule drops to zero (§2.7.3, D-46). No LLM call; safe to run
    unattended from cron/launchd every few minutes. This is what
    `rite scheduler install` wires up — running it directly is mostly for
    testing that wiring.

    Examples:
      rite scheduler-tick
    """
    from datetime import datetime

    from rite_ai.label import decorate
    from rite_ai.scheduler import run_tick

    root = _require_project_root()

    # Local time with its offset: this log is read by a human the morning
    # after, so it should show wall-clock, and the offset keeps it
    # unambiguous across a DST change rather than silently shifting an
    # hour mid-file. Sortable either way.
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

    def emit(message: str) -> None:
        # Every line, not just the first of a tick: `.rite/scheduler.log`
        # is append-only across runs, so a line without its own timestamp
        # cannot be placed once anything else is interleaved.
        for line in decorate(root, message).split("\n"):
            click.echo(f"{stamp}  {line}")

    # Every line this command prints is appended to `.rite/scheduler.log`
    # by cron, or read hours later out of launchd's output — the two places
    # a message is furthest from anything that would identify it.
    result = run_tick(root)
    for message in result.messages:
        emit(message)
    if not result.messages:
        # A tick that finds nothing still has to SAY it found nothing. This
        # is the command cron/launchd runs into `.rite/scheduler.log`, and
        # an empty log is indistinguishable from a scheduler that never
        # fired — the one thing the log exists to tell you.
        emit("nothing to report — no stalled workers, no window transition")
    if not result.ok:
        raise SystemExit(1)


@cli.group()
def loop() -> None:
    """Work the queue instead of stopping after one ticket.

    Today this is `run --dry-run` and nothing else: it prints what a cycle
    would do and does nothing. That ordering is the point — the loop's
    judgement is policy, and policy is worth arguing with before it can
    spend anything.

    Nothing here starts a session, writes to a board, or spends quota, so
    SPEC §9.12 is untouched. The layer that dispatches is a separate
    decision and is not built.
    """


@loop.command("run")
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    help="Print the cycle's decisions without acting. The only mode built.",
)
@click.option(
    "--watch",
    is_flag=True,
    default=False,
    help="Keep cycling until the queue empties or `rite loop stop` is run.",
)
@click.option(
    "--interval",
    default=120.0,
    type=float,
    help="Seconds between cycles with --watch. A Worker session takes minutes.",
)
def loop_run(dry_run: bool, watch: bool, interval: float) -> None:
    """Plan one cycle and print it.

    Exit code carries the verdict, so a caller can branch without parsing
    prose: 0 when there is work or work is in flight, 1 when something could
    not be established, 2 when the board is genuinely empty. "Empty" is the
    only one of the three that is a reason to stop, and it is given its own
    code for exactly that reason.
    """
    from rite_ai.loop import DEADLOCKED, IDLE, UNKNOWN, format_cycle, plan_cycle

    root = _require_project_root()

    if not dry_run:
        # Refused rather than silently doing the dry run: a user who asked
        # for the acting version and got the reporting one would believe
        # work had been dispatched.
        click.echo(
            "`--no-dry-run` is not built. This command reports what a cycle "
            "would do; nothing dispatches yet, and the layer that does spends "
            "quota and needs its own decision.",
            err=True,
        )
        raise SystemExit(1)

    board, _ = _ticket_backend("workers")
    from rite_ai.sandbox import worker_sandbox_status

    if watch:
        from rite_ai.loop import watch as watch_loop

        why = watch_loop(
            root,
            interval=interval,
            emit=click.echo,
            board=board,
            sandbox_status=worker_sandbox_status,
        )
        # The drain is the only exit a human asked for, so it is the only one
        # that is a success. "Stopped because it could not tell" must not read
        # as "finished" to whatever started it.
        # Only a drain, an empty queue or a reached limit are finishing.
        # "Stopped because it could not tell" and "stopped because nothing
        # can move" must not read as "finished" to whatever started it — and
        # they must not read as each other either: one needs somebody to look
        # at the machine, the other needs a specific claim released.
        if why in ("drained", IDLE, "limit"):
            return
        raise SystemExit(3 if why == DEADLOCKED else 1)

    cycle = plan_cycle(root, board=board, sandbox_status=worker_sandbox_status)
    for line in format_cycle(cycle):
        click.echo(line)

    if cycle.verdict == UNKNOWN:
        raise SystemExit(1)
    if cycle.verdict == IDLE:
        raise SystemExit(2)
    if cycle.verdict == DEADLOCKED:
        # A single cycle reported this too and exited 0, which is the same
        # "nothing to see" a healthy cycle returns. The verdict a caller most
        # needs to branch on was the one it could not see.
        raise SystemExit(3)


@loop.command("start")
@click.option(
    "--interval",
    default=120.0,
    type=float,
    help="Seconds between cycles. A Worker session takes minutes.",
)
def loop_start(interval: float) -> None:
    """Run the loop in a detached tmux session.

    tmux because it is the only persistence rite already has — there is no
    `Popen`, no `fork` and no `nohup` anywhere in the package. Two costs,
    accepted rather than engineered around: it dies with this machine's tmux
    server, and it does not survive a reboot. Nothing is registered with cron
    or launchd, so `rite loop status` after a reboot says "not running"
    instead of quietly having restarted.
    """
    from rite_ai.loop.session import Refused, start

    result = start(_require_project_root(), interval=interval)
    if isinstance(result, Refused):
        click.echo(result.reason, err=True)
        if result.remedy:
            click.echo(result.remedy, err=True)
        raise SystemExit(1)
    click.echo(f"loop: started as {result.session} — {result.detail}")


@loop.command("status")
def loop_status() -> None:
    """Whether a loop is running for this project, and whether it is
    draining."""
    from rite_ai.loop.session import status

    root = _require_project_root()
    for line in status(root).lines():
        click.echo(line)

    # Suspect claims, here as well as in the cycle body.
    #
    # `plan_cycle` already finds them and `format_cycle` already prints them,
    # so they reach `.rite/loop.log` — which is the right place for them and
    # the wrong place to STOP. A loop meant to run for days writes a log
    # nobody tails, and a claim whose holder died nine days ago is exactly
    # what someone is looking for when they run this command: it is what
    # they type to ask "is the loop all right?".
    #
    # Reported here rather than folded into `status()`: that answers "is a
    # session running", from tmux and a lock file, and knows nothing about a
    # project. Keeping it that way means this line cannot break the answer
    # a person needs when the loop is NOT running.
    try:
        from rite_ai.claims.suspect import lines as suspect_lines
        from rite_ai.claims.suspect import suspect_claims
        from rite_ai.config.parse import load_project

        project = load_project(root)
        if not isinstance(project, list):
            beat = project.config.heartbeat
            suspects = suspect_claims(
                root,
                registered=[w.name for w in project.workers],
                threshold_seconds=beat.interval_minutes * 60 * beat.stall_threshold,
            )
            for line in suspect_lines(suspects):
                click.echo(line)
    except (OSError, ValueError, KeyError, AttributeError) as e:  # noqa: BLE001
        # Never fail `loop status` over the extra half — whether a loop is
        # running is the question asked, and this is volunteered. But NOT a
        # bare `except Exception`: the first draft had one, and it swallowed
        # an ImportError from a wrong symbol name, so the line simply never
        # appeared and the test failed with no clue why. A catch-all around
        # a call you have just written hides your own mistake first.
        click.echo(f"loop: could not check for dead claims — {e}", err=True)


@loop.command("stop")
@click.option(
    "--reason",
    default="stop requested",
    help="Why. Travels onto the board as 'shutting down: <reason>' on any "
    "ticket handed back.",
)
def loop_stop(reason: str) -> None:
    """Ask the loop to stop after the cycle it is in. Kills nothing.

    A killed loop can leave a claim held by a process that no longer exists —
    the failure §2.6 exists for, caused by the stop command. Asking costs at
    most one cycle. `pool/` has no kill path either, for the same reason.
    """
    from rite_ai.loop.session import stop

    result = stop(_require_project_root(), reason)
    click.echo(f"loop: {result.detail}")


@cli.group()
def scheduler() -> None:
    """Install, remove, or check the OS-level cron/launchd registration
    that calls `rite scheduler-tick` unattended. This changes a standing,
    persistent part of your machine's own crontab or launchd agents —
    `install`/`uninstall` are not run by anything else in rite; a human
    runs them, the same way they'd run `crontab -e` themselves."""


@scheduler.command("install")
@click.option(
    "--interval-minutes",
    default=None,
    type=int,
    help="How often the tick runs. Defaults to watchdog.interval_minutes.",
)
@click.option(
    "--backend",
    type=click.Choice(["cron", "launchd"]),
    default=None,
    help="Defaults to launchd on macOS, cron elsewhere.",
)
def scheduler_install(interval_minutes: int | None, backend: str | None) -> None:
    """Register this project's scheduler tick with cron or launchd. The
    cadence comes from `watchdog.interval_minutes` in config.yaml unless
    `--interval-minutes` overrides it.

    Examples:
      rite scheduler install
      rite scheduler install --interval-minutes 10 --backend cron
    """
    from rite_ai.scheduler import install

    # config.yaml's `watchdog.interval_minutes` is the project's stated
    # cadence and the one the watchdog's own stall threshold is reasoned
    # about in — an installer that hardcoded its own 5 registered an agent
    # that disagreed with the config the user had just edited, silently.
    root, config = _load_config_for_write()
    resolved_interval = (
        interval_minutes
        if interval_minutes is not None
        else config.watchdog.interval_minutes
    )
    result = install(root, interval_minutes=resolved_interval, backend=backend)
    click.echo(result.message)
    if result.ok:
        click.echo(f"  runs every {resolved_interval} minute(s)")
    if not result.ok:
        raise SystemExit(1)


@scheduler.command("uninstall")
@click.option(
    "--backend",
    type=click.Choice(["cron", "launchd"]),
    default=None,
    help="Defaults to launchd on macOS, cron elsewhere.",
)
def scheduler_uninstall(backend: str | None) -> None:
    """Remove this project's scheduler tick registration.

    Examples:
      rite scheduler uninstall
    """
    from rite_ai.scheduler import uninstall

    root = _find_project_root()
    result = uninstall(root, backend=backend)
    click.echo(result.message)
    if not result.ok:
        raise SystemExit(1)


@scheduler.command("status")
@click.option(
    "--backend",
    type=click.Choice(["cron", "launchd"]),
    default=None,
    help="Defaults to launchd on macOS, cron elsewhere.",
)
def scheduler_status(backend: str | None) -> None:
    """Report whether this project's scheduler tick is actually running.

    Not just whether it is registered: it reports the last tick that
    really happened, whether launchd still holds the job, and whether the
    last run exited non-zero. A registration that is correct and never
    fires looks identical to a working one until you ask this.

    Examples:
      rite scheduler status
    """
    from rite_ai.scheduler import format_health, health

    root = _find_project_root()
    for line in format_health(health(root, backend=backend)):
        click.echo(line)


# --- Coordinator redundancy pool (SPEC §2.5, D-26–D-29) ---


@cli.group()
def pool() -> None:
    """A small pool of standby coordinator (Manager/Owner) sessions, so a
    dead one has a warm replacement ready (§2.5). Managers/Owner still
    need a human to start — `rite pool fill` is that explicit action;
    nothing spawns a session automatically."""


@pool.command("fill")
@click.option(
    "--count",
    default=None,
    type=int,
    help="Target depth (default: pool.coordinator_standby).",
)
@click.option(
    "--command",
    default="claude",
    show_default=True,
    help="Command each pooled session runs.",
)
def pool_fill(count: int | None, command: str) -> None:
    """Top up the coordinator pool to its target depth. Existing live
    sessions count toward the target — this starts only the gap.

    Examples:
      rite pool fill
      rite pool fill --count 3
    """
    from rite_ai.pool import fill

    # Before anything is started. `_load_config_for_write` resolves with
    # `_find_project_root`, which falls back to cwd, so without this the
    # command starts `coordinator_standby` real `claude` sessions in
    # whatever directory it was run from and records them in a `.rite/` it
    # creates there. Every sibling writer guards — `pool archive` below,
    # `schedule set`, `add worker`. This one is the one where the damage
    # does not wash out: deleting the phantom directory afterwards does
    # not refund the quota the sessions already spent.
    _require_project_root()
    root, config = _load_config_for_write()
    result = fill(
        root,
        config.pool,
        count=count,
        command=command,
        max_slots=config.sandbox.max_concurrent_workers,
    )
    click.echo(result.message)
    if not result.ok:
        raise SystemExit(1)


@pool.command("status")
def pool_status() -> None:
    """Live/stale split for the coordinator pool — the same read-only,
    zero-token probe `rite status` runs (§2.5.2/§2.5.3): no session is
    spawned by this command.

    Examples:
      rite pool status
    """
    from rite_ai.pool import probe

    root, config = _load_config_for_write()
    result = probe(root, config.pool)
    click.echo(f"{len(result.live)}/{result.target} live")
    # Name the live slots, not just count them: these are real sessions on
    # this machine and their names are the only way to attach to one
    # (§2.5.1's takeover path) or to shut one down.
    for name in result.live:
        click.echo(f"  {name}")
    if result.stale:
        click.echo(f"stale: {', '.join(result.stale)}")
    if result.warn:
        click.echo(result.message)


@pool.command("archive")
@click.option(
    "--after",
    "after_minutes",
    default=None,
    type=int,
    help="Minutes a slot must be continuously unreachable before it is "
    "archived (default: pool.archive_after_minutes).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be archived and released; change nothing.",
)
def pool_archive(after_minutes: int | None, dry_run: bool) -> None:
    """Retire pool slots whose sessions are provably gone, and release the
    claims they died holding.

    A session that ends without running its shutdown hook — killed,
    crashed, machine restarted — never releases its claims, so its work
    keeps reading as live in `rite status` and keeps refusing other
    sessions' overlapping claims. This is the cleanup for that: it
    retires the slot and releases those claims together, recording both
    in `.rite/pool-archive.jsonl`.

    Only slots that fail the OS-level liveness probe continuously for the
    configured window are touched; a session that answers the probe is
    never archived, and if the probe cannot run at all this refuses
    rather than guessing.

    Examples:
      rite pool archive --dry-run
      rite pool archive
      rite pool archive --after 5
    """
    from rite_ai.pool import archive

    # `_load_config_for_write` is shared with the machine-wide READERS
    # (`rite budget`, `rite pool status`), so it cannot carry the guard
    # itself — the refusal belongs on each writer. This one archives pool
    # slots and releases the claims they died holding, writing
    # `.rite/pool.json` and `.rite/pool-archive.jsonl`; outside a project
    # it manufactured both. Round 3 guarded the ten writers it had
    # measured and `pool archive` was not among them, which is what
    # `TestNoCommandInventsAProjectWhereItStands` now derives rather than
    # lists.
    _require_project_root()
    root, config = _load_config_for_write()
    result = archive(root, config.pool, after_minutes=after_minutes, dry_run=dry_run)
    click.echo(result.message)
    for entry in result.archived:
        minutes = int(entry.unreachable_seconds // 60)
        click.echo(f"  {entry.name} — unreachable {minutes}m")
        for path in entry.claim_paths:
            click.echo(f"    released claim: {path}")
    if not result.ok:
        raise SystemExit(1)


@pool.command("history")
@click.option(
    "--limit",
    "-n",
    default=10,
    show_default=True,
    help="How many of the most recent entries to show (0 for all).",
)
def pool_history(limit: int) -> None:
    """What `rite pool archive` has retired, and which claims it released.

    The archive log is the answer to "my claim was here yesterday and now
    it's gone" — the slot that held it, when it stopped answering, and
    why it was retired.

    Examples:
      rite pool history
      rite pool history --limit 0
    """
    from rite_ai.pool import read_archive

    root = _find_project_root()
    records = read_archive(root)
    if not records:
        click.echo("nothing archived yet")
        return

    shown = records if limit <= 0 else records[-limit:]
    if len(shown) < len(records):
        click.echo(f"{len(records)} entries, showing the last {len(shown)}:")
    for record in shown:
        when = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(record.get("timestamp", 0))
        )
        click.echo(f"{when}  {record.get('slot', '(unnamed)')}")
        reason = record.get("reason")
        if reason:
            click.echo(f"  {reason}")
        for released in record.get("released_claims", []):
            paths = ", ".join(released.get("paths", []))
            ticket = released.get("ticket") or ""
            suffix = f" [{ticket}]" if ticket else ""
            click.echo(f"  released claim{suffix}: {paths}")


# --- Burn-rate measurement (SPEC §2.6, D-38/D-39) ---


@cli.command("budget")
def budget_report() -> None:
    """Current burn rate and week-end projection from real Claude Code
    transcripts — reporting only (D-38): no Worker-count recommendation,
    no path back into concurrency anywhere in this command. Also shown in
    `rite status`.

    This is your WHOLE MACHINE's usage across every project, not just
    this one — Anthropic's weekly quota is account-wide, and there is no
    local way to attribute usage back to one project (SPEC §2.6.1). For
    the same reason it reports no percentage: `budget.weekly_token_budget`
    is one project's target, and these figures are not one project's
    usage, so the two cannot be compared.

    Examples:
      rite budget
    """
    from datetime import UTC, datetime

    from rite_ai.budget import (
        compute_burn_rate,
        current_week_start,
        format_burn_rate,
        read_usage_since,
    )

    root, config = _load_config_for_write()
    tz_name = config.schedule.timezone or "UTC"
    now = datetime.now(UTC)
    week_start = current_week_start(now, tz_name, config.budget.week_start_day)
    if week_start is None:
        click.echo(
            f"cannot resolve week start — check schedule.timezone ({tz_name!r}) "
            f"and budget.week_start_day ({config.budget.week_start_day!r})",
            err=True,
        )
        raise SystemExit(1)

    samples = read_usage_since(week_start)
    report = compute_burn_rate(
        samples, week_start, now, config.budget.weekly_token_budget
    )
    click.echo("scope: this MACHINE, all projects — not just this one")
    click.echo(f"since: {report.week_start:%Y-%m-%d %H:%M %Z}")
    for line in format_burn_rate(report):
        click.echo(line)


# --- Project spec pointers (SPEC §9.13, D-52) ---


@cli.group()
def spec() -> None:
    """Where this project's design already lives.

    rite POINTS at a spec, it never copies one. Workers are given the paths
    and the citation convention and read what their ticket needs — a spec
    runs to thousands of lines, and inlining it into every Worker's context
    on every job spends the quota this tool exists to make last overnight.

    `rite init` proposes what it finds. These commands are for changing it
    afterwards; `rite doctor` lists what is configured and checks the paths
    still resolve."""


@spec.command("add")
@click.argument("path")
def spec_add(path: str) -> None:
    """Point Workers at PATH — a file or a directory, repo-relative.

    Examples:
      rite spec add SPEC.md
      rite spec add docs/adr/
    """
    from rite_ai.cli.init.scaffold import write_config

    root, config = _load_config_for_write()
    cleaned = path.strip()
    if not cleaned:
        click.echo("give a path to add", err=True)
        raise SystemExit(2)

    target = root / cleaned
    if not target.exists():
        # Refused, not warned. A pointer to nothing is the one state this
        # feature cannot survive: the Worker is told where the design is
        # and finds nothing there.
        click.echo(f"no such path in this project: {cleaned}", err=True)
        raise SystemExit(1)
    if target.is_dir() and not cleaned.endswith("/"):
        cleaned += "/"

    added = cleaned not in config.spec.paths
    if added:
        config.spec.paths.append(cleaned)
    convention_before = config.spec.convention
    if not config.spec.convention:
        # A spec `/spec` just wrote may hold a single decision row. This is
        # the moment there is something for detection to read, and without a
        # convention line the register exists and nothing cites it.
        from rite_ai.cli.init.detect import detect_decision_convention

        config.spec.convention = detect_decision_convention(root, config.spec.paths)
    if added or config.spec.convention != convention_before:
        write_config(root / ".rite", config)
    click.echo(f"added {cleaned}" if added else f"already pointed at {cleaned}")
    if config.spec.convention:
        click.echo(f"  convention: {config.spec.convention}")
    # Also when nothing was added: a CLAUDE.md generated before its spec
    # section had markers is brought up to date by running this again.
    _report_spec_refresh(root, config)


@spec.command("remove")
@click.argument("path")
def spec_remove(path: str) -> None:
    """Stop pointing Workers at PATH.

    Examples:
      rite spec remove docs/adr/
    """
    from rite_ai.cli.init.scaffold import write_config

    root, config = _load_config_for_write()
    cleaned = path.strip()
    candidates = {cleaned, cleaned.rstrip("/"), cleaned.rstrip("/") + "/"}
    match = next((p for p in config.spec.paths if p in candidates), None)
    if match is None:
        click.echo(f"not pointed at {cleaned}", err=True)
        if config.spec.paths:
            click.echo("  configured: " + ", ".join(config.spec.paths), err=True)
        raise SystemExit(1)
    config.spec.paths.remove(match)
    if not config.spec.paths:
        config.spec.convention = ""
    elif f"`{match}`" in config.spec.convention:
        # The convention names the register's file; it cannot name one that
        # is no longer registered.
        from rite_ai.cli.init.detect import detect_decision_convention

        config.spec.convention = detect_decision_convention(root, config.spec.paths)
    write_config(root / ".rite", config)
    click.echo(f"removed {match}")
    _report_spec_refresh(root, config)


def _report_spec_refresh(root: Path, config) -> None:
    """Rewrite the spec section of every generated `CLAUDE.md`, and say which.

    `rite init` was the only writer of the Owner's `CLAUDE.md`, so a spec
    registered afterwards never reached the Owner, and a Worker created
    before it kept whatever init had found."""
    from rite_ai.project_spec import refresh_spec_sections

    result = refresh_spec_sections(root, config)
    for rel in result.updated:
        click.echo(f"  updated {rel}")
    for note in result.skipped:
        click.echo(f"  not updated: {note}")


# --- The spec digest: one derived file per spec unit ---


def _spec_root_and_config(missing_exit: int = 1):
    """The project root and its config, or exit saying the spec is not set up.

    `missing_exit` is 3 for the gate: a gate that could not run must not share
    an exit code with a gate that ran and found nothing wrong."""
    root, config = _load_config_for_write()
    if not config.spec.paths:
        click.echo("no spec registered — run `rite spec add <path>` first", err=True)
        raise SystemExit(missing_exit)
    return root, config


def _parse_spec(
    root: Path,
    config,
    missing_exit: int = 1,
    echo_problems: bool = True,
    required: bool = True,
    worker_side: bool = False,
):
    """Every registered spec file, parsed into units.

    `echo_problems` is off for `rite spec index`, which prints the same list
    under the verdict where it belongs — said once, after the numbers it
    explains, rather than twice on either side of them."""
    from rite_ai.spec.units import parse_paths

    parsed = parse_paths(root, config.spec.paths, config.spec.extra_units)
    if not parsed.files:
        click.echo(
            "the registered spec paths hold no markdown file: "
            + ", ".join(config.spec.paths),
            err=True,
        )
        # Measured on a real sandbox: on the seatbelt backend the project root
        # is readable and this all works, but rite mounts only `.rite/` — a
        # backend that shows just its mounts leaves the spec unreachable while
        # `.rite/` is right there, which reads as "the spec is missing" rather
        # than "you cannot see it from in here".
        if os.environ.get("RITE_PROJECT_ROOT") and not os.access(root, os.R_OK):
            click.echo(
                f"  {root} is not readable from here. Inside a sandbox the "
                "project root may not be mounted, and the spec lives there — "
                "this is not the spec being gone.",
                err=True,
            )
        if not required:
            return None
        raise SystemExit(missing_exit)
    # A registered file that cannot be read stops everything, even when other
    # files parsed. Every command downstream states a CONCLUSION about the
    # spec's content — "no unit '5.3'", "covers 5.3, which the spec does not
    # have", "it has no headings", "the digest does not match the spec" — and
    # each of those was false on a spec nobody had read: a permissions blip
    # made `rite spec index` refuse the digest outright. The raw cause was
    # printed as a note underneath, which is no help to a script and little to
    # a session that reads the verdict. Partial is refused for the same reason
    # it is refused in the config parser: a silent partial read is worse than a
    # hard failure.
    unreadable = [p for p in parsed.problems if "cannot be read" in p]
    if unreadable:
        click.echo("the spec could not be read:", err=True)
        for problem in unreadable:
            click.echo(f"  {problem}", err=True)
        click.echo(
            "  nothing below this can be judged from here — this says nothing "
            "about whether the spec, or the digest of it, is sound.",
            err=True,
        )
        # Which commands belong where. `slice` and `show` are what a Worker
        # runs and both say more than this on their own; the rest read the
        # whole spec by nature and belong where it lives. A sandboxed Worker
        # mounts `.rite/` and often not the project root, so "run it again
        # from here" is advice that cannot work.
        if not worker_side:
            click.echo(
                "  this command reads the spec, so it runs where the spec is: "
                "the project itself. `/spec-digest` and `rite spec "
                "index|status|verify|stamp` are not Worker-side commands.",
                err=True,
            )
        if not required:
            return None
        raise SystemExit(missing_exit)
    if echo_problems:
        for problem in parsed.problems:
            click.echo(f"  {problem}", err=True)
    return parsed


def _spec_graph(parsed, config):
    from rite_ai.spec.graph import build_graph, classify

    graph = build_graph(parsed)
    return graph, classify(graph, pin_count=config.spec.pin_count)


@spec.command("index")
def spec_index() -> None:
    """Inventory the spec's units, and say whether it is worth digesting.

    Writes `.rite/spec/index.json` — every unit, where it starts and ends,
    and a hash of its text — then reports what a Worker would load for one
    unit. A spec small or dense enough that a slice is most of it is
    refused: reading it whole is cheaper than maintaining a digest of it.

    Examples:
      rite spec index
    """
    from rite_ai.spec.index_file import from_parsed, write_index
    from rite_ai.spec.report import decomposition_report
    from rite_ai.spec.report import render as render_report

    root, config = _spec_root_and_config()
    parsed = _parse_spec(root, config, echo_problems=False)
    report = decomposition_report(
        parsed,
        refuse_above=config.spec.refuse_above,
        pin_count=config.spec.pin_count,
        slice_depth=config.spec.slice_depth,
    )
    click.echo(render_report(report))
    if not report.decomposes:
        # Nothing is written. An index left behind after a refusal invites a
        # digest of a spec that was just refused, and every later command would
        # read it as a project that had chosen to have one.
        raise SystemExit(1)
    path = write_index(root, from_parsed(parsed))
    click.echo(f"wrote {path.relative_to(root)} — {len(parsed.units)} unit(s)")


@spec.command("status")
def spec_status() -> None:
    """What the digest covers, what has drifted, and whether slices are enough.

    Reports four things that are invisible otherwise: units with no derived
    file yet, derived files whose source has changed since they were
    stamped, files edited by hand, and how often a Worker had to fall back
    to the whole spec anyway.

    A report, not a gate: it exits 0 whatever it finds. `rite spec verify` is
    the one to wire into a script.

    Examples:
      rite spec status
    """
    from rite_ai.spec.digest_files import digest_status, unit_filename, units_dir
    from rite_ai.spec.index_file import ABSENT, UNREADABLE, from_parsed, read_index
    from rite_ai.spec.report import SPARSE_ABOVE, decomposition_report
    from rite_ai.spec.telemetry import FALLBACKS_ONLY, insufficiency_rate

    root, config = _spec_root_and_config()
    parsed = _parse_spec(root, config)
    units = {u.id: u for u in parsed.units}
    _, kinds = _spec_graph(parsed, config)

    read = read_index(root)
    if read.status == UNREADABLE:
        click.echo(f"index: unreadable — {read.error}")
        click.echo("  run `rite spec index` to write it again")
    elif read.status == ABSENT:
        click.echo("index: not written yet — run `rite spec index`")
    elif read.index != from_parsed(parsed):
        click.echo("index: out of date with the spec — run `rite spec index`")
    else:
        click.echo(f"index: current — {len(units)} unit(s)")

    report = decomposition_report(
        parsed,
        refuse_above=config.spec.refuse_above,
        pin_count=config.spec.pin_count,
        slice_depth=config.spec.slice_depth,
    )
    if not report.decomposes:
        # Repeated here because a project that ran `rite spec index` once, read
        # the refusal and moved on has no other reminder: everything below
        # would otherwise read as ordinary work waiting to be done.
        click.echo(f"this spec should not be digested — {report.reason}")
    elif report.unlinked_share >= SPARSE_ABOVE:
        click.echo(
            f"{report.unlinked_share:.0%} of this spec's units cite nothing, so "
            "its slices are small for a reason that is not coverage — the rate "
            "below is the evidence that matters here"
        )

    status = digest_status(root, units, kinds)
    click.echo(f"digest: {status.files} derived file(s)")
    for label, entries in (
        ("no derived file yet", status.new),
        ("stale (the spec changed under them)", status.stale),
        ("cover units the spec no longer has", status.removed),
        ("edited by hand since stamping", status.tampered),
        ("never stamped", status.unstamped),
        ("not readable as unit files", status.unreadable),
        ("covered by more than one derived file", status.overlapping),
    ):
        if entries:
            click.echo(f"  {label}: {len(entries)}")
            for entry in sorted(entries)[:20]:
                # A unit id is not its file name (`2.4/promotion` cannot be a
                # path), so the thing to write is spelled out rather than left
                # for the reader to derive.
                if entries is status.new:
                    where = (units_dir(root) / unit_filename(entry)).relative_to(root)
                    click.echo(f"    {entry} → {where}")
                else:
                    click.echo(f"    {entry}")
            if len(entries) > 20:
                click.echo(f"    … and {len(entries) - 20} more")
    if status.clean:
        click.echo("  every unit covered, stamped and current")
    rate = insufficiency_rate(root)
    click.echo(rate.describe())
    if rate.status == FALLBACKS_ONLY:
        # Every fallback and not one retrieval. Depth and pinning are about
        # slices being too SMALL; here none was taken, so neither lever can
        # move this number. Measured today: a Worker that cannot reach the
        # spec produces exactly this signature — `rite spec slice` refuses,
        # `rite handover write --spec-fallback` still records, because the
        # first needs the project root and the second needs only `.rite/`.
        click.echo(
            "  nothing was sliced at all, so this is not the slices being too "
            "small. Check that a Worker can run `rite spec slice` where it "
            "works: inside a sandbox the project root holding the spec may not "
            "be mounted, while `.rite/` is — which lets the fallback record "
            "and the retrieval fail."
        )
    elif rate.fallbacks:
        # No threshold, because none has been measured. What IS measured is
        # which lever works on a spec of this shape — and the first version of
        # this advice named depth 2 unconditionally, which is useless on
        # exactly the specs most likely to be falling back.
        if report.unlinked_share >= SPARSE_ABOVE:
            click.echo(
                "  raising `spec.slice_depth` will not help here: most units "
                "cite nothing, so there is no second reference to follow. "
                "Measured on three specs of this shape, depth 2 left the median "
                "slice unchanged. The levers are a larger `spec.pin_count`, "
                "which loads more shared context into every slice, and writing "
                "the missing references into the spec itself."
            )
        else:
            click.echo(
                "  if that is too high: `spec.slice_depth: 2` follows "
                "references one step further — on a spec with this shape it "
                "roughly doubled the p90 slice when measured (8.9% to 14.9% on "
                "rite's own). A larger `spec.pin_count` loads more shared "
                "context into every slice, including the ones that did not "
                "need it."
            )


@spec.command("slice")
@click.argument("unit")
@click.option(
    "-w",
    "--worker",
    default="",
    help="Which worker is reading it, recorded with the retrieval.",
)
@click.option(
    "--depth",
    type=int,
    default=None,
    help="How far to follow references. Overrides spec.slice_depth in config.yaml.",
)
def spec_slice(unit: str, worker: str, depth: int | None) -> None:
    """Print what a Worker needs to read for UNIT, and nothing more.

    UNIT is a section number, a slug path or a decision id: `5.3`,
    `scope/non-goals`, `D-12`. The output is the unit, what it references,
    and the sections everything depends on — the hubs — which is a fraction
    of the spec. Each run is counted, so that `rite spec status` can say
    how often a slice was not enough.

    Examples:
      rite spec slice 5.3
      rite spec slice D-12 --worker alpha
    """
    from rite_ai.spec.digest_files import unit_filename, units_dir
    from rite_ai.spec.slice import NotATarget, compute_slice
    from rite_ai.spec.telemetry import record_retrieval

    root, config = _spec_root_and_config()
    parsed = _parse_spec(root, config, required=False, worker_side=True)
    if parsed is None:
        # The refusal is honest, and on its own it is a dead end. A Worker
        # standing where the spec cannot be read still has two moves, and they
        # are the two halves that must not be confused: the derived text, which
        # lives under `.rite/` and is readable from inside a sandbox, and
        # recording that the whole spec had to be read instead — which is the
        # only thing that makes this visible to anybody afterwards.
        if (units_dir(root) / unit_filename(unit)).exists():
            click.echo(
                f"  the derived text for {unit} is readable from here: "
                f"rite spec show {unit}",
                err=True,
            )
        click.echo(
            "  and if you read the whole spec instead, record it: "
            f"rite handover write --spec-fallback {unit}",
            err=True,
        )
        raise SystemExit(1)
    graph, kinds = _spec_graph(parsed, config)
    try:
        computed = compute_slice(
            graph,
            kinds,
            unit,
            parsed.total_lines,
            depth=config.spec.slice_depth if depth is None else depth,
        )
    except NotATarget as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    except ValueError as e:
        # A depth the domain refuses is a usage error, and a traceback would
        # bury the sentence that says which depths are allowed.
        click.echo(str(e), err=True)
        raise SystemExit(2) from e

    # A section's range covers the subsections inside it, and a slice can hold
    # both. Printed once: a Worker reading the same paragraph twice under two
    # headings has no way to tell it is one paragraph.
    shown: set[tuple[str, int]] = set()
    for unit_id in computed.units + computed.pinned:
        found = graph.units.get(unit_id)
        if found is None:
            continue
        lines = parsed.lines.get(found.source, [])
        wanted = [
            n
            for n in range(found.start, found.end + 1)
            if (found.source, n) not in shown
        ]
        if not wanted:
            continue
        shown.update((found.source, n) for n in wanted)
        # Printed as the runs actually included, each labelled with its own
        # range, with the gaps marked. A decision register whose rows are
        # separate units is printed after those rows, so joining what is left
        # under the register's full range would show a complete-looking table
        # with a row missing from the middle — which a Worker would read as
        # "that decision does not exist".
        runs: list[list[int]] = []
        for n in wanted:
            if runs and n == runs[-1][-1] + 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        spans = ", ".join(f"{r[0]}" if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)
        click.echo(f"# {unit_id} ({found.source}:{spans})")
        for i, run in enumerate(runs):
            if i:
                click.echo("# … part of this unit is printed elsewhere …")
            click.echo("\n".join(lines[n - 1] for n in run).rstrip())
        if wanted[0] != found.start or wanted[-1] != found.end or len(runs) > 1:
            click.echo(
                f"# (this is {unit_id}, lines {found.start}-{found.end}; the "
                "rest of it is printed elsewhere in this slice)"
            )
        click.echo("")
    record_retrieval(root, unit, worker=worker, slice_ratio=computed.ratio)
    # On stderr: the slice itself is what a Worker pipes or reads, and a
    # measurement inside it would read as part of the spec.
    click.echo(
        f"{computed.lines} of {computed.total_lines} line(s), "
        f"{computed.ratio:.1%} of the spec "
        f"({len(computed.units)} unit(s), {len(computed.pinned)} pinned)",
        err=True,
    )
    click.echo(
        "if this was not enough, record it: "
        f"rite handover write --spec-fallback {unit}",
        err=True,
    )


@spec.command("show")
@click.argument("unit")
@click.option(
    "-w",
    "--worker",
    default="",
    help="Which worker is reading it, recorded with the retrieval.",
)
def spec_show(unit: str, worker: str) -> None:
    """Print the derived text for UNIT, and where in the spec it came from.

    This is what `/spec-digest` wrote and reviewed: the section said in fewer
    words, with its citations intact. `rite spec slice` is the other half —
    the source text of the unit and what it references.

    The exit code says whether the text can be trusted: 0 when it matches the
    spec, 1 when it is stale, hand-edited or never stamped. The text is
    printed either way, with what is wrong with it on stderr, because a
    Worker that has already been handed nothing cannot judge anything.

    Examples:
      rite spec show 5.3
      rite spec show D-12 --worker alpha
    """
    from rite_ai.spec.digest_files import (
        UNITS_DIR,
        covers_hash,
        file_hash,
        read_unit_file,
        unit_filename,
        units_dir,
    )
    from rite_ai.spec.graph import build_graph
    from rite_ai.spec.telemetry import record_retrieval

    root, config = _spec_root_and_config()
    # `required=False`: the derived text lives under `.rite/`, which a sandbox
    # mounts, while the spec lives at the project root, which it may not. A
    # Worker that can read the unit should be handed it even when the source
    # cannot be checked — saying so, rather than guessing at its freshness.
    parsed = _parse_spec(root, config, required=False, worker_side=True)
    units = {u.id: u for u in parsed.units} if parsed else {}
    # An unreadable spec file is FOUND but yields nothing — `parse_paths`
    # records "cannot be read" and returns no units — so "no files" is not the
    # test. No units at all is: a spec that exists says something.
    readable = parsed is not None and bool(units)

    path = units_dir(root) / unit_filename(unit)
    if not path.exists():
        click.echo(
            f"{unit}: nothing derived for it yet — {UNITS_DIR} has no file for "
            "this unit. Run `/spec-digest`, or read the source with "
            f"`rite spec slice {unit}`.",
            err=True,
        )
        raise SystemExit(1)
    read = read_unit_file(path)
    if isinstance(read, str):
        click.echo(read, err=True)
        raise SystemExit(1)

    click.echo(read.body.strip())
    where = read.source or "the spec"
    if read.source_lines:
        where += f":{read.source_lines[0]}-{read.source_lines[1]}"
    click.echo(f"\n# derived from {where}; covers {', '.join(read.covers)}")

    # What a unit sits inside is where its qualifiers live, and a unit file
    # cannot carry them: measured on a trial digest of this spec, a subsection
    # read as current behaviour because the ⚠ saying it was unshipped sat nine
    # lines above the cut, in the parent's own text. Naming the parent is what
    # a Worker needs to go and look.
    graph = build_graph(parsed) if readable else None
    parents = (
        [u.parent for c in read.covers if (u := units.get(c)) and u.parent]
        if graph
        else []
    )
    for parent in dict.fromkeys(parents):
        # Not the document title: it holds nothing, which is why the graph
        # excludes it from ancestors too.
        if parent in graph.units and graph.units[parent].level > 1:
            click.echo(
                f"# it sits inside {parent} — anything qualifying it may be "
                f"there: rite spec show {parent}"
            )

    if not read.source_sha or not read.body_sha:
        problem = "never stamped, so nothing says which spec it was written from"
    elif file_hash(read) != read.body_sha:
        problem = "edited by hand since it was stamped — it is not what was reviewed"
    elif not readable:
        # NOT "stale". An unreadable spec parses to nothing, so every hash
        # mismatches, and "the spec has changed since it was written" would be
        # a lie in the worst direction: it sends a Worker to re-digest — which
        # it cannot do from inside a sandbox — over text that may be current.
        problem = (
            "not checkable from here: the spec could not be read, so whether "
            "this still matches it is unknown. It has not been shown to be stale"
        )
    elif covers_hash(units, read.covers) != read.source_sha:
        problem = "stale: the spec has changed since it was written"
    else:
        problem = ""
    # Counted like a slice: a Worker reading a derived unit is a retrieval, and
    # a fallback after one means the same thing either way. Which is why the
    # reminder is here too: `show` counted the denominator and never asked for
    # the numerator, so the one path the digest exists to serve could only ever
    # push the measured rate DOWN.
    record_retrieval(root, unit, worker=worker, slice_ratio=None)
    click.echo(
        "if this was not enough, record it: "
        f"rite handover write --spec-fallback {unit}",
        err=True,
    )
    if problem:
        remedy = (
            "Check it where the spec is readable before relying on it."
            if not readable
            else "Re-digest it before relying on it."
        )
        click.echo(f"⚠ {unit} is {problem}. {remedy}", err=True)
        raise SystemExit(1)


@spec.command("stamp")
@click.argument("units", nargs=-1)
@click.option(
    "--all",
    "all_",
    is_flag=True,
    help="Stamp every file that has never been stamped. Refuses stale or "
    "hand-edited ones: those are re-digested first, then stamped by name.",
)
def spec_stamp(units: tuple[str, ...], all_: bool) -> None:
    """Record, on each derived file, the spec it was written from.

    Run after writing a derived unit's text. The stamp is what later tells
    a stale file from a current one and a hand-edited one from a generated
    one, so it is a deliberate step: stamping on every index run would
    bless a spec change nobody had read and a hand edit nobody had made.

    Examples:
      rite spec stamp 5.3
      rite spec stamp --all
    """
    from rite_ai.spec.digest_files import (
        UnitFile,
        digest_status,
        read_unit_file,
        read_unit_files,
        stamp,
        unit_filename,
        units_dir,
    )

    if bool(units) == all_:
        click.echo("name the unit(s) to stamp, or pass --all", err=True)
        raise SystemExit(2)
    root, config = _spec_root_and_config()
    parsed = _parse_spec(root, config)
    by_id = {u.id: u for u in parsed.units}

    targets: list[UnitFile] = []
    failed = False
    if all_:
        targets, problems = read_unit_files(root)
        for problem in problems:
            click.echo(problem, err=True)
            failed = True
        # `--all` stamps what has never been stamped, and NOTHING ELSE. Stamping
        # a stale or hand-edited file is exactly the automatic restamp this
        # design refuses: it would record the current spec against text written
        # from an older one, and the drift would be gone with no trace. Those
        # files are re-digested and then stamped BY NAME, which is a deliberate
        # act by someone who has read the new source.
        _, kinds = _spec_graph(parsed, config)
        state = digest_status(root, by_id, kinds)
        drifted = set(state.stale) | set(state.tampered)
        skipped = [t for t in targets if t.path.name in drifted]
        targets = [t for t in targets if t.path.name not in drifted]
        for target in skipped:
            click.echo(
                f"not stamped: {target.path.name} — it is stale or edited by "
                "hand. Re-digest that unit, then stamp it by name; stamping it "
                "as it stands would record it as matching a spec it does not.",
                err=True,
            )
            failed = True
    else:
        for unit_id in units:
            path = units_dir(root) / unit_filename(unit_id)
            if not path.exists():
                click.echo(
                    f"{unit_id}: no derived file at "
                    f"{path.relative_to(root)} — write it first",
                    err=True,
                )
                failed = True
                continue
            read = read_unit_file(path)
            if isinstance(read, str):
                click.echo(read, err=True)
                failed = True
                continue
            targets.append(read)

    for target in targets:
        result = stamp(target, by_id)
        if isinstance(result, str):
            click.echo(result, err=True)
            failed = True
        else:
            click.echo(f"stamped {result.path.relative_to(root)} ({result.id})")
    if failed:
        raise SystemExit(1)


@spec.command("verify")
@click.option(
    "--strict",
    is_flag=True,
    help="Also refuse two derived files covering the same source unit.",
)
def spec_verify(strict: bool) -> None:
    """Check the digest still matches the spec. Exit 0 only if it does.

    Every unit covered, nothing covering a unit the spec no longer has,
    nothing stale, nothing hand-edited, nothing unstamped. It does not read
    for meaning — that is what the review rounds in `/spec-digest` are for.

    Exit codes: 0 the digest is current, 1 something has drifted, 3 the
    check could not run at all.

    Examples:
      rite spec verify
      rite spec verify --strict
    """
    from rite_ai.spec.digest_files import UNITS_DIR, digest_status
    from rite_ai.spec.graph import INDEX
    from rite_ai.spec.index_file import PRESENT, from_parsed, read_index

    root, config = _spec_root_and_config(missing_exit=3)
    parsed = _parse_spec(root, config, missing_exit=3)
    units = {u.id: u for u in parsed.units}
    _, kinds = _spec_graph(parsed, config)
    status = digest_status(root, units, kinds)

    failures: list[tuple[str, list[str], str]] = [
        ("not readable as unit files", status.unreadable, "fix or delete them"),
        (
            "cover units the spec no longer has",
            status.removed,
            "delete them, or point `covers` at what replaced them",
        ),
        ("edited by hand since stamping", status.tampered, "re-digest those units"),
        ("stale — the spec changed under them", status.stale, "re-digest those units"),
        ("never stamped", status.unstamped, "run `rite spec stamp`"),
        ("no derived file yet", status.new, "run `/spec-digest`"),
    ]
    if strict:
        failures.append(
            (
                "covered by more than one derived file",
                status.overlapping,
                "merge them, or narrow `covers` so each unit has one",
            )
        )

    read = read_index(root)
    index_problem = ""
    if read.status != PRESENT:
        index_problem = f"the unit index is {read.status}"
    elif read.index != from_parsed(parsed):
        index_problem = "the unit index no longer matches the spec"

    if not status.files:
        # Not the same failure as a digest that has drifted, and saying so
        # matters: one is re-digesting a few units, the other is starting.
        click.echo(
            f"nothing is digested — no unit files under {UNITS_DIR}, "
            f"and the spec has {len(units)} unit(s). Run `/spec-digest`.",
            err=True,
        )
        raise SystemExit(1)

    # Incomplete is not drifted. A first digest of a large spec is written over
    # several passes — 189 units for rite's own — and calling that "a Worker
    # would be reading something the spec no longer says" is both false and
    # unactionable: nothing is wrong with what HAS been written.
    only_uncovered = (
        status.new
        and not index_problem
        and not any(
            entries for label, entries, _ in failures if entries is not status.new
        )
    )
    if only_uncovered:
        # Against the units a digest is owed, not every unit: index sections are
        # never owed a file, so counting them would report progress no session
        # made.
        owed = sum(1 for uid in units if kinds.get(uid) != INDEX)
        covered = owed - len(status.new)
        click.echo(
            f"incomplete: {covered} of {owed} unit(s) digested, "
            f"{len(status.new)} still to write — nothing that is written has "
            "drifted. Run `/spec-digest` again to continue.",
            err=True,
        )
        for entry in sorted(status.new)[:20]:
            click.echo(f"  {entry}", err=True)
        if len(status.new) > 20:
            click.echo(f"  … and {len(status.new) - 20} more", err=True)
        raise SystemExit(1)

    total = sum(len(entries) for _, entries, _ in failures)
    for label, entries, remedy in failures:
        if not entries:
            continue
        click.echo(f"{len(entries)} {label} — {remedy}:", err=True)
        for entry in sorted(entries)[:20]:
            click.echo(f"  {entry}", err=True)
        if len(entries) > 20:
            click.echo(f"  … and {len(entries) - 20} more", err=True)
    if index_problem:
        click.echo(f"{index_problem} — run `rite spec index`", err=True)

    if total or index_problem:
        click.echo(
            "✗ the digest does not match the spec — a Worker reading it would "
            "be reading something the spec no longer says",
            err=True,
        )
        raise SystemExit(1)
    click.echo(
        f"✓ the digest matches the spec — {status.files} derived file(s) "
        f"covering {len(units)} unit(s)"
    )


# --- Worker sandboxing (SPEC §5.3, D-26, D-30, D-31) ---


@cli.group()
def sandbox() -> None:
    """Process-isolate a Worker's session with yoloAI (https://yoloai.dev,
    installed separately). These commands drive a sandbox directly and
    work whenever yoloai does.

    `sandbox.enabled` in config.yaml is a separate statement — that this
    project runs its Workers sandboxed as a matter of course. It governs
    setup rather than these commands: `rite doctor` verifies a sandbox can
    actually start and treats a failure as a problem instead of a note. A
    project that leaves it false can still run everything here.

    It says nothing about credentials. Per-Worker token scoping is asked
    for with `rite add worker --scoped-token`; otherwise Workers share the
    credentials the project already holds."""


@sandbox.command("start")
@click.argument("worker")
@click.option(
    "--agent-arg",
    "agent_args",
    multiple=True,
    help="Argument to pass to the agent inside the sandbox (repeatable).",
)
@click.option(
    "--allow-dirty",
    is_flag=True,
    default=False,
    help="Start on the workspace as it is: skip preparing it, and start "
    "even with uncommitted changes (they become visible to the agent).",
)
@click.option(
    "--ticket",
    default=None,
    help="Ticket for the Worker to work, sent as its opening prompt "
    '("Work ticket <TICKET>.").',
)
@click.option(
    "--prompt",
    "prompt_text",
    default=None,
    help="Opening prompt for the Worker, sent verbatim. For work that is not "
    "a ticket on your board; use instead of --ticket.",
)
def sandbox_start(
    worker: str,
    agent_args: tuple[str, ...],
    allow_dirty: bool,
    ticket: str | None,
    prompt_text: str | None,
) -> None:
    """Launch WORKER's session inside a fresh sandbox. If a token was
    provisioned for this Worker (`rite add worker`'s sandbox step, or
    `rite credential set sandbox_token_<worker>`), delivers it via
    `--env` (D-31) — never a file, never a CLI argument.

    Give it its work when you start it: nothing in rite can type into a
    sandbox afterwards. The sandbox name is printed on start; `yoloai
    attach <name>` opens the session to watch it or step in.

    Before the first one, give sandboxes a Claude login: `claude
    setup-token`, then `rite credential set claude`.

    Examples:
      rite sandbox start alpha --ticket ABC-12
      rite sandbox start alpha --prompt "Add a CSV export to the invoices page."
      rite sandbox start alpha --allow-dirty --ticket ABC-12
    """
    if ticket is not None and prompt_text is not None:
        raise click.UsageError("give --ticket or --prompt, not both")
    if ticket is not None and not ticket.strip():
        raise click.UsageError("--ticket needs a ticket ID")
    prompt = f"Work ticket {ticket}." if ticket is not None else prompt_text
    from rite_ai.credentials.store import worker_environment
    from rite_ai.sandbox import (
        GLOBAL_TOKEN_CREDENTIAL,
        resolve_worker_token,
        start_worker,
    )

    root, config = _load_config_for_write()

    # Prepare first, outside the sandbox. The sandbox works on a copy of the
    # workspace and cannot run `rite prepare` itself, so a Worker started on
    # an unprepared checkout would work on stale code and nothing would say.
    from rite_ai.workspace import prepare_workspace

    worker_dir, modules = _worker_modules_or_exit(root, worker)
    # Instructions are written once, by `rite add worker`. A Worker created
    # before they said to push would work in a copy that is discarded with
    # the sandbox and never be told its work has to leave it.
    claude_md = worker_dir / "CLAUDE.md"
    if claude_md.is_file() and "## Your ticket" not in claude_md.read_text(
        errors="replace"
    ):
        click.echo(
            f"warning: workers/{worker}/CLAUDE.md was written before sandboxed "
            "Workers were supported: it does not tell the Worker to push, send "
            "heartbeats or read its ticket, so its work may never leave the "
            f"sandbox. Recreate the Worker for current instructions: "
            f"`rite remove worker {worker}`, then `rite add worker {worker}`.",
            err=True,
        )
    if allow_dirty:
        click.echo(
            f"not preparing workers/{worker}/: --allow-dirty starts the sandbox on "
            "the checkout as it is, uncommitted changes included"
        )
    else:
        prep = prepare_workspace(worker_dir, modules, root)
        click.echo(prep.summary())
        if not prep.ok:
            blocked = ", ".join(m.module for m in prep.blocking)
            click.echo(
                f"not starting '{worker}': its workspace is not ready ({blocked}). "
                f"Resolve what is listed above in workers/{worker}/ — commit and "
                "push, or stash, any uncommitted changes — then run "
                f"`rite sandbox start {worker}` again. `--allow-dirty` starts it "
                "on the checkout as it is instead.",
                err=True,
            )
            raise SystemExit(1)

    token, tier = resolve_worker_token(worker, config.credentials)
    # Every credential this project holds, not just the git token (§5.3.4).
    env = worker_environment(config.credentials, worker_token=token)
    if tier == "global":
        # Loud, every time — but ONLY for a token belonging to the whole
        # machine. It used to fire for this project's own `github_token`
        # too, which is the configuration §5.3.4 calls correct, and the
        # remedy it printed was to provision a per-Worker token that the
        # same section retired. See `resolve_worker_token`.
        #
        # "not scoped to this worker's modules" is also gone: §5.3.4 says
        # no token is, by design, and a warning that treats the intended
        # model as the fault teaches the reader to ignore it.
        click.echo(
            f"warning: '{GLOBAL_TOKEN_CREDENTIAL}' is set for this whole "
            f"machine, not for this project, so this Worker is being given a "
            f"token that reaches beyond the project's repos — which is the "
            f"one bound §5.3.3 keeps and the sandbox does not (SPEC §5.3.2). "
            # `migrate`, which exists and copies the machine-wide entry
            # into this project's namespace. The first cut said `adopt`,
            # which is not a command — `rite credential adopt` exits 2 —
            # and `rite credential list` already prints `migrate` for this
            # same condition, so the two surfaces that diagnose one state
            # disagreed, and the broken one was the one that fires while
            # someone is starting work.
            f"Give the project its own: `rite credential migrate "
            f"{GLOBAL_TOKEN_CREDENTIAL}` moves the one you have, or `rite "
            f"credential set {GLOBAL_TOKEN_CREDENTIAL}` from inside the "
            f"project sets a new one.",
            err=True,
        )
    result = start_worker(
        root,
        worker,
        config.sandbox,
        token=token,
        agent_args=list(agent_args) or None,
        env=env,
        allow_dirty=allow_dirty,
        prompt=prompt,
    )
    click.echo(result.message)
    if not result.ok:
        raise SystemExit(1)


@sandbox.command("stop")
@click.argument("worker")
def sandbox_stop(worker: str) -> None:
    """Stop WORKER's sandbox, preserving its state (yoloAI's own
    distinction from `destroy`).

    \b
    PRESERVED STATE CAN INCLUDE THE WORKER'S GITHUB TOKEN.
    rite delivers it with `--env` and nothing else (SPEC §5.3.3, D-31), and
    what the sandbox does with it afterwards is outside that guarantee — a
    dogfood session reported yoloAI 0.11.0 persisting it inside the sandbox,
    cleared by `destroy` and not by `stop`. That report is unverified here and
    its exact locations did not reproduce, so take the general reading rather
    than the specific one: `stop` keeps the sandbox's state, `destroy` removes
    it. Prefer `rite sandbox destroy` once the token is no longer wanted on
    this machine, and rotate it if a stopped sandbox has been sitting around.

    \b
    NOTE: the Worker's checkout inside is a copy, and `rite sandbox destroy`
    discards it with the sandbox. Stop warns, and destroy refuses without
    --force, while that copy holds uncommitted changes or commits on no
    remote, naming the module, branch and count.

    Examples:
      rite sandbox stop alpha
    """
    from rite_ai.sandbox import stop_worker

    result = stop_worker(worker, _find_project_root())
    click.echo(result.message)
    if not result.ok:
        raise SystemExit(1)


@sandbox.command("destroy")
@click.argument("worker")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Destroy even though the sandbox's copy holds work on no remote; "
    "that work is lost.",
)
def sandbox_destroy(worker: str, force: bool) -> None:
    """Stop and remove WORKER's sandbox entirely, with the Worker's copy of
    its checkout. Refuses while that copy holds uncommitted changes or
    commits on no remote, naming them, since destroying it loses them.

    Examples:
      rite sandbox destroy alpha
      rite sandbox destroy alpha --force
    """
    from rite_ai.sandbox import destroy_worker

    result = destroy_worker(worker, _find_project_root(), force=force)
    click.echo(result.message)
    if not result.ok:
        raise SystemExit(1)


def _injected_secret_values(root: Path | None, worker: str) -> list[str]:
    """Every credential value `rite sandbox start` would give WORKER, so the
    pane can be searched for them. Read on the host, where the keychain is
    readable; nothing is printed."""
    from rite_ai.config.parse import load_project
    from rite_ai.credentials.store import worker_environment
    from rite_ai.sandbox import resolve_worker_token

    credentials = None
    if root is not None:
        project = load_project(root)
        if not isinstance(project, list):
            credentials = project.config.credentials
    token, _tier = resolve_worker_token(worker, credentials)
    injected = worker_environment(credentials, worker_token=token)
    return [value for value in injected.values() if value]


@sandbox.command("pane")
@click.argument("worker")
@click.option("--ansi", is_flag=True, default=False, help="Keep colour/escape codes.")
def sandbox_pane(worker: str, ansi: bool) -> None:
    """Show what WORKER's sandboxed session currently has on screen.

    A sandboxed Worker does not appear in Claude Code's own session list,
    so it cannot be listed or messaged the way an ordinary session can —
    its screen is the only way to see what it is doing. Read-only; this
    types nothing into the session.

    Exits non-zero when the pane could not be captured, which is NOT the
    same as an idle worker — a broken yoloAI must not read as a quiet one.

    Examples:
      rite sandbox pane w1
      rite sandbox pane w1 --ansi | less -R
      watch -n 30 rite sandbox pane w1
    """
    from rite_ai.sandbox import worker_pane

    root = _find_project_root()
    capture = worker_pane(
        worker, ansi=ansi, root=root, secrets=_injected_secret_values(root, worker)
    )
    click.echo(capture.text, err=not capture.ok)
    if not capture.ok:
        raise SystemExit(1)


@sandbox.command("status")
@click.argument("worker")
def sandbox_status(worker: str) -> None:
    """Report WORKER's current sandbox status.

    Examples:
      rite sandbox status alpha
    """
    from rite_ai.sandbox import worker_sandbox_status

    status = worker_sandbox_status(worker, _find_project_root())
    click.echo(status.value)
    if not status.known:
        # The status could not be determined. Exiting 0 would report that
        # as an answer, which is how "yoloai is broken" came to look
        # exactly like "this worker has no sandbox".
        raise SystemExit(1)


# --- Multi-project registry (SPEC §8.9, D-34) ---


@cli.group()
def projects() -> None:
    """The Dispatch hub's project registry — additive; a single-project
    setup needs none of this."""


@projects.command("list")
def projects_list() -> None:
    """Show registered aliases, paths, and roles.

    Examples:
      rite projects list
    """
    from rite_ai.dispatch import default_dispatch_dir, load_registry

    registry = load_registry(default_dispatch_dir())
    if not registry.projects:
        click.echo("no projects registered — add one with `rite projects add`")
        return
    for alias, entry in registry.projects.items():
        click.echo(f"  {alias}: {entry.path} ({entry.role})")


@projects.command("add")
@click.argument("alias")
@click.argument("path", type=click.Path(exists=True))
@click.option("--role", default="manager", help="This machine's role for this project")
def projects_add(alias: str, path: str, role: str) -> None:
    """Register a project under an alias.

    Refuses an alias that collides with a Manager name declared by any
    registered project: `rite start <word>` matches Manager names before
    aliases, so such an alias would never resolve and would fail silently.
    Pick another alias — the Manager name is committed in that project's
    config and is not yours to change here.

    Warns, without refusing, if the aggregate scheduled load across all
    registered projects looks high.

    Examples:
      rite projects add acme ~/work/acme
    """
    from rite_ai.dispatch import (
        add_project,
        aggregate_load_warning,
        default_dispatch_dir,
    )
    from rite_ai.managers import name_collisions

    # BEFORE registering. `rite start <word>` tries Managers first, so an
    # alias that collides is unreachable by name from the moment it exists
    # — and this is the one point where the colliding name is the user's to
    # change at no cost. See `name_collisions` for why it cannot be a
    # refusal at `rite start` instead.
    clashes = _manager_names_across(default_dispatch_dir(), Path(path))
    for root, names in clashes.items():
        if name_collisions(names, [alias]):
            click.echo(
                f"refusing to register '{alias}': {root} declares a Manager "
                f"of that name.\n"
                f"  `rite start {alias}` starts that Manager — Manager names "
                f"are matched before aliases — so this alias would never "
                f"resolve, silently.\n"
                f"  Pick another alias. The Manager name is committed in "
                f"that project's config and is not yours to change here.",
                err=True,
            )
            raise SystemExit(1)

    result = add_project(default_dispatch_dir(), alias, Path(path), role=role)
    if not result.ok:
        click.echo(result.message, err=True)
        raise SystemExit(1)
    click.echo(result.message)

    warning = aggregate_load_warning(default_dispatch_dir())
    if warning:
        click.echo(f"warning: {warning}")


@projects.command("remove")
@click.argument("alias")
def projects_remove(alias: str) -> None:
    """Deregister an alias — does not touch the project's own .rite/.

    Examples:
      rite projects remove acme
    """
    from rite_ai.dispatch import default_dispatch_dir, remove_project

    result = remove_project(default_dispatch_dir(), alias)
    if not result.ok:
        click.echo(result.message, err=True)
        raise SystemExit(1)
    click.echo(result.message)


def _manager_names_across(dispatch_dir: Path, extra: Path | None = None) -> dict:
    """Every declared Manager name this machine can reach, by project path.

    Reads each REGISTERED project plus one not yet registered (the argument
    to `rite projects add`). A project whose config will not parse
    contributes nothing rather than raising: this runs inside `doctor`,
    which has its own check for unparseable config and must not be
    hijacked by it.
    """
    from rite_ai.dispatch import load_registry

    found: dict = {}
    roots = [Path(e.path) for e in load_registry(dispatch_dir).projects.values()]
    if extra is not None:
        roots.append(extra)
    for root in roots:
        names, _problems = _manager_roles(root)
        if names:
            found[root] = [role.name for role in names]
    return found


def _resolve_directory_or_alias(directory: str) -> Path:
    """`rite start <alias>` resolves via the registry first; a bare
    directory argument (or none, defaulting to `.`) works exactly as
    before, with no dependency on `~/.rite/dispatch/` existing at all.

    An argument that is neither is refused HERE, naming both things it
    could have been. It used to fall through to `Path(directory)`, so a
    mistyped alias became a relative path and `rite stop schd` from any
    directory answered "no .rite/ directory" — a message about a
    directory the user never mentioned, for a registry the user was
    plainly addressing."""
    from rite_ai.dispatch import default_dispatch_dir, load_registry, resolve_alias

    dispatch_dir = default_dispatch_dir()
    resolved = resolve_alias(dispatch_dir, directory)
    if resolved is not None:
        return resolved

    path = Path(directory)
    if path.exists():
        return path.resolve()

    aliases = sorted(load_registry(dispatch_dir).projects)
    known = (
        f"registered aliases: {', '.join(aliases)}"
        if aliases
        else "no projects are registered (`rite projects add`)"
    )
    click.echo(
        f"'{directory}' is neither an existing directory nor a registered "
        f"alias — {known}",
        err=True,
    )
    raise SystemExit(1)


# --- Lifecycle ---


def _manager_roles(root: Path) -> tuple[list, list[str]]:
    """This project's declared Managers, or none.

    Returns `(roles, problems)`. **Both halves matter and an earlier version
    had only the first.**

    ⚠ **The broad `except Exception` was narrowed and the bug came back one
    layer up, structurally rather than syntactically.** `load_project`
    returns `list[ParseError]` when any config file fails, and a draft
    collapsed that list to `[]` with no message — so an invalid engine gave
    `rite start planner` → "neither an existing directory nor a registered
    alias", while `rite doctor` reported the real cause. The docstring
    claimed the directory/alias path "already reports the error". It does
    not: it reports a different error, about a directory the user never
    mentioned.

    Discarding a parser's answer is the same defect as catching its
    exception. Narrowing the catch fixed the syntax and left the shape.
    """
    from rite_ai.config.parse import load_project

    project = load_project(root)
    if isinstance(project, list):
        return [], [str(getattr(e, "message", e)) for e in project]
    return list(project.config.coordination.manager_roles), []


def _a_manager_is_running(root: Path, roles: list) -> bool:
    """Is any declared Manager live right now? (D-80)

    What separates orientation from starting work is not the argument, it is
    whether a Manager is already running — observable, where the argument
    relies on the caller remembering which form to type. A session that runs
    `start` to orient finds the Manager it is running inside.
    """
    from rite_ai.managers.session import running

    return any(running(root, role.name) is not None for role in roles)


def _loop_verdict(root: Path) -> str:
    """The loop's own answer to "should this continue" (§9.14.4).

    `unknown` when it cannot be established, which is a STOP — the loop's
    own default on the unknown, and the safe direction when the alternative
    is spending on a project whose state could not be read.
    """
    try:
        from rite_ai.loop import plan_cycle

        return str(getattr(plan_cycle(root), "verdict", "unknown") or "unknown")
    except Exception:  # noqa: BLE001 - an unreadable project is `unknown`
        return "unknown"


def _start_a_manager(
    root: Path, role, sessions: int | None, minutes: float | None
) -> None:
    """Start one Manager, refusing without a ceiling (D-68)."""
    if sessions is None:
        click.echo(
            "refusing to start: --sessions is required and has no default.\n"
            "  It caps how many provider sessions this run may START — a "
            "COUNT, not spend, because rite cannot read the quota (§2.6.1, "
            "D-69).\n"
            "  Silently choosing a number you did not choose is how `rite "
            "pool fill --count 500` became possible, and spent quota is the "
            "one damage no cleanup reverses.",
            err=True,
        )
        raise SystemExit(1)

    if minutes is None:
        click.echo(
            "refusing to start: --minutes is required and has no default.\n"
            "  It caps how LONG this run may keep starting sessions. The two "
            "bounds are not interchangeable and neither suffices alone — "
            "measured: sessions that end instantly hit the count with the "
            "clock untouched, and sessions of realistic length hit the clock "
            "after three with the count untouched (D-82).\n"
            "  A duration that defaults to forever is a bound in name only, "
            "and this is the command that spends quota unattended.",
            err=True,
        )
        raise SystemExit(1)

    # `supervise`, not a single `start`: the feature is KEEPING the Manager
    # working. This runs in the FOREGROUND — it is the human's own process,
    # which is the whole of §9.12's compliance argument — so it does not
    # return until a bound or a stop verdict ends it.
    from rite_ai.managers.session import exit_status_available
    from rite_ai.managers.supervise import supervise

    if not exit_status_available():
        click.echo(
            "warning: this tmux did not report why a probe session ended "
            "(#{pane_dead_status} came back empty), so finished, quit and "
            "crashed may not be tellable apart. Where they are not, the "
            "Manager runs ONE session and stops rather than resume into an "
            "unknown state.\n"
            "  Measured as a SAMPLE, not a verdict on the machine: on tmux "
            "3.4 this has come back empty for a probe and populated for a "
            "real session minutes later. The run may do better than this "
            "warning; it will not do worse.",
            err=True,
        )

    click.echo(
        f"starting Manager '{role.name}' — up to {sessions} session(s), "
        f"for up to {minutes:g} minute(s). Whichever bound is reached first "
        "ends the run. Ctrl-C ends it too; a session already started keeps "
        "running."
    )
    # Composed HERE because this layer is the one that knows what the user
    # asked for. `for_manager` takes an `extra` that the journal's
    # instructions will fill when `--record-issues` is wired (D-93); it is
    # empty until then, and the empty case is the same shape as the full
    # one so this call site does not branch.
    from rite_ai.managers.prompt import for_manager

    outcome = supervise(
        root,
        role.name,
        engine=role.engine,
        max_sessions=sessions,
        window_seconds=minutes * 60.0,
        prompt=for_manager(role.name),
        verdict=_loop_verdict,
        note=lambda m: click.echo(m, err=True),
    )
    click.echo(outcome.reason)
    if not outcome.ok:
        raise SystemExit(1)


@cli.command("start")
@click.argument("directory", default=".")
@click.option(
    "--sessions",
    type=int,
    default=None,
    help="Ceiling on provider sessions this run may start. Required when "
    "starting a Manager; a COUNT, not spend (D-69).",
)
@click.option(
    "--minutes",
    type=float,
    default=None,
    help="Ceiling on how long this run may keep starting sessions. Required "
    "when starting a Manager: the two bounds catch different runaways and "
    "neither suffices alone (D-82).",
)
def start_cmd(directory: str, sessions: int | None, minutes: float | None) -> None:
    """Bring rite up — assess state and act.

    Examples:
      rite start
      rite start planner --sessions 3 --minutes 90   # a declared Manager
      rite start /path/to/project
      rite start acme               # resolves a registered alias (§8.9)
    """
    from rite_ai.lifecycle import start

    # MANAGER NAMES ARE INTERCEPTED HERE, before `_resolve_directory_or_alias`
    # — which tries an alias, then a path, and then EXITS 1. A Manager name
    # reaching it becomes "neither an existing directory nor a registered
    # alias", which is true and useless.
    here = _find_project_root()
    roles, config_problems = _manager_roles(here)

    # A config that will not parse is reported HERE rather than becoming a
    # Manager that silently does not exist. The directory/alias path below
    # would say "neither a directory nor an alias", which is true of the
    # word and says nothing about the reason.
    # Only when there IS a project here to misread. A draft fired on "file
    # not found" — which means no project in the cwd, not a broken one — and
    # so refused `rite start <alias>` run from anywhere outside a project,
    # which is the ordinary way that command is used.
    # `.rite/config.yaml` alone is NOT the test — rite's own repository has
    # one, tracked for the gate, and is not a rite project. `PROJECT_MARKERS`
    # is the property, which is the same distinction `_find_project_root`'s
    # own docstring makes. A draft used config.yaml and so refused
    # `rite start <alias>` run from inside rite's checkout.
    here_is_a_project = _is_project(here)
    if config_problems and here_is_a_project:
        click.echo("cannot read this project's Managers:", err=True)
        for problem in config_problems[:3]:
            click.echo(f"  {problem}", err=True)
        click.echo("  `rite doctor` shows the full picture.", err=True)
        raise SystemExit(1)

    # D-78/D-80. A draft gated this on `directory != "."`, so bare
    # `rite start` never reached it and "one works bare" / "2+ refuses and
    # lists" were written in SPEC, implemented in `manager_to_start`, unit
    # tested — and called by nothing. The policy existed and the wiring did
    # not, which reviews clean in every individual piece.
    if directory == "." and not roles:
        pass  # no Managers declared: the ordinary bring-up below
    elif directory == "." and roles:
        if _a_manager_is_running(here, roles):
            pass  # orientation (D-80): report below, do not start a second
        else:
            from rite_ai.managers import manager_to_start

            chosen = manager_to_start(roles, "")
            if not chosen.ok:
                click.echo(chosen.problem, err=True)
                raise SystemExit(1)
            _start_a_manager(here, chosen.role, sessions, minutes)
            return
    elif roles:
        from rite_ai.managers import manager_to_start

        chosen = manager_to_start(roles, directory)
        if chosen.ok:
            _start_a_manager(here, chosen.role, sessions, minutes)
            return
        # Not a Manager name: fall through to directory/alias resolution,
        # which is what `rite start /path` and `rite start <alias>` need.

    root = _resolve_directory_or_alias(directory)
    result = start(root)
    if not result.ok:
        click.echo(result.message, err=True)
        _echo_phase(result.phase, err=True)
        raise SystemExit(1)
    for action in result.actions:
        click.echo(f"  {action}")
    click.echo(result.message)
    _echo_instruction_drift(root)
    _echo_phase(result.phase)


def _echo_instruction_drift(root: Path) -> None:
    """Say when this session's own instructions are behind the installed rite.

    A session routes on `CLAUDE.md` and the commands in `.claude/`, and has no
    way to tell that an older rite wrote them — which is exactly how a fix to
    generated content fails to reach the project that reported the bug.
    `start` is where a session begins, so it is where being behind is worth
    one line.

    Never fails `start`. Orientation is what a session needs most when
    something is wrong, and a project whose files cannot be compared is still
    a project that can be worked on.
    """
    try:
        from rite_ai.update.refresh import pending

        plan = pending(root)
    except Exception:
        return
    if plan.files:
        # FILES, because that is the noun in the sentence. This counted
        # changes: one CLAUDE.md a release out of date is behind in several
        # sections at once, and announced itself as three files.
        click.echo(
            f"\n{len(plan.files)} generated file(s) are behind this rite — "
            "`rite update --files-only --dry-run` shows what would change"
        )


def _echo_phase(phase, err: bool = False) -> None:
    """Where the project is and the next step, printed last.

    It is the part of `start`'s output someone who has just run `rite init`
    needs, so it goes where the eye lands. Printed on failure too: "no
    `.rite/`" and "config errors" are phases, and the ones a newcomer meets
    first."""
    if phase is None:
        return
    from rite_ai.phase import render_phase

    click.echo("", err=err)
    for line in render_phase(phase):
        click.echo(line, err=err)


@cli.command("stop")
@click.argument("directory", default=".")
@click.option("--worker", "-w", default=None, help="Release claims for specific worker")
@click.option("--reason", "-r", default="clean shutdown", help="Reason for stopping")
@click.option(
    "--ticket",
    "-t",
    default="",
    help="Ticket ID to comment/label on handover (default: resolved from the "
    "released claims' own ticket, per SPEC §9.10 step 1)",
)
def stop_cmd(directory: str, worker: str | None, reason: str, ticket: str) -> None:
    """Shut down with handover — release claims, update board.

    Examples:
      rite stop
      rite stop --worker alpha --reason "lunch break"
      rite stop --worker alpha --ticket ABC-12
      rite stop acme                # resolves a registered alias (§8.9)
    """
    from rite_ai.lifecycle import stop

    root = _resolve_directory_or_alias(directory)
    result = stop(root, worker=worker, reason=reason, ticket=ticket)
    if not result.ok:
        click.echo(result.message, err=True)
        raise SystemExit(1)
    click.echo(result.message)


# --- Review ---


@cli.command()
@click.option(
    "--module", "-m", default=None, help="Module name — appends its own checklist"
)
def review(module: str | None) -> None:
    """Load and print the merged review checklist (SPEC §7).

    Prints the checklist a Dispatch session hands to review agents when
    running the review convention — this command does not spawn agents
    itself (that needs judgement; the CLI's charter is the non-AI surface,
    §9).

    `--module` takes a name from `.rite/modules.yaml`, or the exact path
    registered there. Anything else is refused, not ignored.

    Examples:
      rite review
      rite review --module backend
    """
    from rite_ai.config.parse import ParseError, parse_modules
    from rite_ai.review.checklist import format_checklist_prompt
    from rite_ai.review.merge import merge_checklists

    root = _find_project_root()

    module_path = None
    if module is not None:
        # Refuse an unregistered module rather than falling through to the
        # project-wide list. `merge_checklists` takes a PATH and treats a
        # missing file as "this repo has no checklist yet", which is right
        # for a registered module and silently wrong for a typo: `rite
        # review --module bakcend` printed the project-wide checklist,
        # exit 0, no warning — a review agent then works a checklist
        # missing every repo-level line and reports a clean pass against
        # it. Both review templates now make this command mandatory, so a
        # quiet wrong answer here is a quiet wrong answer in every review.
        modules = parse_modules(root / ".rite" / "modules.yaml")
        if isinstance(modules, ParseError):
            click.echo(f"modules.yaml: {modules.message}", err=True)
            raise SystemExit(1)
        wanted = module.rstrip("/")
        for m in modules:
            if wanted in (m.name, m.path.rstrip("/")):
                module_path = m.path
                break
        else:
            known = ", ".join(m.name for m in modules)
            click.echo(
                f"no module '{module}' in .rite/modules.yaml — "
                + (f"registered: {known}" if known else "none are registered")
                + ". Register it with `rite add module`, or run `rite review` "
                "with no --module for the project-wide checklist alone.",
                err=True,
            )
            raise SystemExit(1)

    items = merge_checklists(root, module_path)

    # Say what was merged, before the checklist itself.
    #
    # `rite review --module x` on a module with no checklist of its own was
    # byte-identical to `rite review` — so a reviewer told to work "the
    # project checklist plus that repo's own, appended" (which is what both
    # review templates now promise) had no way to tell the second half was
    # absent rather than empty. Found by running the command rather than
    # reading it.
    from rite_ai.review.merge import project_checklist_path, repo_checklist_path

    project_items = merge_checklists(root)
    # A module registered at `path: .` resolves to the project's own
    # checklist, so `merge_checklists` loads the same file twice and every
    # item appears twice. Reported rather than silently doubled — the count
    # in the provenance line would otherwise be the honest half of a
    # dishonest listing.
    same_file = (
        module_path is not None
        and repo_checklist_path(root, module_path).resolve()
        == project_checklist_path(root).resolve()
    )

    def _source(path: Path, count: int, nothing_here: str) -> str:
        # `relative_to` raises for a module registered with an ABSOLUTE
        # `path:` in modules.yaml — which `parse_modules` does not forbid —
        # so it is tried rather than assumed. The provenance line is a
        # convenience; it must not be the thing that turns `rite review`
        # into a traceback.
        try:
            shown = path.relative_to(root)
        except ValueError:
            shown = path
        if not path.is_file():
            return f"{shown} (absent)"
        if not count:
            # A file that exists and parses to nothing is NOT absent, and
            # saying so told a user who typed `* [ ]` instead of `- [ ]`
            # that their file did not exist.
            return f"{shown} ({nothing_here})"
        return f"{shown} ({count} item{'' if count == 1 else 's'})"

    sources = [
        _source(
            project_checklist_path(root),
            len(project_items),
            "no `- [ ] ` items found — check the bullet syntax",
        )
    ]
    if same_file:
        sources.append(
            "the same file again — that module is registered at the "
            "project root, so every item below is listed twice"
        )
    elif module_path is not None:
        sources.append(
            _source(
                repo_checklist_path(root, module_path),
                len(items) - len(project_items),
                "no `- [ ] ` items found — nothing appended",
            )
        )
    click.echo(f"# checklist: {' + '.join(sources)}")
    click.echo(format_checklist_prompt(items))


# --- Update ---


def _hand_off_refresh(take: tuple[str, ...]) -> bool:
    """Run the refresh in the rite that was just installed, not in this one.

    Returns whether a child ran; the caller refreshes in-process if not, which
    is no worse than what it did before.
    """
    import os
    import shutil
    import subprocess
    import sys

    if os.environ.get("RITE_UPDATE_CHILD"):
        return False
    command = (
        [rite, "update", "--files-only"]
        if (rite := shutil.which("rite"))
        else [sys.executable, "-m", "rite_ai.cli.main", "update", "--files-only"]
    )
    for name in take:
        command += ["--take-rite", name]
    try:
        subprocess.run(
            command, env={**os.environ, "RITE_UPDATE_CHILD": "1"}, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        click.echo(
            f"refresh the project's generated files with `rite update "
            f"--files-only` — this run could not ({e})",
            err=True,
        )
        return True
    return True


@cli.command()
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@click.option(
    "--files-only",
    is_flag=True,
    help="Refresh this project's generated files; do not update rite itself.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what refreshing generated files would change, with the "
    "difference for anything left alone. Changes nothing.",
)
@click.option(
    "--take-rite",
    "take",
    multiple=True,
    metavar="SECTION|FILE",
    help="Replace this CLAUDE.md section (by heading, e.g. 'Role: Owner') or "
    "generated file with rite's current version even though it differs. "
    "Repeatable.",
)
def update(yes: bool, files_only: bool, dry_run: bool, take: tuple[str, ...]) -> None:
    """Update rite itself, migrate `.rite/` config files, and refresh the files
    rite generated in this project (SPEC §9.9).

    Refreshing never overwrites your edits. Each generated section of
    CLAUDE.md records a hash of what rite wrote: a section still matching it is
    replaced with this version's, a section you changed is left alone and
    reported. Sections this version adds are inserted; your own sections are
    never touched. A copied command or agent file is replaced only when it is
    byte-identical to one a release shipped. The spec section is `rite spec
    add`'s to rewrite.

    Examples:
      rite update
      rite update --files-only --dry-run
      rite update --files-only --take-rite "Role: Owner"
    """
    from rite_ai.update import detect_install_method, migrate_config, run_self_update

    failed = False
    if not files_only and not dry_run:
        method = detect_install_method()
        failed = False
        if method == "dev":
            click.echo(run_self_update(method).message)
        else:
            confirmed = yes or click.confirm(f"Update rite via {method}?", default=True)
            if not confirmed:
                click.echo("cancelled")
                return
            result = run_self_update(method)
            click.echo(result.message, err=not result.ok)
            failed = not result.ok
            if not failed and _hand_off_refresh(take):
                # This process is the version that was just replaced. The
                # refresh below would be the OLD rite's idea of what the
                # generated files should say — on the first upgrade to a rite
                # that has this command at all, it would be no refresh.
                return

    # Runs whether or not the self-update worked: the files on disk have
    # nothing to do with whether a download succeeded.
    root = _find_project_root()
    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        if files_only or dry_run:
            click.echo("not a rite project — no generated files to refresh")
        if failed:
            raise SystemExit(1)
        return
    if not dry_run:
        applied = migrate_config(rite_dir)
        if applied:
            click.echo(f"migrated .rite/ config: {', '.join(applied)}")
        else:
            click.echo(".rite/ config already current")

    from rite_ai.update.refresh import refresh_project, report

    wanted = frozenset(take)
    results = refresh_project(root, take=wanted, apply=not dry_run)
    lines = report(results, dry_run, wanted)

    # The spec section is `rite spec add`'s to rewrite, so `refresh_project`
    # steps around it — which left it the one part of a generated file that
    # upgrading could not deliver. A Worker created before this version kept
    # instructions naming commands that did not exist yet, and both this
    # command and `rite doctor` called the file current. Asking its owner is
    # the fix that keeps the ownership: refresh does not learn the block,
    # update just stops claiming to have finished without it.
    from rite_ai.config.models import ProjectConfig
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.project_spec import refresh_spec_sections

    parsed_config = parse_config(rite_dir / "config.yaml")
    # A config that will not parse is doctor's to report; here it only means
    # the spec section cannot be rebuilt, so the rest of the refresh stands.
    spec_refresh = (
        refresh_spec_sections(root, parsed_config, apply=not dry_run)
        if isinstance(parsed_config, ProjectConfig)
        else None
    )
    if isinstance(parsed_config, ParseError):
        lines.append(f"  spec section not refreshed: {parsed_config.message}")
        spec_refresh = None
    updated = spec_refresh.updated if spec_refresh else []
    for rel in updated:
        lines.append(
            f"  {rel}: spec section would be brought up to date"
            if dry_run
            else f"  {rel}: spec section brought up to date"
        )
    if updated and lines and lines[0] == "generated files already current":
        # It was not current: this command just found the part of it that
        # `refresh_project` does not look at.
        lines = lines[1:]
    for line in lines:
        click.echo(line)

    if failed:
        raise SystemExit(1)


# --- Help ---


_HELP_TEXT = """\
rite — multi-session Claude coordination for teams.

Getting started:
  rite init                         Set up a new project (interactive)
  rite add module backend <git-url> Register a repo as a module
  rite add worker alpha             Create a worker workspace

Day to day:
  rite start                        Bring rite up: read handover, load state
  rite status                       What's happening right now
  rite claim src/ --worker alpha -t ABC-12
                                     Claim paths before touching them
  rite prepare --worker alpha       Sync a worker's workspace before a task
  rite release --worker alpha       Release claims after a merge
  rite stop                         Shut down with handover

Coordination surfaces:
  rite doctor                       Health check — tools, credentials, schedule
  rite schedule set 09:00-18:00 3   Set how many workers run, and when
  rite handover write/show          The continuous "state right now" snapshot
  rite heartbeat --worker alpha     "Still alive" beat — what watchdog reads
  rite watchdog                     Cheap liveness check (no LLM call)
  rite credential set/rotate        Store or rotate a credential

Every command supports --help for its own options and examples:
  rite <command> --help
  rite <group> <subcommand> --help  (e.g. rite schedule --help)

Full command reference: rite --help
"""


@cli.command()
def help() -> None:  # noqa: A001 - deliberately shadows builtin, it's the command name
    """Friendly command list with examples — distinct from `rite --help`,
    which is click's own generated (accurate, but example-free) reference.

    Examples:
      rite help
    """
    click.echo(_HELP_TEXT)


# `python -m rite_ai.cli.main` imported this module, found nothing to run, and
# exited 0 printing nothing — indistinguishable from a command that succeeded,
# which is the first line of this project's own review checklist ("a broken
# check that reports 'clean' is worse than no check at all") in the CLI's own
# entry point. The `-m` form is an idiom this codebase already uses
# (`python -m rite_ai.gate`, see `rite_ai/gate/__main__.py`), so a reader will
# reasonably try it here too.
if __name__ == "__main__":  # pragma: no cover - see tests/test_module_entry_point.py
    cli()
