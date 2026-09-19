"""Add and remove modules and workers (SPEC §5.1, §9.5–9.7).

Each operation reads `modules.yaml`, performs the git/filesystem work, and
writes the updated `modules.yaml` back. Workers also get a `worker.yml`
manifest and a scoped Claude configuration.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rite_ai.config.models import (
    Module,
    ProjectConfig,
    SandboxConfig,
    SpecConfig,
    WorkerManifest,
)
from rite_ai.config.parse import parse_config, parse_modules
from rite_ai.names import name_problem
from rite_ai.project_spec import mark_spec_section
from rite_ai.state import locked, write_atomic


@dataclass
class AddModuleResult:
    ok: bool
    message: str
    module: Module | None = None


@dataclass
class AddWorkerResult:
    ok: bool
    message: str
    worker: WorkerManifest | None = None
    cloned_modules: list[str] = field(default_factory=list)
    # (module name, why) for every module this worker was given that it
    # did NOT end up with a checkout of. Separate from `cloned_modules`
    # rather than inferred from the difference, so the reason survives to
    # whoever reads the result.
    failed_modules: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RemoveModuleResult:
    ok: bool
    message: str


@dataclass
class UnsavedWork:
    """What deleting one module checkout would destroy."""

    module: str
    branch: str
    uncommitted: list[str] = field(default_factory=list)
    unpushed: int = 0
    # Set when git could not be asked at all. Reported as its own thing
    # rather than folded into a count: saying "1 commit(s) on no remote"
    # about a repository whose state could not be read is a number made
    # up to carry a warning, and this text is what someone decides a
    # deletion on.
    unreadable: str = ""

    def describe(self) -> str:
        parts = []
        if self.unreadable:
            parts.append(f"could not be read ({self.unreadable})")
        if self.uncommitted:
            shown = ", ".join(self.uncommitted[:4])
            more = (
                f", …{len(self.uncommitted) - 4} more"
                if len(self.uncommitted) > 4
                else ""
            )
            parts.append(f"{len(self.uncommitted)} uncommitted ({shown}{more})")
        if self.unpushed:
            parts.append(f"{self.unpushed} commit(s) on no remote")
        return f"{self.module} @ {self.branch or '(unknown branch)'}: " + "; ".join(
            parts
        )


@dataclass
class RemoveWorkerResult:
    ok: bool
    message: str
    unsaved: list[UnsavedWork] = field(default_factory=list)


DEFAULT_BRANCH_FALLBACK = "main"


def resolve_cloned_branch(module_dir: Path) -> str:
    """The branch a fresh clone actually checked out. `git clone` without
    `--branch` follows the remote's own HEAD, so this is the only way to
    know what was taken — and it is what must be recorded, since every
    later `prepare`/clone reads the recorded value back."""
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=module_dir,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    )
    resolved = proc.stdout.strip()
    return resolved if proc.returncode == 0 and resolved else DEFAULT_BRANCH_FALLBACK


def add_module(
    root: Path,
    name: str,
    url: str | None = None,
    branch: str | None = None,
    description: str = "",
) -> AddModuleResult:
    """Register (and optionally clone) a module.

    `branch=None` means "whatever the remote's default is" — `--branch` is
    omitted from the clone entirely rather than defaulting to `main`. A
    hardcoded `main` made `rite add module <name> <url>` fail outright
    against every repo whose default is `master`, `develop`, or `trunk`,
    with `fatal: Remote branch main not found in upstream origin` — and
    the first thing a new project does is register its existing repos.
    """
    # Same class: `add_module(root, "../../ESCAPED")` returned ok=True,
    # created a directory outside the project tree and ran `git init`
    # in it.
    problem = name_problem(name, kind="module name")
    if problem:
        return AddModuleResult(False, f"refusing to add: {problem}")

    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return AddModuleResult(False, "no .rite/ directory — run `rite init` first")

    modules_path = rite_dir / "modules.yaml"
    existing = parse_modules(modules_path)
    if not isinstance(existing, list):
        return AddModuleResult(False, f"cannot parse modules.yaml: {existing.message}")

    if any(m.name == name for m in existing):
        return AddModuleResult(False, f"module '{name}' already registered")

    module_dir = root / name
    if url:
        if module_dir.exists():
            return AddModuleResult(
                False,
                f"directory '{name}/' already exists — "
                "remove it first or register without a URL",
            )
        try:
            clone_args = ["git", "clone"]
            if branch is not None:
                clone_args += ["--branch", branch]
            subprocess.run(
                [*clone_args, url, str(module_dir)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            return AddModuleResult(False, f"git clone failed: {e.stderr.strip()[:300]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            return AddModuleResult(False, f"git clone failed: {e}")
    else:
        module_dir.mkdir(parents=True, exist_ok=True)
        if not (module_dir / ".git").exists():
            subprocess.run(
                ["git", "init"],
                cwd=module_dir,
                capture_output=True,
                timeout=10,
            )

    # Record what was actually checked out, not what was asked for — an
    # unspecified branch is only knowable after the clone.
    if branch is None:
        branch = resolve_cloned_branch(module_dir) if url else DEFAULT_BRANCH_FALLBACK

    module = Module(
        name=name,
        path=f"{name}/",
        url=url,
        branch=branch,
        description=description,
    )
    # Re-read and re-check UNDER the lock. The check above is a cheap
    # pre-flight so a doomed registration does not pay for a clone; this
    # is the authoritative one, because the clone between them can take
    # two minutes and another `rite add module` can land in that window.
    # Holding the lock across the clone instead would serialise every
    # registration behind the slowest network operation rite performs.
    with _locked_modules(modules_path):
        latest = parse_modules(modules_path)
        if not isinstance(latest, list):
            return AddModuleResult(
                False, f"cannot parse modules.yaml: {latest.message}"
            )
        if any(m.name == name for m in latest):
            return AddModuleResult(False, f"module '{name}' already registered")
        latest.append(module)
        _write_modules(modules_path, latest)

    return AddModuleResult(True, f"module '{name}' registered", module=module)


def remove_module(root: Path, name: str) -> RemoveModuleResult:
    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return RemoveModuleResult(False, "no .rite/ directory — run `rite init` first")

    modules_path = rite_dir / "modules.yaml"
    with _locked_modules(modules_path):
        existing = parse_modules(modules_path)
        if not isinstance(existing, list):
            return RemoveModuleResult(
                False, f"cannot parse modules.yaml: {existing.message}"
            )

        found = [m for m in existing if m.name == name]
        if not found:
            return RemoveModuleResult(False, f"module '{name}' not registered")

        _write_modules(modules_path, [m for m in existing if m.name != name])

    return RemoveModuleResult(
        True,
        f"module '{name}' deregistered from modules.yaml "
        f"(directory not deleted — that is your decision)",
    )


def add_worker(
    root: Path,
    name: str,
    manager: str = "",
    module_subset: list[str] | None = None,
    instructions: str = "",
) -> AddWorkerResult:
    """`instructions` is SPEC §8.4's `claude_instructions` — extra standing
    direction for this one Worker, stored in its `worker.yml` and rendered
    into its `CLAUDE.md`. It had neither a writer nor a reader before, so
    the documented key could only ever be set by hand and was then dropped
    on the next write."""
    # Same boundary as `remove_worker`. Creating `workers/../x` is not
    # destructive, but it writes a workspace outside the project that every
    # later command then fails to find, and it is the same missing check.
    problem = name_problem(name, kind="worker name")
    if problem:
        return AddWorkerResult(False, f"refusing to add: {problem}")

    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return AddWorkerResult(False, "no .rite/ directory — run `rite init` first")

    modules_path = rite_dir / "modules.yaml"
    all_modules = parse_modules(modules_path)
    if not isinstance(all_modules, list):
        return AddWorkerResult(
            False, f"cannot parse modules.yaml: {all_modules.message}"
        )

    if module_subset:
        known_names = {m.name for m in all_modules}
        unknown = [n for n in module_subset if n not in known_names]
        if unknown:
            return AddWorkerResult(False, f"unknown module(s): {', '.join(unknown)}")
        modules = [m for m in all_modules if m.name in module_subset]
    else:
        modules = all_modules

    workers_dir = root / "workers"
    worker_dir = workers_dir / name
    if worker_dir.exists():
        return AddWorkerResult(
            False, f"worker directory 'workers/{name}/' already exists"
        )

    worker_dir.mkdir(parents=True)

    cloned: list[str] = []
    failed: list[tuple[str, str]] = []
    for m in modules:
        src = root / m.path
        dest = worker_dir / m.name
        remote_error = ""
        if m.url:
            try:
                subprocess.run(
                    ["git", "clone", "--branch", m.branch, m.url, str(dest)],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=120,
                    check=True,
                )
                cloned.append(m.name)
                continue
            except subprocess.CalledProcessError as e:
                remote_error = _git_detail(e) or f"git clone exited {e.returncode}"
            except (OSError, subprocess.TimeoutExpired) as e:
                remote_error = str(e) or e.__class__.__name__
        if not src.is_dir():
            failed.append(
                (
                    m.name,
                    f"{remote_error}; no local copy at {src} to fall back on"
                    if remote_error
                    else f"no url in modules.yaml and no local copy at {src}",
                )
            )
            continue
        local_error = _clone_local(src, dest, m.branch)
        if local_error:
            detail = (
                f"{remote_error}; then {local_error}" if remote_error else local_error
            )
            failed.append((m.name, detail))
            continue
        cloned.append(m.name)

    manifest = WorkerManifest(
        name=name,
        manager=manager,
        modules=[m.name for m in modules],
        claude_instructions=instructions,
    )
    _write_worker_manifest(worker_dir, manifest)
    _write_worker_claude_config(
        worker_dir,
        manifest,
        _spec_config(root),
        _module_commands_section(root, modules),
    )

    return AddWorkerResult(
        True,
        f"worker '{name}' created with {len(cloned)} module(s)",
        worker=manifest,
        cloned_modules=cloned,
        failed_modules=failed,
    )


def unsaved_work(worker_dir: Path) -> list[UnsavedWork]:
    """Everything under `worker_dir` that exists nowhere else.

    A worker's checkout is where the work actually happens, so it is the
    one directory in a rite project whose contents can be irreplaceable.
    """
    from rite_ai.workspace import git_ops

    found: list[UnsavedWork] = []
    if not worker_dir.is_dir():
        return found
    for child in sorted(p for p in worker_dir.iterdir() if p.is_dir()):
        if not git_ops.is_git_repo(child):
            continue
        dirty = git_ops.uncommitted_paths(child)
        unpushed = git_ops.unpushed_commit_count(child)
        branch = git_ops.current_branch(child)
        # A repository this cannot interrogate is reported as at-risk
        # rather than as safe: "I could not find out" is not "nothing
        # would be lost", and this decides whether files are deleted —
        # the same asymmetry `rite_ai.state` draws between an absent file
        # and an unreadable one.
        problems = [
            r.message for r in (dirty, unpushed) if isinstance(r, git_ops.GitError)
        ]
        dirty_list = dirty if isinstance(dirty, list) else []
        unpushed_n = unpushed if isinstance(unpushed, int) else 0
        if not problems and not dirty_list and not unpushed_n:
            continue
        found.append(
            UnsavedWork(
                module=child.name,
                branch=branch if isinstance(branch, str) else "",
                uncommitted=dirty_list,
                unpushed=unpushed_n,
                unreadable="; ".join(problems),
            )
        )
    return found


def remove_worker(root: Path, name: str, force: bool = False) -> RemoveWorkerResult:
    """Delete a worker's workspace and deregister it.

    **Refuses while that workspace holds work that exists nowhere else.**
    It used to be an unconditional `shutil.rmtree`. Measured, on the
    scenario this whole mechanism exists for: a worker was killed between
    claim and commit, holding `DEF-1`, with a modified `README.md` and a
    new `src/scoring.ts` on `feature/DEF-1`; the Manager did the natural
    thing and retired it, and `rite remove worker w1` printed `worker 'w1'
    removed`, exited 0, and destroyed both files. They were not committed,
    not pushed, not stashed, and not anywhere else.

    `prepare_workspace` already refuses on exactly this condition, and its
    module docstring says why in terms that describe this function without
    knowing it existed: the dirty-tree rule is how "no residue from the
    previous task" is enforced "rather than being silently swept away by
    some separate cleanup step that could just as easily delete work
    someone meant to keep." Two mechanisms disagreeing about the same
    directory is how one of them deletes what the other preserved.

    Note what it deletes versus what it keeps: the workspace is
    irreplaceable and the claims, heartbeat and handover are bookkeeping
    that can be rebuilt — and this destroyed the first while leaving the
    second behind for the watchdog to complain about.
    """
    # BEFORE ANY PATH IS BUILT. `rite remove worker ..` resolved to the
    # project root, `.is_dir()` agreed, and `shutil.rmtree` emptied the
    # repository — `.rite/`, `src/`, everything — before raising, so the
    # user's first signal was a traceback about a directory that no longer
    # existed. `unsaved_work` could not save them: it inspects the target's
    # direct children that are git repos, and a project root has none, so it
    # truthfully reported nothing at risk about a directory holding all of it.
    problem = name_problem(name, kind="worker name")
    if problem:
        return RemoveWorkerResult(False, f"refusing to remove: {problem}")

    workers_dir = root / "workers"
    worker_dir = workers_dir / name

    if not worker_dir.is_dir():
        return RemoveWorkerResult(False, f"worker 'workers/{name}/' does not exist")

    at_risk = [] if force else unsaved_work(worker_dir)
    if at_risk:
        return RemoveWorkerResult(
            False,
            f"refusing to remove worker '{name}': its workspace holds work "
            "that is not committed or not pushed anywhere, and removing it "
            "deletes that work permanently.",
            unsaved=at_risk,
        )

    shutil.rmtree(worker_dir)

    if workers_dir.is_dir() and not any(workers_dir.iterdir()):
        workers_dir.rmdir()

    return RemoveWorkerResult(True, f"worker '{name}' removed")


def _git_detail(proc) -> str:
    """git puts the useful line on stderr; `errors="replace"` keeps a
    non-UTF-8 remote message from turning a clone failure into a crash."""
    for stream in (getattr(proc, "stderr", None), getattr(proc, "stdout", None)):
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", "replace")
        if stream and stream.strip():
            return " / ".join(
                line.strip() for line in stream.strip().splitlines() if line.strip()
            )
    return ""


def _clone_local(src: Path, dest: Path, branch: str) -> str:
    """Clone `src` into `dest`. Returns "" on success, or why it failed.

    It used to return None and swallow everything: `capture_output=True`
    with no `check=True`, so a `git clone` that exited non-zero was
    indistinguishable from one that worked. `add_worker` appended the
    module to `cloned` regardless, and `rite add worker alpha` printed

        worker 'alpha' created with 1 module(s)
          cloned: backend

    and exited 0 with no `workers/alpha/backend` on disk at all. Measured
    against a module registered with no url, whose source checkout had no
    commits yet: `fatal: Remote branch main not found in upstream origin`.
    `rite prepare` caught it afterwards and said so clearly — but the
    command that did the cloning had already reported success, which is
    the wrong place to find out.
    """
    try:
        proc = subprocess.run(
            ["git", "clone", "--branch", branch, str(src), str(dest)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e) or e.__class__.__name__
    if proc.returncode != 0:
        return _git_detail(proc) or f"git clone exited {proc.returncode}"
    return ""


def _write_modules(path: Path, modules: list[Module]) -> None:
    from rite_ai.cli.init.scaffold import modules_to_yaml

    write_atomic(path, modules_to_yaml(modules))


def _locked_modules(path: Path):
    """Exclusion around `modules.yaml`'s read-modify-write.

    `add_module` and `remove_module` both read the file, change one entry
    and write the whole thing back, with no lock and a truncating write.
    Measured, six concurrent registrations over five rounds: every round
    lost entries and the worst kept one of six. The truncating write is
    the worse half — a process killed between the truncate and the write
    leaves a torn `modules.yaml`, `load_project` then fails on it, and a
    failed `load_project` is what sends `perform_handover` to the outbox
    instead of the board."""
    return locked(path)


def _write_worker_manifest(worker_dir: Path, manifest: WorkerManifest) -> None:
    """Serialises every `WorkerManifest` field, not the three `add_worker`
    happens to set.

    `claude_instructions` — SPEC §8.4's own worker.yml example carries it —
    was read by `parse_worker` and written by nothing, so the only way to
    set it was to hand-edit the file, and the first rewrite of that file
    would have deleted it again without saying so. Same rule, and the same
    reason, as the note on `config_to_yaml` in `cli/init/scaffold.py`, which
    is also a line on the review checklist this repo ships.

    Omitted when empty rather than written as `claude_instructions: ''`: a
    Worker with nothing extra to say gets the file §8.4 documents, not an
    empty key inviting someone to wonder what it does."""
    data = {
        "worker": {
            "name": manifest.name,
            "manager": manifest.manager,
            "modules": manifest.modules,
        }
    }
    if manifest.claude_instructions:
        data["worker"]["claude_instructions"] = manifest.claude_instructions
    write_atomic(
        worker_dir / "worker.yml",
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
    )


def _install_worker_review_convention(claude_dir: Path) -> None:
    """Copy the review convention into the Worker's own `.claude/`.

    SPEC §9.6 says `rite add worker` generates "a minimal `CLAUDE.md` and
    `.claude/` directory". It generated the directory and nothing else —
    `mkdir`, then never written to again — while the `CLAUDE.md` written
    beside it told the Worker, at step 6, to run `/review`. A slash command
    is read from the `.claude/` of the directory the session is started in,
    and a Worker session is started in `workers/<name>/`, so that step named
    a command that was not there. An empty directory is not a
    configuration; this is what §9.6 was describing.

    The review command and the four agents it names are copied from the
    SAME `templates/` tree `rite init` copies the project-root pair from,
    via the same `_AGENT_FILES` list — not a second list of the same
    filenames. Stage 2 #3's lesson, applied to a third copy site: the last
    time one of these sets existed twice, the two wired different installers
    to divergent content.

    Deliberately NOT `ticket.md` / `refine.md`. §9.4.2 scopes a Worker to
    "the claims system, the review convention, the ticket workflow", and
    only the review convention is named as a slash command by the
    instructions this function writes — copying in a command nothing tells
    the Worker to run would recreate the same defect one directory over.
    """
    from rite_ai.cli.init.claude_gen import _AGENT_FILES
    from rite_ai.cli.init.paths import templates_dir

    src = templates_dir()
    agents_dir = claude_dir / "agents"
    commands_dir = claude_dir / "commands"
    agents_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    for fname in _AGENT_FILES:
        shutil.copyfile(src / "agents" / fname, agents_dir / fname)
    shutil.copyfile(src / "commands" / "review.md", commands_dir / "review.md")


def _spec_config(root: Path) -> SpecConfig:
    """This project's spec pointers, or an empty one if config is
    unreadable — a Worker workspace is still worth creating when the spec
    section is missing or malformed."""
    config = parse_config(root / ".rite" / "config.yaml")
    return config.spec if isinstance(config, ProjectConfig) else SpecConfig()


def _spec_section(spec: SpecConfig) -> str:
    """The pointer a Worker reads. Paths, the convention and how to ask for
    one part — never the content: a spec runs to thousands of lines, and
    pasting it into every Worker's context on every job spends the quota this
    tool exists to make last overnight.

    The retrieval commands are here because this file is where the Worker
    doing the reading actually looks. The Owner's `CLAUDE.md` carried them
    first and this one did not, which put the instructions in front of the
    role that does not read slices and hid them from the role that does.

    The closing line is the load-bearing one. rite has no way to tell
    whether a spec still describes the code — and a stale spec handed over
    confidently is worse than none, because the Worker implements it and
    nobody finds out until review. Naming the limit turns that from a
    silent defect into a reported one."""
    if not spec.paths:
        return ""
    listed = "\n".join(f"- `{p}`" for p in spec.paths)
    convention = f"\n{spec.convention}\n" if spec.convention else ""
    return f"""
