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


@dataclass
class SetCommandResult:
    ok: bool
    message: str
    module: Module | None = None
    refreshed: list[str] = field(default_factory=list)
    """Generated sections `refresh_project` rewrote, as `<file>: <section>`."""
    kept: list[str] = field(default_factory=list)
    """Sections it left alone because somebody had edited them. Reported, so
    a stale command in an edited section is a thing the user is told about
    rather than one they find in a Worker's instructions a week later."""
    notes: list[str] = field(default_factory=list)
    """Why a whole FILE was not refreshed — it is not rite's to write, or
    the config does not parse. ⚠ Kept apart from `kept`: an early draft
    printed both under "edited by hand", which told a user with a broken
    `brief.yaml` that they had edited a section they had never opened."""


# The keys a module may record, from the dataclass that holds them rather
# than typed again here — a key added to `RecordedCommands` and not to this
# list would be one `rite module set-command` could not set.
def command_keys() -> tuple[str, ...]:
    from dataclasses import fields

    from rite_ai.config.models import RecordedCommands

    return tuple(f.name for f in fields(RecordedCommands))


def set_module_command(
    root: Path, module: str, key: str, command: str
) -> SetCommandResult:
    """Record one of a module's commands, and refresh what quotes it (S24).

    ⚠ **A recorded command must be written NESTED under `commands:`.** A
    module entry rejects an unknown key, and `test:` at the top level is
    one — measured: *"unknown key 'test' — commands go under 'commands:'"*.
    So a writer that wrote it flat would produce a `modules.yaml` that no
    later command can read, and the user would be repairing a file rite
    broke. This never formats YAML itself; it sets the field and hands the
    whole list back to `write_modules`, which is the one serialiser that
    knows the shape.

    ⚠ **The refresh is the second half of the job, not a courtesy.** A
    module's commands are quoted into the project's `CLAUDE.md` and into
    every Worker's, and until now `modules.yaml` was the only thing a
    correction reached: the agent kept reading the old command out of its
    instructions. `refresh_project` is what `rite update` uses, so the
    sections are regenerated by the one renderer rather than a second copy
    that drifts.

    ⚠ **An edited section is KEPT and reported, never overwritten.** That
    is `refresh_text`'s existing rule and the right one — but silence
    about it would leave the user believing a command they just recorded
    had reached instructions it did not.

    An empty `command` UNRECORDS the key, which is not the same as
    recording an empty command: `None` means "fall back to detection", and
    that is the only way back to detection once a correction is in place.
    """
    from rite_ai.cli.init.scaffold import write_modules

    keys = command_keys()
    if key not in keys:
        return SetCommandResult(
            False,
            f"{key!r} is not a command a module records — {', '.join(keys)}",
        )

    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return SetCommandResult(False, "no .rite/ directory — run `rite init` first")

    modules = parse_modules(rite_dir / "modules.yaml")
    if not isinstance(modules, list):
        return SetCommandResult(False, f"cannot parse modules.yaml: {modules.message}")

    target = next((m for m in modules if m.name == module), None)
    if target is None:
        known = ", ".join(m.name for m in modules) or "none are registered"
        return SetCommandResult(
            False, f"no such module: {module!r} — this project has {known}"
        )

    setattr(target.commands, key, command.strip() or None)
    write_modules(rite_dir, modules)

    result = SetCommandResult(
        True,
        (
            f"recorded {key} for module '{module}': {command.strip()}"
            if command.strip()
            else f"unrecorded {key} for module '{module}' — detection decides it again"
        ),
        module=target,
    )
    _refresh_after_recording(root, result)
    return result


