"""Deliver a finished task: bring a Worker's commits out of its sandbox and
into the project's own checkout, under the strategy in force (PB1).

**"Don't push" must never mean "don't commit"** (Robert, 2026-09-26). The
work a Worker commits lives in yoloAI's copy of its workspace, which a
destroy deletes. So every strategy starts with the same step, done here by
rite, not by a model: **collect** each module's `<ticket>` branch into the
project's checkout of that module. It adds refs and nothing else: no working
tree changes, no `--force`, no rebase. git itself refuses a non-fast-forward
or a branch that is checked out, and that refusal is passed on with its fix.

**What each strategy does after collecting:**
- `commit`: nothing more. The branch is local, rebaseable, and nothing
  reached any remote.
- `push`: after rite's publish gate passes on exactly `<branch>..<ticket>`,
  push the ticket branch onto the module's branch on origin. No force: a
  branch that moved is refused by the remote, and rite never rebases.
- `pull_request`: after the gate, push the ticket branch and open a pull
  request against the module's branch, or report the one already open.
  Nothing here merges it (`auto_merge`, piece 5, is rite's gated step).
- `push_to_shared`: refused here too, not only at start (v0.8.0).

A refusal after collecting loses nothing: the work is already on the
project's branch, and the note says so.

**Read once** (`record`, design §3.1): the settings the Worker was started
under are compared with the config read now. If they differ in any key, or
either cannot be read, rite collects WITHOUT squashing and does nothing else,
and says both values and the command the User runs. A change can take
permission away from in-flight work, never give it. A User running `rite
deliver` at a terminal is the authority on current config and is not held
to the snapshot.

**Uncommitted work is refused, not committed for the Worker.** Committing an
unverified half-state would publish something nobody decided was finished.
It stays in the copy, which is never destroyed while it holds it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rite_ai.config.models import Module
from rite_ai.publishing import record as publish_record
from rite_ai.publishing.settings import _unavailable, effective

PUSHES = {"push", "pull_request"}
"""Strategies under which rite sends the work to the module's origin."""


@dataclass(frozen=True)
class Outcome:
    """What happened to one module of one delivery.

    `note()` is what the Manager is told: one line that carries its own fix.
    """

    module: str
    ticket: str
    ok: bool
    why: str
    fix: str = ""
    # Set when a pull request was opened or found: its number, repository
    # and the exact head rite pushed, which is the only head `auto_merge`
    # will ever merge (piece 5).
    pr: int | None = None
    repo: str = ""
    head: str = ""

    def note(self) -> str:
        head = "Delivered" if self.ok else "NOT delivered"
        line = f"{head} {self.module}/{self.ticket}: {self.why}."
        return f"{line} {self.fix}".rstrip() if self.fix else line


@dataclass(frozen=True)
class Delivered:
    """Every module's outcome, and what became of the sandbox."""

    outcomes: list[Outcome]
    sandbox: str

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)


@dataclass(frozen=True)
class Refused:
    """The whole delivery was refused before any module was touched."""

    why: str


