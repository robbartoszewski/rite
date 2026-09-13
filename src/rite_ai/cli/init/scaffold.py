"""Write `.rite/` and its contents from a resolved set of init answers.

Separated from prompting on purpose (SPEC.md §9.2, "Separate prompting from
file writing" — the Yeoman convention): everything in this module is a pure
function of already-collected data, so it can be tested without a terminal.
"""

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from rite_ai.config.models import Module, ProjectBrief, ProjectConfig
from rite_ai.context.manage import CONTEXT_INDEX_TEMPLATE as CONTEXT_INDEX
from rite_ai.gate.ci import (
    CI_WORKFLOW_MARKER,
    CI_WORKFLOW_REL_PATH,
    is_rite_workflow,
    workflow_runs_gate,
)
from rite_ai.gate.hook import HOOK_MARKER as PRE_PUSH_MARKER
from rite_ai.gate.hook import PRE_PUSH_HOOK_SCRIPT as PRE_PUSH_HOOK
from rite_ai.gate.hook import _is_rite_installed as _is_rite_pre_push_hook
from rite_ai.gate.hook import redirected_hooks_dir

from .paths import templates_dir
from .questionnaire import KbAnswers

# CONTEXT_INDEX is re-exported from `rite_ai.context.manage` — this module
# used to define its own divergent copy (different prose, different
# divider width) from the one `context/manage.py`'s `_append_index_row`
# fell back to when creating the file from scratch; the two had already
# drifted apart and, worse, a row appended via the OTHER path landed after
# this module's explanatory prose instead of inside the table. One
# template now, owned by the domain module.

# PRE_PUSH_MARKER / PRE_PUSH_HOOK are re-exported from `rite_ai.gate.hook` (Stage
# 2 #3, implementation-plan) — this module used to define its own divergent
# marker and script, wired by `rite init`, while `rite_ai.gate.hook` defined a
# second one wired by `python -m rite_ai.gate install-hook`. ONE script and ONE
# marker now, imported here rather than duplicated, so `rite init`'s
# scaffold and the standalone module path can never diverge again.


def brief_to_yaml(brief: ProjectBrief) -> str:
    data = {
        "project": {
            "name": brief.name,
            "role": brief.role,
            "root_branch": brief.root_branch,
        },
        "what": {
            "kind": brief.kind,
            "features": brief.features,
        },
        "technology": {
            "platform": brief.platform,
            "languages": brief.languages,
            "frameworks": brief.frameworks,
            "architecture": brief.architecture,
        },
    }
    if brief.source_path or brief.source_changes:
        data["source"] = {"path": brief.source_path, "changes": brief.source_changes}
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def modules_to_yaml(modules: list[Module]) -> str:
    data = {"modules": {}}
    for m in modules:
        entry = {"path": m.path}
        if m.url:
            entry["url"] = m.url
        entry["branch"] = m.branch
        entry["description"] = m.description
        recorded = {key: value for key, value in asdict(m.commands).items() if value}
        if recorded:
            # Only when something was recorded: `commands: {}` under every
            # module would rewrite every existing modules.yaml to say nothing.
            entry["commands"] = recorded
        data["modules"][m.name] = entry
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def config_to_yaml(config: ProjectConfig) -> str:
    """Serialises every `ProjectConfig` field, not just the ones `rite
    init`'s questionnaire currently collects — `rite schedule set` (and
    anything else that reads-modifies-writes config.yaml) round-trips the
    WHOLE file through `parse_config` → this function, so a section this
    function drops is a section that gets silently deleted from a real
    project's config.yaml the next time anything writes it back."""
    data = {
        "ticket_backend": {
            "type": config.ticket_backend.type,
            "site": config.ticket_backend.site,
            "repo": config.ticket_backend.repo,
            "projects": config.ticket_backend.projects,
            "credential": config.ticket_backend.credential,
        },
        # Committed ON PURPOSE (§10.2): a NAME, never a value. A fresh
        # clone reads which credentials this project needs and what they
        # are called, without a secret ever being in the file — the
        # `.env.example` pattern. `rite credential list` prints the
        # composed account names.
        "credentials": {"namespace": config.credentials.namespace},
        "expertise": {e.name: {"tags": e.tags} for e in config.expertise},
        "publish_gate": {
            "scan_patterns": [asdict(p) for p in config.publish_gate.scan_patterns],
            "gitleaks_config": config.publish_gate.gitleaks_config,
        },
        "spec": {
            "paths": config.spec.paths,
            "convention": config.spec.convention,
        },
        "heartbeat": {
            "interval_minutes": config.heartbeat.interval_minutes,
            "stall_threshold": config.heartbeat.stall_threshold,
        },
        "watchdog": {
            "interval_minutes": config.watchdog.interval_minutes,
        },
        "pool": {
            "coordinator_standby": config.pool.coordinator_standby,
            "warn_threshold": config.pool.warn_threshold,
            "lease_expiry_minutes": config.pool.lease_expiry_minutes,
            "archive_after_minutes": config.pool.archive_after_minutes,
        },
        "sandbox": {
            "enabled": config.sandbox.enabled,
            "backend": config.sandbox.backend,
            "token_permissions": config.sandbox.token_permissions,
            "max_concurrent_workers": config.sandbox.max_concurrent_workers,
        },
        "budget": {
            "weekly_quota_pct": config.budget.weekly_quota_pct,
            "weekly_token_budget": config.budget.weekly_token_budget,
            "week_start_day": config.budget.week_start_day,
        },
        "schedule": {
            "timezone": config.schedule.timezone,
            "windows": [asdict(w) for w in config.schedule.windows],
        },
    }
    return yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def write_brief(rite_dir: Path, brief: ProjectBrief) -> Path:
    path = rite_dir / "brief.yaml"
    path.write_text(brief_to_yaml(brief))
    return path