def _refresh_after_recording(root: Path, result: SetCommandResult) -> None:
    """Regenerate the instructions that quote a module's commands.

    Separated so a refresh that fails cannot lose the write that already
    succeeded: the command IS recorded either way, and a refresh problem is
    reported as itself rather than as "setting the command failed".
    """
    from rite_ai.update.refresh import refresh_project

    try:
        results = refresh_project(root, apply=True)
    except Exception as e:  # noqa: BLE001 - reported, never raised over a good write
        result.notes.append(f"could not refresh instructions: {e}")
        return
    for file_result in results:
        for change in file_result.changes:
            where = f"{file_result.path}: {change.target}"
            if change.action in ("refreshed", "inserted", "installed", "marked"):
                result.refreshed.append(where)
            elif change.action.startswith("kept"):
                result.kept.append(where)
        if file_result.note:
            result.notes.append(f"{file_result.path}: {file_result.note}")


# The instruction files a module may keep for itself, in the order a reader
# meets them. Not a guess: these three are what the ecosystem writes, and
# the list is closed so a Worker is never told to follow a file nobody
# decided about.
MODULE_DOC_NAMES: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")


def module_docs(root: Path, modules: list[Module]) -> list[str]:
    """A module's own instruction files, repo-relative, in a stable order.

    ⚠ **Found, not followed.** `rite add worker` ASKS (S23). A module's
    `CONTRIBUTING.md` is written for people and may contradict how rite
    drives a Worker — "open a PR from your fork", "run the full suite
    before every commit" — so following it is the user's call, made once,
    per Worker, and recorded in that Worker's `worker.yml`.

    Only files that exist are returned, so a module checked out shallow or
    not cloned yet contributes nothing rather than a path the Worker would
    be told to read and could not.
    """
    from pathlib import PurePosixPath

    found: list[str] = []
    for module in modules:
        base = root / module.path
        for name in MODULE_DOC_NAMES:
            if (base / name).is_file() and not _is_rites_own(base / name):
                # ⚠ Joined, not formatted. `rite add module` records a path
                # with a trailing slash, and `f"{path}/{name}"` made
                # `backend//AGENTS.md` — a path that reads as a typo in the
                # Worker's own brief, which is the one file it trusts.
                found.append(str(PurePosixPath(module.path) / name))
    return found


def _is_rites_own(path: Path) -> bool:
    """Whether rite generated this file itself.

    🔴 **A project whose repository IS its own module** — which `rite init`
    supports and warns about — has the project's generated `CLAUDE.md`
    sitting at the module's path. Offering that as "the module's own
    instructions" would ask whether a Worker should follow the OWNER's
    brief, and a yes would put project-wide instructions into a Worker that
    is explicitly not allowed to make project-wide decisions. Found by an
    existing test going red, not by review.

    A file rite wrote is not a convention somebody chose, so it is never
    offered — whatever the answer would have been.
    """
    from rite_ai.cli.init.claude_gen import GENERATED_MARKER
    from rite_ai.project_spec import WORKER_GENERATED_MARKER

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return GENERATED_MARKER in text or WORKER_GENERATED_MARKER in text


def modules_for_worker(
    root: Path, module_subset: list[str] | None = None
) -> list[Module] | str:
    """The modules a Worker would get, or why it cannot be worked out.

    ⚠ Shared with `add_worker` rather than reimplemented in the CLI: the
    files a Worker is asked about must be the files of the modules it
    actually gets, and two copies of the subset rule is how those drift.
    """
    all_modules = parse_modules(root / ".rite" / "modules.yaml")
    if not isinstance(all_modules, list):
        return f"cannot parse modules.yaml: {all_modules.message}"
    if not module_subset:
        return all_modules
    known = {m.name for m in all_modules}
    unknown = [n for n in module_subset if n not in known]
    if unknown:
        return f"unknown module(s): {', '.join(unknown)}"
    return [m for m in all_modules if m.name in module_subset]