def _run(
    args: list[str],
    cwd: Path,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one git command.

    ⚠ **Every caller passes a literal `["git", ...]` list**, never a helper
    that prepends "git": `tests/test_blast_radius.py` finds git verbs by
    enumerating those lists, and a `["git", *args]` wrapper would hide every
    verb in this file from it."""
    # Host-side git in a Manager-writable repo: fsmonitor and hooks forced
    # off, whatever the repo's own config says (SCRUM-75; `githost`).
    from rite_ai.githost import hardened_git_env

    return subprocess.run(
        args,
        cwd=cwd,
        input=stdin,
        env=hardened_git_env(env),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )


def _said(done: subprocess.CompletedProcess[str]) -> str:
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[-1].strip() if lines else f"exit {done.returncode}"


def _branch_ok(ticket: str) -> bool:
    done = subprocess.run(
        ["git", "check-ref-format", "--branch", ticket],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    return done.returncode == 0 and done.stdout.strip() == ticket


def _sha(repo: Path, ref: str) -> str | None:
    done = _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo)
    return done.stdout.strip() if done.returncode == 0 else None


def _contains(repo: Path, sha: str) -> bool:
    """Is commit `sha` reachable from some ref in `repo`? Reachability, not
    presence: an object can sit in a repository unreferenced and be pruned."""
    done = _run(["git", "for-each-ref", "--contains", sha, "--format=%(refname)"], repo)
    return done.returncode == 0 and bool(done.stdout.strip())


def collected(clone: Path, project: Path) -> bool:
    """Is every local branch of `clone` saved: on one of its remotes, or
    reachable in the project's checkout `project`? False whenever it cannot
    tell. `sandbox` asks this before destroying a copy (design §2)."""
    done = _run(["git", "for-each-ref", "refs/heads", "--format=%(objectname)"], clone)
    if done.returncode != 0:
        return False
    for sha in done.stdout.split():
        on_remote = _run(["git", "branch", "-r", "--contains", sha], clone)
        if on_remote.returncode != 0:
            return False
        if on_remote.stdout.strip():
            continue
        if not _contains(project, sha):
            return False
    return True


def _squash_message(project: Path, base_sha: str, full: str, ticket: str) -> str:
    """A default message for the whole change: amendable, never final."""
    done = _run(
        ["git", "log", "--reverse", "--format=%s", f"{base_sha}..{full}"], project
    )
    subjects = [s for s in done.stdout.splitlines() if s.strip()]
    body = "\n".join(f"- {s}" for s in subjects)
    # rite builds this commit itself, so no hook sees it: credited here.
    from rite_ai.publishing.attribution import credit

    return credit(f"{ticket}: {len(subjects)} commit(s), squashed by rite\n\n{body}\n")


def _collect(
    clone: Path, project: Path, module: Module, ticket: str, squash: bool
) -> Outcome | str:
    """Bring `<ticket>` from `clone` into `project`. The collected branch
    name on success, else an Outcome saying why not."""

    def no(why: str, fix: str = "") -> Outcome:
        return Outcome(module.name, ticket, False, why, fix)

    if not (project / ".git").exists():
        return no(
            f"{module.path} is not a git checkout",
            f"Check out module {module.name} at {module.path}",
        )
    base = _sha(project, f"refs/heads/{module.branch}")
    if base is None:
        return no(
            f"{module.path} has no local branch {module.branch}",
            f"Run `git fetch` there and create {module.branch}",
        )
    tip = _sha(clone, f"refs/heads/{ticket}")
    if tip is None:
        return no(
            f"the Worker made no branch {ticket}",
            "Ask the Worker to commit its work on a branch named for the ticket",
        )
    target = f"{ticket}-unsquashed" if squash else ticket
    fetched = _run(
        ["git", "fetch", "--no-tags", str(clone), f"{ticket}:{target}"], project
    )
    if fetched.returncode != 0:
        return no(
            f"git would not update {target} in {module.path}: {_said(fetched)}",
            f"Switch {module.path} off {target}, or rename that branch, then "
            "deliver again",
        )
    ahead = _run(["git", "rev-list", "--count", f"{module.branch}..{target}"], project)
    if ahead.returncode != 0:
        return no(f"could not count {ticket}'s commits: {_said(ahead)}")
    if ahead.stdout.strip() == "0":
        return no(
            f"{ticket} has no commits beyond {module.branch}",
            "Nothing to deliver; ask the Worker whether it committed",
        )
    if not squash:
        return ticket
    merge_base = _run(["git", "merge-base", module.branch, target], project)
    if merge_base.returncode != 0:
        return no(f"{ticket} shares no history with {module.branch}")
    parent = merge_base.stdout.strip()
    message = _squash_message(project, parent, target, ticket)
    made = _run(
        ["git", "commit-tree", f"{target}^{{tree}}", "-p", parent, "-F", "-"],
        project,
        stdin=message,
    )
    if made.returncode != 0:
        return no(f"could not squash: {_said(made)}")
    squashed = made.stdout.strip()
    existing = _sha(project, f"refs/heads/{ticket}")
    if existing is not None and existing != squashed:
        # Never moved by force: a branch someone may be reworking.
        return no(
            f"branch {ticket} already exists in {module.path} and differs; "
            f"the full history is on {target}",
            f"Rename or remove {ticket} there, then deliver again",
        )
    if existing is None:
        created = _run(["git", "branch", ticket, squashed], project)
        if created.returncode != 0:
            return no(f"could not create {ticket}: {_said(created)}")
    return ticket


def _remote_environment(root: Path, worker: str, module: Module, config):
    """The environment rite pushes in: the Worker's resolved GitHub token and
    the same git settings a sandboxed push uses (`sandbox_git_environment`:
    the credential helper reset to `gh`, SSH origins rewritten, the
    repository's own hooks, so rite's pre-push gate still runs). A dict, or
    why it cannot be built."""
    import os
    import shutil

    from rite_ai.sandbox import (
        owner_repo_from_url,
        resolve_worker_token,
        sandbox_git_environment,
    )

    env = dict(os.environ)
    if not (module.url and owner_repo_from_url(module.url)):
        return env  # a non-GitHub origin: the operator's own git config
    token, _tier = resolve_worker_token(worker, config.credentials)
    if not token:
        from rite_ai.credentials.services import how_to_set

        return (
            f"no GitHub token for {worker} "
            f"(`rite credential set {how_to_set('github_token')}`)"
        )
    gh = shutil.which("gh")
    env.update(sandbox_git_environment(gh))
    env["GITHUB_TOKEN"] = token
    env["GH_TOKEN"] = token
    return env


def _publish(
    root: Path,
    worker: str,
    module: Module,
    ticket: str,
    branch: str,
    strategy: str,
    config,
) -> Outcome:
    """Push, or push and open a pull request, after the gate passes (PB1
    piece 4). Called only with the work already collected, so a refusal here
    loses nothing: it is on `branch` in the project's checkout."""
    from rite_ai.gate.gate import EXIT_CLEAN, brief, run_gate

    project = root / module.path
    home = f"committed locally on {branch} in {module.path}"

    def no(why: str, fix: str) -> Outcome:
        return Outcome(module.name, ticket, False, f"{why}; {home}, not pushed", fix)

    # 🔴 The floor the model cannot talk past: nothing leaves unless rite's
    # publish gate passed on exactly the commits being sent. "Could not
    # check" (EXIT_ERROR: a scanner missing) is not a pass.
    # Scanned at the module repository, with the PROJECT's rules: `root` is
    # where `.rite/` lives and `project` is the repository beneath it, and
    # passing one path for both either scans a tree git cannot answer about
    # (🔴 SCRUM-60) or drops every suppression the project declared.
    rng = f"{module.branch}..{branch}"
    _record_scope_budget(root, worker, module, ticket, project, rng, config)
    # Full ref names, so no tag or other ref of the same name is what is
    # gated; the branch is a checked name (`parse.branch_problem`, SCRUM-59).
    report = run_gate(
        project,
        rev_range=f"refs/heads/{module.branch}..refs/heads/{branch}",
        config_root=root,
    )
    if report.exit_code != EXIT_CLEAN:
        # 🔴 SCRUM-59: the findings themselves, in the note. "Run `rite
        # publish check`" was a host command a sandboxed Manager cannot run,
        # so a refused delivery told it what to do and not what was wrong.
        found = "; ".join(brief(report, 10))
        return no(
            f"the publish gate did not pass (exit {report.exit_code}): {found}",
            "Have the Worker fix what it names and commit it, then ask for the "
            "delivery again: the delivery collects the fix and gates it. "
            f"`rite request gate {worker} --ticket {ticket}` re-reads what was "
            f"last collected, on {branch} (from the host: `rite publish check "
            f"--rev-range {rng}` in {module.path})",
        )
    env = _remote_environment(root, worker, module, config)
    if isinstance(env, str):
        return no(env, "Set it, then ask for the delivery again")

    if strategy == "push":
        # No force: a moved branch is rejected by the remote, and rite never
        # rebases, because a rebased tree is not the one that was verified.
        pushed = _run(
            ["git", "push", "--porcelain", "origin", f"{branch}:{module.branch}"],
            project,
            env=env,
        )
        if pushed.returncode != 0:
            return no(
                f"origin refused the push to {module.branch}: {_said(pushed)}",
                f"If {module.branch} moved, ask a Worker to update {branch} from "
                "it, then deliver again",
            )
        return Outcome(module.name, ticket, True, f"{home}; pushed to {module.branch}")

    # Checked BEFORE the push: a branch pushed to someone else's repository
    # has already gone upstream, whatever happens to the pull request.
    refusal = _pull_request_target_refusal(project, module, env)
    if refusal:
        return no(*refusal)
    pushed = _run(
        ["git", "push", "--porcelain", "origin", f"{branch}:{branch}"],
        project,
        env=env,
    )
    if pushed.returncode != 0:
        return no(
            f"origin refused branch {branch}: {_said(pushed)}",
            f"Rename or remove {branch} on origin, then deliver again",
        )
    return _open_pull_request(project, module, ticket, branch, env, home)


def _pull_request_target_refusal(
    project: Path, module: Module, env: dict
) -> tuple[str, str] | None:
    """Why rite must not push a branch and open a pull request for this
    module, as (why, fix), or None.

    ⚠ **rite opens a pull request only as a DRAFT, on a repository the
    operator owns, against its default branch** (Robert, 2026-09-29: rite
    opens PRs on his fork; he takes them upstream himself). A property of this
    code, not an instruction: the owner of the token rite pushes with must
    own both the repository the branch is pushed to (`origin`) and the one
    the pull request is opened on (the module's URL), and the base must be
    that repository's default branch. Anything rite cannot establish — the
    token's owner, the default branch — refuses: "could not check" is not
    "allowed"."""
    from rite_ai.sandbox import owner_repo_from_url

    target = owner_repo_from_url(module.url or "")
    if target is None:
        return None  # `_open_pull_request` refuses it, after nothing is pushed
    origin = _run(["git", "remote", "get-url", "origin"], project, env=env)
    pushed_to = (
        owner_repo_from_url(origin.stdout.strip()) if origin.returncode == 0 else None
    )
    if pushed_to is None:
        return (
            f"could not read which GitHub repository {module.name}'s origin is "
            f"({_said(origin)}), so could not check it is yours",
            "Point origin at your fork, then deliver again",
        )
    me = _run(["gh", "api", "user", "--jq", ".login"], project, env=env)
    login = me.stdout.strip() if me.returncode == 0 else ""
    if not login:
        return (
            f"could not establish whose GitHub token rite pushes with "
            f"({_said(me)}), so could not check the repository is theirs",
            "Check the project's github_token, then deliver again",
        )
    for owner, repo in dict.fromkeys([pushed_to, target]):
        if owner.lower() != login.lower():
            return (
                f"{owner}/{repo} is not {login}'s, and rite pushes branches and "
                "opens pull requests only on a repository the operator owns",
                "Point this module (its origin and its url) at your fork; you "
                "open the pull request to the upstream yourself",
            )
    slug = "/".join(target)
    default = _run(
        ["gh", "api", f"repos/{slug}", "--jq", ".default_branch"], project, env=env
    )
    base = default.stdout.strip() if default.returncode == 0 else ""
    if not base:
        return (
            f"could not read {slug}'s default branch ({_said(default)})",
            "Check the token can read the repository, then deliver again",
        )
    if module.branch != base:
        return (
            f"{module.name}'s branch is {module.branch}, not {slug}'s default "
            f"branch {base}, and rite opens pull requests only against that",
            f"Set {module.name}'s branch to {base}, or open this one yourself",
        )
    return None


def _open_pull_request(
    project: Path, module: Module, ticket: str, branch: str, env: dict, home: str
) -> Outcome:
    """`gh pr create --draft`, or the pull request already open for `branch`.
    Only after `_pull_request_target_refusal` passed."""
    import json
    import tempfile

    from rite_ai.sandbox import owner_repo_from_url

    def no(why: str, fix: str) -> Outcome:
        return Outcome(module.name, ticket, False, f"{why}; {home}, pushed", fix)

    repo = owner_repo_from_url(module.url or "")
    if repo is None:
        return no(
            f"{module.name}'s origin is not on GitHub, so rite cannot open a pull "
            "request there",
            "Open it by hand, or set this module's strategy to push or commit",
        )
    slug = "/".join(repo)
    existing = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        project,
        env=env,
    )
    try:
        found = json.loads(existing.stdout) if existing.returncode == 0 else None
    except ValueError:
        found = None
    if found is None:
        return no(
            f"could not ask GitHub for an open PR from {branch}: {_said(existing)}",
            "Check the token's pull_requests permission, then deliver again",
        )
    head = _sha(project, f"refs/heads/{branch}") or ""
    if found:
        return Outcome(
            module.name,
            ticket,
            True,
            f"{home}; pushed; PR {found[0]['url']}",
            pr=int(found[0]["number"]),
            repo=slug,
            head=head,
        )
    log = _run(
        ["git", "log", "--reverse", "--format=- %s", f"{module.branch}..{branch}"],
        project,
    )
    body = (
        f"Delivered by rite for {ticket} (`rite deliver`).\n\n"
        f"Commits:\n{log.stdout}\n"
        "Nothing merges this PR but the User, or rite's own gate when "
        "`publish.auto_merge` is on.\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name
    try:
        made = _run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                slug,
                "--base",
                module.branch,
                "--head",
                branch,
                "--draft",
                "--title",
                f"{ticket}",
                "--body-file",
                body_file,
            ],
            project,
            env=env,
        )
    finally:
        Path(body_file).unlink(missing_ok=True)
    if made.returncode != 0:
        return no(
            f"gh could not open the pull request: {_said(made)}",
            "Open it by hand from the pushed branch, or deliver again",
        )
    url = (made.stdout.strip().splitlines() or [""])[-1]
    number = url.rstrip("/").rsplit("/", 1)[-1]
    return Outcome(
        module.name,
        ticket,
        True,
        f"{home}; pushed; PR {url}",
        pr=int(number) if number.isdigit() else None,
        repo=slug,
        head=head,
    )