def write_modules(rite_dir: Path, modules: list[Module]) -> Path:
    path = rite_dir / "modules.yaml"
    path.write_text(modules_to_yaml(modules))
    return path


def write_config(rite_dir: Path, config: ProjectConfig) -> Path:
    path = rite_dir / "config.yaml"
    path.write_text(config_to_yaml(config))
    return path


def write_context_index(rite_dir: Path) -> Path:
    context_dir = rite_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    path = context_dir / "INDEX.md"
    path.write_text(CONTEXT_INDEX)
    return path


# The CI half of SPEC §11.5's "belt and braces". The path, the markers and
# the "does this actually run the gate?" test are NOT defined here — they are
# imported from `rite_ai.gate.ci`, the domain module `rite doctor` also reads
# them from, for exactly the reason the PRE_PUSH re-export above records: the
# last time one of these constants existed in two places, `rite init` and the
# standalone module path shipped divergent markers wired to different
# installers. This module owns GENERATING the workflow; `gate/ci.py` owns
# knowing what one is.

_CI_INSTALL_PLACEHOLDER = "{{RITE_INSTALL_SPEC}}"
# The placeholder the shipped template carries in place of a pip install
# target, substituted at generation time by `render_ci_workflow`. It sits
# inside a quoted shell string, so the template file itself stays valid YAML
# and a broken template is caught by a test rather than in a user's CI.

# Kept in step with `install.sh`'s REPO and its default VERSION —
# tests/test_init_ci_workflow.py reads both back out of that script and fails
# if either drifts.
RITE_REPO_URL = "https://github.com/robbartoszewski/rite.git"

_RELEASE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def rite_install_spec(version: str | None = None) -> str:
    """What the generated workflow should hand `pipx install`.

    NOT `rite-ai`: the distribution is not on PyPI yet (README, "Install"),
    so that step installs nothing — it fails, in the one job SPEC §11.5.1
    calls the load-bearing layer. The tagged git source is the same thing
    `install.sh` installs, pinned the same way and for the same reason: a
    CI job that resolves to whatever `main` says today is not reproducible.

    The test is the version string's SHAPE, not whether the tag exists —
    deliberately, because asking would mean `rite init` making a network
    call to GitHub to write a local file, and failing or hanging when it
    could not. So a release-shaped version pins `v<version>` whether or not
    that tag has been pushed yet; `.docs/PUBLISH_RUNBOOK.md` step 7 is
    where that is checked, and until the tag lands the generated workflow
    fails loudly at install rather than scanning nothing quietly.

    What the shape test does catch is `0.0.0-unknown` — what `__version__`
    reports when it cannot read VERSION at all. There is no plausible tag
    for that, so it falls back to the default branch: a workflow that runs
    against `main` beats one that dies at `git checkout v0.0.0-unknown`.
    """
    if version is None:
        from rite_ai import __version__

        version = __version__
    ref = f"v{version}" if _RELEASE_VERSION_RE.match(version) else "main"
    return f"git+{RITE_REPO_URL}@{ref}"