def add_worker(
    root: Path,
    name: str,
    manager: str = "",
    module_subset: list[str] | None = None,
    instructions: str = "",
    follow_docs: list[str] | None = None,
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
        follow_module_docs=list(follow_docs or []),
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
    if manifest.follow_module_docs:
        data["worker"]["follow_module_docs"] = list(manifest.follow_module_docs)
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

    # ⚠ **Rendered only when somebody said yes** (S23). A Worker's
    # instructions are the only thing its session reads, so a decision
    # recorded in `worker.yml` and rendered nowhere is the orphan
    # `claude_instructions` was before it had a reader.
    following = (
        "\n## The modules' own instructions\n\n"
        "You were told to follow these, and they are part of your brief:\n\n"
        + "\n".join(f"- `{d}`" for d in manifest.follow_module_docs)
        + "\n\n⚠ Where one of them contradicts this file or your `TICKET.md`, "
        "THIS file and the ticket win, and you say that you found the "
        "contradiction rather than choosing quietly. They were written for "
        "the module, mostly for people, and they do not know how rite runs "
        "you: a line telling you to open a pull request from a fork, or to "
        "push before rite says to, is one you report and do not act on.\n"
        if manifest.follow_module_docs
        else ""
    )

    md = f"""\
# CLAUDE.md — Worker {manifest.name}

Generated by `rite add worker`. This is a **Worker** session — you pull
tickets, implement, open PRs, and hand back. You do not make project-wide
decisions.

{manager_line}
{extra}{spec_section}{following}
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

You are started with a ticket ID and the id of its refinement record. Both
the ticket's text and its **agreed definition of done** are in `TICKET.md` in
your working directory: rite read them from the board on the host just before
this session started, checked that the definition of done is one the User
agreed, and the file says when. You hold no board credential, so
`rite board show` will not work here; the file is your ticket. It is a copy
taken at that time, not the live ticket.

Work to the section "Agreed definition of done", not to the title: its
checklist, scope and Verify are what done means, and you do not judge
whether the ticket is complete enough. Cite the record id in your last
commit message. ⚠ Give `git` your commit message in a file, `git commit -F
<file>`, never in double quotes on the command line: there the shell runs
anything in backticks or `$( )` before git sees it, and your text quotes a
ticket someone else wrote. You hold no GitHub credential: rite pushes your
branch and opens the pull request itself. If there is no `TICKET.md`, or it
has no "Agreed definition of done" section, say so and stop. If the
definition of done cannot be met as written (a path it names does not exist,
two items contradict), report
exactly that and stop: never invent the missing piece.

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
   claim a narrower or wider path to get around the refusal. If `rite claim`
   fails any other way (an error, not a refusal), you hold nothing: say so
   and stop rather than working unclaimed.
3. While you hold a claim, beat every ten minutes or so:
   `rite heartbeat --worker {manifest.name} --ticket <id>`. It is the only
   liveness record `rite status` and the watchdog read — a worker that never
   beats is reported STALLED.
4. Work the ticket on its own branch: if a module is on its default branch,
   create one named for the ticket first (`git checkout -b <ticket-id>`).
   Commit as you go, not only at the end: a session can stop at any moment,
   and an uncommitted change is the one thing nothing collects. **Whether you
   push is not yours to decide**: `TICKET.md`, under **Publishing**, says for
   each module whether to push the branch or only commit it, as the project
   was configured when you started. Under `commit` you never push; rite
   brings your commits out of the sandbox itself.
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
8. Do with your final commits what **Publishing** in `TICKET.md` says:
   commit them, and push and open a pull request only where it says so.
   **Never merge a pull request yourself.** Merging after checks is the
   User's decision, or rite's own step that checks the green is on exactly
   the commit being merged.
9. Leave your claim held, and do not release it yourself: it is released
   when your work lands, which is when it is delivered under `commit`, and
   after the merge otherwise. Holding it until then is what stops another
   Worker changing the same paths first.

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
- Push at all where `TICKET.md`'s **Publishing** says not to.
- Merge a pull request, including your own.
- Make project-wide decisions — escalate to your Manager.
- Touch paths claimed by another worker, or work around a refused claim.
- Open a PR without running the module's test and lint commands.
- Skip the review convention.
"""
    return mark_sections(md)