## Project spec

This project's design lives in:

{listed}

Read what your ticket needs; do not read it all.
{convention}
When your ticket cites a part of it (`§5.3`, `D-31`), ask for that part
rather than opening the document:

```
rite spec slice 5.3 --worker <your-name>
```

It prints that section, what it cites, and the sections everything depends
on. Where the spec has been digested, `rite spec show 5.3` gives you the
same section rewritten shorter and reviewed against its source, and exits
non-zero if that text has drifted from the spec.

**If the slice was not enough and you read the whole spec anyway, say so**
when you write your handover: `rite handover write --spec-fallback 5.3`.
Nobody can see a slice that came up short — that count is the only evidence
the slices need to be bigger.

rite cannot tell whether this is current. If it contradicts the code, say so
in the ticket rather than silently implementing either.
"""


def _module_commands_section(root: Path, modules: list[Module]) -> str:
    """Each module's commands, worked out NOW, for this Worker's CLAUDE.md.

    The project root's CLAUDE.md carries the same map, but as of `rite init`:
    nothing regenerates it. A command recorded in `modules.yaml` afterwards,
    or a sandbox setting changed since, reached no session at all. A Worker's
    CLAUDE.md is written when the Worker is, so it sees both — and it is the
    file the session that runs the commands actually loads.
    """
    from rite_ai.cli.init.claude_gen import _format_commands
    from rite_ai.cli.init.detect import module_commands

    rows = ["", "## Module commands", ""]
    if not modules:
        rows.append("_(no modules)_")
        return "\n".join(rows) + "\n"
    config = parse_config(root / ".rite" / "config.yaml")
    if isinstance(config, ProjectConfig):
        sandbox = config.sandbox
    else:
        # Said, not assumed silently: the sandbox setting decides which flags
        # the Swift commands carry, and the default is what gets used here.
        sandbox = SandboxConfig()
        rows.append(
            f"_`.rite/config.yaml` could not be read ({config.message}); the "
            "commands below assume the default sandbox setting._"
        )
        rows.append("")
    for m in modules:
        rows.append(f"### `{m.name}/`")
        rows.extend(_format_commands(module_commands(m, root, sandbox)))
        rows.append("")
    return "\n".join(rows)


def worker_spec_block(spec: SpecConfig) -> str:
    """The Worker's spec section between its markers, or "" when there is no
    spec — a Worker gets no heading over nothing. Shared with `rite spec
    add`, which rewrites existing Workers' files."""
    rendered = _spec_section(spec)
    return mark_spec_section(rendered) if rendered else ""