def render_ci_workflow(version: str | None = None) -> str:
    """The shipped template with its install target substituted in."""
    text = (templates_dir() / "ci" / "publish-gate.yml").read_text()
    return text.replace(_CI_INSTALL_PLACEHOLDER, rite_install_spec(version))


@dataclass
class CiWorkflowResult:
    """What `write_ci_workflow` did, and — separately — whether the publish
    gate actually runs in CI afterwards.

    Two questions, deliberately not one. "Did rite write a file?" and "is
    this project covered?" have different answers in both directions: a
    workflow rite wrote and the user has since edited into a no-op is ours
    and inert; a workflow someone else wrote that runs `rite publish check`
    is not ours and covers them completely. Collapsing the two is how
    `rite init` came to print "you are not uncovered" on the strength of a
    marker in a file it had not read for content — the same shape as the
    `✓ Created .git/hooks/pre-push` it printed for a hook git would never
    read (SPEC §11.5.1). `gate_hook_status` keeps the same split for the
    hook, and for the same reason.

    `status` is one of:
        "written"      — this call created the file
        "already_ours" — a file rite wrote is there; left exactly as it is
        "foreign"      — a file rite did not write is there; left alone
        "unreadable"   — a file is there and could not be read
        "not_a_repo"   — no `.git/`, so no CI to run
        "write_failed" — the file could not be created; `detail` says why
    """

    status: str
    runs_gate: bool = False
    detail: str = ""


def write_ci_workflow(project_root: Path) -> CiWorkflowResult:
    """Write `.github/workflows/publish-gate.yml`, unless something is there.

    SPEC §11.5.1: the pre-push hook is disarmable by the developer's own
    git configuration, without any action by the developer and with no
    signal that it happened, "which makes [CI] the load-bearing one, not
    the backup". The template existed and shipped in the wheel without
    anything ever writing it out, so the user who hit the `core.hooksPath`
    warning — the one the README tells that CI has them covered — had no
    gate at all.

    NEVER overwrites an existing workflow at that path, rite's own
    included. The marker identifies who AUTHORED a file, not who owns its
    current contents, and the first thing a user does to a generated
    workflow is edit it — a self-hosted runner, an extra branch, a required
    secret. Rewriting it would discard that silently while printing a tick.
    `rite publish install-ci --force` is the deliberate way to replace one.

    Returns rather than raises, on the same reasoning as `_install_hook`:
    this runs partway through `rite init`, between the `.rite/` writes and
    `CLAUDE.md`, so an exception here leaves a project initialised enough
    that re-running is refused and incomplete enough to be useless.
    `.github/workflows` being a file, or a checkout being read-only, are
    ordinary states, not unexpected ones.
    """
    if not (project_root / ".git").exists():
        return CiWorkflowResult("not_a_repo")
    path = project_root / CI_WORKFLOW_REL_PATH
    if path.exists():
        try:
            existing = path.read_text()
        except OSError as e:
            # NOT "foreign": that would have `rite init` tell the user the
            # file "was not written by rite", which is an authorship claim
            # nobody checked. Same outcome — leave it alone — different
            # sentence. And `runs_gate` stays False: unread is unknown, and
            # unknown is not coverage.
            return CiWorkflowResult("unreadable", detail=str(e))
        return CiWorkflowResult(
            "already_ours" if is_rite_workflow(existing) else "foreign",
            runs_gate=workflow_runs_gate(existing),
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_ci_workflow())
    except OSError as e:
        return CiWorkflowResult("write_failed", detail=str(e))
    return CiWorkflowResult("written", runs_gate=True)


def write_review_checklist(rite_dir: Path) -> Path:
    src = templates_dir() / "review-checklist.md"
    dest = rite_dir / "review-checklist.md"
    dest.write_text(src.read_text())
    return dest