def _changed_key(then: dict, now: dict) -> tuple[str, str]:
    """`('squash on', 'squash off')` for the first key that differs, when
    the strategy itself did not change."""
    for key in ("strategy", "squash", "auto_merge"):
        if then.get(key) != now.get(key):

            def said(v: object) -> str:
                return f"{key} {'on' if v is True else 'off' if v is False else v}"

            return said(then.get(key)), said(now.get(key))
    return "settings", "settings"


def _record_scope_budget(
    root: Path,
    worker: str,
    module: Module,
    ticket: str,
    project: Path,
    rev_range: str,
    config,
) -> None:
    """Measure the delivery's size and record it (SCRUM-65 part 2).

    Shadow mode: nothing reads the verdict, so a HOLD cannot refuse a
    delivery. A failure records the event with `unmeasured` set rather than
    nothing, because an absent event is indistinguishable from a PASS and the
    whole point is counting how often this would have held.
    """
    from rite_ai.publishing.scope_budget import Budget
    from rite_ai.reporting import events

    def record(budget) -> None:
        events.record(
            root,
            "scope-budget",
            worker=worker,
            ticket=ticket,
            module=module.name,
            **budget.note(),
        )

    try:
        from rite_ai.publishing.scope_budget import measure

        scope = config.scope
        dod_paths, items = _dod_scope(root, config, ticket)
        if not dod_paths:
            # No paths to judge against: every file would read as out of
            # scope, which is a hold invented from an absent board read.
            record(Budget(unmeasured=f"no definition-of-done paths for {ticket}"))
            return
        record(
            measure(
                project,
                rev_range,
                dod_paths=dod_paths,
                exclude=list(scope.exclude),
                items=items,
                lines_per_item=scope.lines_per_item,
                factor=scope.factor,
            )
        )
    except Exception as e:  # noqa: BLE001 - a measurement must not fail a delivery
        try:
            record(Budget(unmeasured=f"{type(e).__name__}: {e}"))
        except Exception:  # noqa: BLE001
            return