# The heading a Worker's module list sits under. Defined here because this
# module writes it, and imported by `project_spec` rather than repeated:
# renaming it silently broke `rite spec add`'s insertion point, which had the
# literal in a second file. `WORKER_MODULES_HEADING_LEGACY` is what releases
# up to v0.3.0 wrote — a worker file generated then must still be found.
WORKER_MODULES_HEADING = "## Modules checked out in your workspace"
WORKER_MODULES_HEADING_LEGACY = "## Your modules"


def _write_worker_claude_config(
    worker_dir: Path,
    manifest: WorkerManifest,
    spec: SpecConfig | None = None,
    module_commands: str = "",
) -> None:
    claude_dir = worker_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    _install_worker_review_convention(claude_dir)

    write_atomic(
        worker_dir / "CLAUDE.md",
        render_worker_claude_md(manifest, spec, module_commands),
    )


def render_worker_claude_md(
    manifest: WorkerManifest,
    spec: SpecConfig | None = None,
    module_commands: str = "",
) -> str:
    """A Worker's `CLAUDE.md`, as `rite add worker` writes it — separate from
    the writing so `rite update` can regenerate it and compare."""
    from rite_ai.generated_sections import mark_sections

    manager_line = (
        f"Your Manager is **{manifest.manager}**."
        if manifest.manager
        else "No Manager assigned yet."
    )
    modules_lines = "\n".join(f"- `{m}/`" for m in manifest.modules)

    # The other half of `claude_instructions` having a writer: a key stored
    # in worker.yml and never rendered anywhere a session reads is the same
    # orphan one file further on. This is the only generated file a Worker
    # session loads, so it is where per-Worker instructions have to land.
    extra = (
        f"\n## From your Manager\n\n{manifest.claude_instructions.rstrip()}\n"
        if manifest.claude_instructions.strip()
        else ""
    )

    block = worker_spec_block(spec or SpecConfig())
    spec_section = f"\n{block}\n" if block else ""

    md = f"""\
# CLAUDE.md — Worker {manifest.name}

Generated by `rite add worker`. This is a **Worker** session — you pull
tickets, implement, open PRs, and hand back. You do not make project-wide
decisions.

{manager_line}
{extra}{spec_section}
{WORKER_MODULES_HEADING}

{modules_lines or "_(none)_"}

**This is not an assignment, and not a specialism. A Worker is a workspace —
not a module, a component or a specialism** (SPEC.md §5.3.4). Every Worker
carries the same project credentials, so any Worker can take any ticket;
tickets go to whoever is free. Two Workers editing different files of the
same module at once is normal, and `rite claim` on the paths is what keeps
you apart — not which module you are "for". If you were started on a ticket
for something not checked out above, say so rather than assuming the ticket
went to the wrong Worker.
{module_commands}
## Your ticket

You are usually started with a ticket ID ("Work ticket ABC-12."). Read that
ticket with `rite board show <ticket-id>`, which prints its title, status and
description from the board this project uses, JIRA or GitHub Issues. If it is
not complete enough to start cold — no
definition of done, no clear scope — or you cannot read it at all, say so
and stop rather than guessing.

## Workflow

1. Prepare your workspace: `rite prepare --worker {manifest.name}` — right
   repos, right branches, no residue from a previous task (SPEC §2.1). A
   dirty tree blocks and is never discarded. In a sandbox, `rite sandbox
   start` already ran it before your session began, and it cannot run from
   inside: skip it.
2. Claim paths before touching them:
   `rite claim <paths> --worker {manifest.name} --ticket <id>`. Claim files or
   directories, never a whole module. If the claim is refused, another
   worker holds an overlapping path: do not work on those paths, and do not
   claim a narrower or wider path to get around the refusal.
3. While you hold a claim, beat every ten minutes or so:
   `rite heartbeat --worker {manifest.name} --ticket <id>`. It is the only
   liveness record `rite status` and the watchdog read — a worker that never
   beats is reported STALLED.
4. Work the ticket on its own branch: if a module is on its default branch,
   create one named for the ticket first (`git checkout -b <ticket-id>`).
   Push that branch after every commit, not only at the end —
   `git push -u origin <ticket-id>`. Your work exists outside this session
   only once it is pushed. In a sandbox this checkout is a copy that is
   discarded with the sandbox, and a session can stop at any moment, so a
   commit that was never pushed is gone.
5. Run the module's own **test and lint** commands. `rite prepare` prints
   them every time it runs, resolved at that moment — those are the ones to
   use. **Module commands** above lists them as `Test:` and `Lint:` as of
   when this Worker was created, and the module map in the
   project root's `CLAUDE.md` has them as of `rite init`; a command recorded
   in `modules.yaml` since then appears only in `rite prepare`'s output. In a
   sandbox `rite prepare` ran before you started and you cannot see its
   output, so use **Module commands** above. Run
   them as written; where an entry says "not detected", ask rather than
   inventing a command, because one that is wrong in a way that still exits
   0 looks exactly like a passing suite.
6. Verify your own fix before review. A green suite says the project still
   works, not that your change does anything — delete the fix and re-run
   whatever proves it.
7. Run `/review` (the review convention from the project root).
8. Push your final commits, then open a PR, get it reviewed, and merge.
9. Release your claim (after merge, not before): `rite release --worker {manifest.name}`

## When this ticket is done

Tell your Manager you are free, in the same message that reports the work.
Do not start another ticket on your own: your Manager holds the board and the
capacity, and two Workers picking their own next ticket is how the same path
gets claimed twice.

If nothing comes back, stop — and say you are stopping because you were not
given more, rather than going quiet. A Worker that finishes and falls silent
is indistinguishable from one that died mid-ticket, and only one of those
needs somebody woken up.

## What you must not do

- Push directly to the root branch.
- Make project-wide decisions — escalate to your Manager.
- Touch paths claimed by another worker, or work around a refused claim.
- Open a PR without running the module's test and lint commands.
- Skip the review convention.
"""
    return mark_sections(md)