def write_kb(rite_dir: Path, project_root: Path, kb: KbAnswers) -> tuple[Path, int]:
    """Write `.rite/kb/INDEX.md`, copying authored files in.

    Links are recorded by source only — fetching and summarising is
    `rite kb add` / `rite kb refresh`'s job (SPEC.md §8.7), not init's. A
    fresh clone getting no cache files is expected behaviour, not a gap.
    """
    kb_dir = rite_dir / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / ".cache").mkdir(exist_ok=True)

    rows: list[str] = []
    entry_count = 0

    for link in kb.links:
        rows.append(
            f"| {link} | link | {link} | not yet fetched — run `rite kb refresh` |"
        )
        entry_count += 1

    for file_ref in kb.files:
        src_path = Path(file_ref).expanduser()
        if not src_path.is_absolute():
            src_path = (project_root / src_path).resolve()
        if not src_path.is_file():
            rows.append(
                f"| {file_ref} | file | (not found) | source path did not exist "
                "at init time |"
            )
            entry_count += 1
            continue
        dest = kb_dir / src_path.name
        shutil.copyfile(src_path, dest)
        rows.append(
            f"| {src_path.name} | file | (authored) | copied from `{file_ref}` |"
        )
        entry_count += 1

    table = "\n".join(rows) if rows else "| _(none yet)_ | | | |"
    content = f"""\
# Knowledge Base Index

User-provided reference material (SPEC.md §8.7). Authored files are
committed; fetched link caches live in `.cache/` and are always gitignored.

| Entry | Type | Source | Notes |
|-------|------|--------|-------|
{table}
"""
    index_path = kb_dir / "INDEX.md"
    index_path.write_text(content)
    return index_path, entry_count


# What a project SHARES. SPEC §8's layout is explicit about the split:
# these are authored or generated once by `rite init`, read by everyone on
# the team, and belong in the repo — §8 marks `kb/` "(committed)" and §7
# requires review checklists committed per repo.
AUTHORED_CONFIG = (
    ".rite/brief.yaml",
    ".rite/modules.yaml",
    ".rite/config.yaml",
    ".rite/review-checklist.md",
    ".rite/gitleaks.toml",
    ".rite/gitleaksignore",
    ".rite/.schema_version",
    ".rite/context/",
    ".rite/kb/",
)

# Everything else under `.rite/` is runtime state, and it is ignored by
# DEFAULT rather than by enumeration.
#
# An enumerated list was tried first and had already drifted: it named
# `.rite/handover.json` and `.rite/handovers/` but not `.rite/handover/`,
# the per-worker directory that replaced the first of those, so every
# worker's handover snapshot was landing in git. `.rite/scheduler-last-tick`
# was missing too. Both were added by commits that had no reason to think
# about gitignore, which is the point: a list of things to remember is
# remembered until it isn't. Ignoring the directory and re-including the
# short, stable set of shared files inverts that — a new runtime file is
# ignored because nobody did anything.
#
# Runtime state is meaningful only on the machine that wrote it: a claims
# ledger names this machine's workers, a heartbeat is a local clock
# reading, the outbox is an undelivered queue, `workers/` holds this
# machine's clones of the module repos. Committing any of it puts one
# machine's ephemera in a shared history and conflicts on every pull.
#
# Nothing reads any of it out of git — verified, not assumed: every git
# subprocess in `src/` targets a module repo, repo detection, the publish
# gate or the hooks path, and the cross-machine transport SPEC describes
# for handovers is the Dispatch hub at `~/.rite/dispatch/`, not version
# control.
#
# PHASE 1 ONLY. SPEC §8.11 records why, and what changes: Phase 2's state
# branch makes claims and messages shared coordination state, at which
# point some of this becomes exactly what belongs in version control.
RUNTIME_STATE_IGNORES = (
    ".rite/*",
    "workers/",
)