def _dod_scope(root: Path, config, ticket: str) -> tuple[set[str], int]:
    """Paths the agreed definition of done names, and how many items it has."""
    from rite_ai.refinement.status import of

    record = of(root, config, ticket).record
    if record is None:
        return set(), 1
    text = "\n".join(record.items)
    # A path with a separator. A bare basename is deliberately not a path:
    # `_in_scope` matches whole segments, so a basename could only ever match
    # a file at the repository root.
    found = {p.rstrip(".") for p in re.findall(r"[\w.-]+(?:/[\w.-]+)+", text)}
    return found, max(1, len(record.items))


def _release_claims(root: Path, worker: str) -> str:
    """Release `worker`'s claims the way `rite release --worker` does, and
    say what happened. A failure is said, never swallowed: a claim left held
    blocks the next Worker on those paths."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.coordination.identity import claims_channel

    try:
        layer, machine = claims_channel(root)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        n = ledger.release(worker, None, layer=layer, machine=machine)
    except Exception as e:  # noqa: BLE001 — said, not swallowed
        return f"its claims were NOT released ({type(e).__name__}: {e})"
    return f"released {n} claim(s)"


def _worker_modules(root: Path, worker: str, modules: list[Module]) -> list[Module]:
    from rite_ai.config.parse import ParseError, parse_worker

    manifest = parse_worker(root / "workers" / worker / "worker.yml")
    if isinstance(manifest, ParseError):
        raise ValueError(f"worker.yml: {manifest.message}")
    wanted = set(manifest.modules)
    return [m for m in modules if m.name in wanted]


def _host_measurement_hold(root: Path, started) -> list[str]:
    """Why this Worker's work must not be published yet (S31), or [].

    Read from the refinement record the Worker was STARTED on (the publish
    snapshot), never from the board now: that is the definition of done the
    work was done to. A start with no snapshot, or a record with no
    host-measured items, holds nothing. One that has them holds until each
    has a genuine PASS (`refinement.measurement`), and fails closed: a
    snapshot that does not verify, or no key to verify with, holds.
    """
    if not isinstance(started, publish_record.Record):
        return []
    payload = started.refinement
    if not isinstance(payload, dict) or not payload.get("host_measured"):
        return []
    from rite_ai.refinement import key as refinement_key
    from rite_ai.refinement import measurement
    from rite_ai.refinement import record as rec

    key = refinement_key.load().key
    if key is None:
        return [
            "rite cannot read its refinement key here, so no host measurement "
            "can be verified"
        ]
    if not rec.mac_verifies(payload, key):
        return [
            "the refinement record this Worker was started on does not verify, "
            "so which items the host measures cannot be trusted"
        ]
    return measurement.holds(root, rec.from_payload(payload), key)


def deliver(
    root: Path,
    worker: str,
    ticket: str | None = None,
    *,
    by_user: bool = False,
    manager: str = "",
) -> Delivered | Refused:
    """Deliver `worker`'s finished task. See the module docstring."""
    from rite_ai.config.parse import ParseError, parse_config, parse_modules
    from rite_ai.names import name_problem
    from rite_ai.sandbox import (
        _sandbox_copy,  # noqa: PLC2701
        destroy_worker,
        existing_sandbox_name,
        stop_worker,
        worker_sandbox_status,
    )

    root = Path(root)
    problem = name_problem(worker, kind="worker name")
    if problem:
        return Refused(problem)
    if not (root / "workers" / worker / "worker.yml").is_file():
        return Refused(f"no Worker named {worker!r} in this project")

    started = publish_record.read(root, worker)
    if ticket is None:
        if not isinstance(started, publish_record.Record):
            return Refused(
                f"rite has no record of which ticket {worker} was started on; "
                f"name it: rite deliver {worker} --ticket <id>"
            )
        ticket = started.ticket
    if (
        isinstance(started, publish_record.Record)
        and started.ticket != ticket
        and not by_user
    ):
        return Refused(
            f"{worker} was started on {started.ticket}, not {ticket}; nothing delivered"
        )
    if not _branch_ok(ticket):
        return Refused(f"{ticket!r} cannot be a git branch name")

    all_modules = parse_modules(root / ".rite" / "modules.yaml")
    if isinstance(all_modules, ParseError):
        # Without it rite cannot know where each module's checkout is, so
        # there is nowhere safe to collect into.
        return Refused(f"modules.yaml cannot be read: {all_modules.message}")
    config = parse_config(root / ".rite" / "config.yaml")
    if isinstance(config, ParseError):
        if by_user:
            return Refused(
                "config.yaml cannot be read, so rite cannot tell which strategy "
                "is in force; `rite doctor` shows why"
            )
        # A Manager's request still collects: "cannot read the settings now"
        # is divergence, and divergence is commit-only, never "unchanged".
        config = None
    try:
        modules = _worker_modules(root, worker, all_modules)
    except ValueError as e:
        return Refused(str(e))
    if not modules:
        return Refused(f"{worker} has no module in this project's modules.yaml")
    from rite_ai.config.parse import module_dir_problem

    for module in modules:
        problem = module_dir_problem(root, module.path)
        if problem:
            return Refused(f"module {module.name}: {problem}")

    status = worker_sandbox_status(worker, root)
    if not status.known:
        return Refused(f"rite could not ask yoloAI about {worker}'s sandbox: {status}")
    if status.value == "not found":
        return Refused(f"{worker} has no sandbox, so no work waits in one")
    # Stopped FIRST, so the Worker cannot commit between the collect and
    # anything that later trusts it (the destroy guard's pattern).
    stopped = stop_worker(worker, root)
    if not stopped.ok:
        return Refused(f"could not stop {worker}'s sandbox: {stopped.message}")
    copy = _sandbox_copy(existing_sandbox_name(worker, root), root / "workers" / worker)
    if copy is None:
        return Refused(f"could not find the sandbox's copy of workers/{worker}/")

    from rite_ai.workspace import git_ops

    held = _host_measurement_hold(root, started)
    outcomes: list[Outcome] = []
    applied: set[str] = set()
    for module in modules:
        now = effective(config.publish, module) if config is not None else None
        then = (
            started.modules.get(module.name)
            if isinstance(started, publish_record.Record)
            else None
        )
        diverged = not by_user and (now is None or then != publish_record.settings(now))
        strategy = "commit" if diverged or now is None else now.strategy
        squash = False if diverged or now is None else now.squash

        applied.add(strategy)
        refusal = _unavailable(strategy)
        if refusal:
            outcomes.append(Outcome(module.name, ticket, False, refusal))
            continue
        clone = copy / module.name
        if not git_ops.is_git_repo(clone):
            outcomes.append(
                Outcome(
                    module.name,
                    ticket,
                    False,
                    f"the sandbox holds no checkout of {module.name}",
                )
            )
            continue
        dirty = git_ops.uncommitted_paths(clone)
        if not isinstance(dirty, list) or dirty:
            shown = ", ".join(dirty[:3]) if isinstance(dirty, list) else dirty.message
            outcomes.append(
                Outcome(
                    module.name,
                    ticket,
                    False,
                    f"uncommitted in the sandbox: {shown}",
                    "Ask the Worker to commit or discard it; nothing was taken",
                )
            )
            continue
        got = _collect(clone, root / module.path, module, ticket, squash)
        if isinstance(got, Outcome):
            outcomes.append(got)
            continue
        where = f"committed locally on {got} in {module.path}"
        if diverged:
            was = then["strategy"] if isinstance(then, dict) else "unrecorded"
            is_now = now.strategy if now is not None else "unreadable"
            if now is not None and then is not None and was == is_now:
                was, is_now = _changed_key(then, publish_record.settings(now))
            outcomes.append(
                Outcome(
                    module.name,
                    ticket,
                    False,
                    f"publish changed {was}→{is_now} since the Worker "
                    f"started; {where} only",
                    f"Ask the User to run: rite deliver {worker}",
                )
            )
        elif strategy in PUSHES and held:
            # S31: collected, so the host has the work to measure, and not
            # published until every host-measured item has a genuine PASS.
            outcomes.append(
                Outcome(
                    module.name,
                    ticket,
                    False,
                    f"held for the host measurement: {'; '.join(held)}; "
                    f"{where}, not pushed",
                    f"Measure it on the host, record it with `rite refine "
                    f"measured {ticket} --item <n> --result pass --output "
                    "<file>`, then ask for the delivery again",
                )
            )
        elif strategy in PUSHES:
            outcomes.append(
                _publish(root, worker, module, ticket, got, strategy, config)
            )
        else:
            outcomes.append(
                Outcome(module.name, ticket, True, f"{where}; nothing was pushed")
            )

    # The copy goes only when EVERY module was delivered, and through the
    # ordinary guarded destroy: its own check (now counting collected
    # commits as saved) and its refusal for a Worker's unanswered question
    # both still apply. Never `force`. A divergence keeps it too, so the
    # User's `rite deliver` has the sandbox to deliver from.
    released = ""
    if outcomes and all(o.ok for o in outcomes) and applied == {"commit"}:
        # D-41 holds a claim until the work lands where it goes. Under
        # `commit` that is now: it is on the project's own branch. Under a
        # push strategy it is the merge, so the claim stays held.
        released = _release_claims(root, worker)
    if outcomes and all(o.ok for o in outcomes):
        gone = destroy_worker(worker, root)
        if gone.ok:
            from rite_ai.managers.lifecycle import forget_owner

            forget_owner(root, worker)
        sandbox = (
            f"sandbox removed; {worker} can start its next ticket"
            if gone.ok
            else f"sandbox kept: {gone.message.splitlines()[0]}"
        )
    else:
        sandbox = "sandbox kept, stopped, so nothing in it is lost"
    if released:
        sandbox = f"{sandbox}; {released}"

    # Every pull request opened or found is watched from now on (piece 5):
    # its merge releases the claims, and `auto_merge` may merge it.
    from rite_ai.publishing import merging

    for outcome in outcomes:
        if outcome.pr is not None:
            then = (
                started.modules.get(outcome.module, {})
                if isinstance(started, publish_record.Record)
                else {}
            )
            try:
                merging.watch(
                    root,
                    merging.Watched(
                        worker=worker,
                        ticket=ticket,
                        module=outcome.module,
                        repo=outcome.repo,
                        number=outcome.pr,
                        head=outcome.head,
                        manager=manager,
                        auto_merge_at_start=then.get("auto_merge") is True,
                    ),
                )
            except OSError as e:
                # Said on the outcome: an unwatched PR is one whose merge
                # never releases the claims and that auto_merge never sees.
                outcomes[outcomes.index(outcome)] = Outcome(
                    outcome.module,
                    outcome.ticket,
                    False,
                    f"{outcome.why}, but rite cannot watch it ({e})",
                    "It must be merged, and the claims released, by hand",
                )

    from rite_ai.reporting import events

    events.record(
        root,
        "delivered",
        worker=worker,
        ticket=ticket,
        by_user=by_user,
        outcomes=[o.note() for o in outcomes],
        sandbox=sandbox,
    )
    return Delivered(outcomes, sandbox)