def gitignore_lines(kb_commit: bool) -> list[str]:
    """The block `rite init` appends, in order. `.rite/*` ignores the
    directory's CONTENTS rather than the directory itself, which is what
    makes the `!` re-includes below legal — git cannot re-include a path
    whose parent directory is itself excluded."""
    lines = ["# rite — runtime state is local (SPEC §8.11; revisit in Phase 2)"]
    # From the constant, not from two string literals repeating it. The
    # constant carried the whole rationale above and had no reader at all:
    # this function — the only thing it exists for — spelled the same two
    # patterns out again three lines below its definition, so editing
    # `RUNTIME_STATE_IGNORES` changed nothing a project ever saw, and the
    # comment explaining why `.rite/*` and not an enumerated list sat on a
    # tuple that was not the list being used.
    lines.extend(RUNTIME_STATE_IGNORES)
    for path in AUTHORED_CONFIG:
        if path == ".rite/kb/" and not kb_commit:
            continue
        lines.append(f"!{path}")
    # Fetched link caches are never committed, whatever the KB answer was.
    lines.append(".rite/kb/.cache/")
    # Re-exclude the lock sidecars, AFTER the re-includes above (last
    # matching pattern wins). `.rite/*` cannot reach these: a `!` re-include
    # brings a directory back WHOLE, and each re-included directory also
    # holds the `<name>.lock` that `rite_ai.state.locked()` flocks —
    # machinery, not knowledge. Measured: `rite init` then `rite context
    # add` then `git add -A` committed `.rite/context/INDEX.md.lock`.
    #
    # Tracking one is not cosmetic. `state.locked()` depends on the sidecar
    # being "created on first use and thereafter only opened — never
    # written, replaced, or unlinked", because a lock file removed while
    # held gives the next acquirer a fresh inode and lets two writers into
    # the critical section at once. Committing it hands that to `git
    # checkout`/`stash`/`pull`/`clean`, none of which know a process is
    # holding it.
    #
    # By shape, not by naming the two directories re-included today: this
    # block's whole argument is that enumerations drift, and a rule naming
    # `context` and `kb` would drift the first time a third is shared.
    lines.append(".rite/**/*.lock")
    return lines


def update_gitignore(project_root: Path, kb_commit: bool) -> None:
    if not (project_root / ".git").exists():
        return
    gitignore = project_root / ".gitignore"
    lines = gitignore_lines(kb_commit)
    existing = gitignore.read_text() if gitignore.exists() else ""
    missing = [line for line in lines if line not in existing]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with gitignore.open("a") as f:
        f.write(prefix + "\n".join(missing) + "\n")


def _install_hook(repo_dir: Path) -> bool:
    hooks_dir = repo_dir / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return False
    # `core.hooksPath` makes git ignore this directory entirely, so a hook
    # written here would report installed and never run. See
    # `rite_ai.gate.hook.redirected_hooks_dir`; `init` warns about the repos
    # this skips rather than counting them as installed.
    if redirected_hooks_dir(repo_dir) is not None:
        return False
    hook_path = hooks_dir / "pre-push"
    if hook_path.exists():
        existing = hook_path.read_text()
        if not _is_rite_pre_push_hook(existing):
            return False  # don't clobber a hand-written hook
    hook_path.write_text(PRE_PUSH_HOOK)
    hook_path.chmod(
        hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
    )
    return True


def install_pre_push_hooks(project_root: Path, modules: list[Module]) -> list[str]:
    """Install the publish-gate pre-push hook (SPEC.md §11.5) wherever a real
    git repo exists — the root, and each module that is its own checkout."""
    installed: list[str] = []
    if _install_hook(project_root):
        installed.append(str(project_root))
    for m in modules:
        module_dir = project_root / m.path
        if _install_hook(module_dir):
            installed.append(str(module_dir))
    return installed


__all__ = [
    "write_brief",
    "write_modules",
    "write_config",
    "write_context_index",
    "write_review_checklist",
    "write_ci_workflow",
    "render_ci_workflow",
    "rite_install_spec",
    "CI_WORKFLOW_REL_PATH",
    "CI_WORKFLOW_MARKER",
    "CiWorkflowResult",
    "RITE_REPO_URL",
    "write_kb",
    "AUTHORED_CONFIG",
    "gitignore_lines",
    "update_gitignore",
    "install_pre_push_hooks",
    "RUNTIME_STATE_IGNORES",
    "PRE_PUSH_MARKER",
    "PRE_PUSH_HOOK",
]
