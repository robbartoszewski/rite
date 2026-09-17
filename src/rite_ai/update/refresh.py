"""Refresh the files rite generated in a project it initialised earlier.

`rite init` refuses an initialised directory, and nothing else ever rewrote
`CLAUDE.md` or `.claude/`, so a project kept the files the rite that created it
wrote — forever. Each release widened the gap: a project initialised on v0.2.0
never received `/spec`, the phase table, or guidance a tester had reported
missing.

The hard constraint is that these files are meant to be edited. So nothing here
overwrites content it cannot show to be rite's own:

- **A marked `CLAUDE.md` section** (see `generated_sections`) is replaced only
  while its text still hashes to its marker. Edited, it is kept and reported.
- **A section with no marker** — every file written before markers existed —
  is adopted, marker added and text unchanged, when it is identical to what
  this version generates. When it instead matches what some RELEASE wrote
  (`section_history`, recorded per release for the sections a release writes
  identically for every project), it is provably rite's own and is refreshed.
  Otherwise nothing can tell an older rite's output from an edit, so it is
  kept and reported, with the difference, and replaced only when named with
  `--take-rite`.
- **A section this version generates that the file lacks** is inserted beside
  its generated neighbours. That adds; it removes nothing.
- **A section rite does not generate** is never touched, nor is the file's
  header, nor a `<!-- rite:spec -->` block (`rite spec add` owns that).
- **A copied command or agent file** is replaced only when its bytes match a
  version some release shipped (`template_history`); a missing one is
  installed; anything else is the user's.

One known cost: a generated section the user deleted on purpose is indistin-
guishable from one that never existed, and is inserted again.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rite_ai.generated_sections import Block, parse, section_hash

KEPT = ("kept-edited", "kept-unknown")

# Everything a refresh may write, and nothing else. `.rite/` is where a
# project's AUTHORED content lives — the brief, modules, config, the context
# and knowledge bases, and whatever architecture, plan or decisions a team
# keeps beside them — and a command that regenerates instructions must not be
# able to touch any of it. `review-checklist.md` is the one generated file in
# there, so it is the one listed. Enforced at every write rather than trusted
# to each call site: a surface added carelessly later fails a test instead of
# eating someone's design notes.
REFRESHABLE = (
    "CLAUDE.md",
    ".claude/agents/*",
    ".claude/commands/*",
    ".rite/review-checklist.md",
    ".github/workflows/publish-gate.yml",
    "workers/*/CLAUDE.md",
    "workers/*/.claude/agents/*",
    "workers/*/.claude/commands/*",
)


def may_refresh(root: Path, path: Path) -> bool:
    """Whether `path` is one of the files rite generates in a project."""
    try:
        rel = path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return any(PurePosixPath(rel.as_posix()).match(pattern) for pattern in REFRESHABLE)


class RefusedWrite(RuntimeError):
    """A refresh tried to write something rite does not generate."""


def _write(root: Path, path: Path, data: bytes | str) -> None:
    if not may_refresh(root, path):
        raise RefusedWrite(
            f"{path} is not a file rite generates — refusing to write it"
        )
    from rite_ai.state import write_atomic

    if isinstance(data, bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    else:
        write_atomic(path, data)


@dataclass
class Change:
    target: str
    action: str
    """`refreshed`, `inserted`, `marked`, `taken`, `installed`, or one of
    `KEPT`."""
    diff: str = ""


@dataclass
class FileResult:
    path: str
    changes: list[Change] = field(default_factory=list)
    note: str = ""
    written: bool = False


def _diff(current: str, rite: str, limit: int = 60) -> str:
    lines = list(
        difflib.unified_diff(
            current.splitlines(), rite.splitlines(), "yours", "rite", n=1, lineterm=""
        )
    )
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more lines"]
    return "\n".join(lines)


def _swap(old: Block, new_raw: str, new: Block) -> Block:
    tail = old.raw[len(old.raw.rstrip("\n")) :] or "\n"
    return Block("section", new.heading, new_raw.rstrip("\n") + tail, new.recorded)


def refresh_text(
    current: str, generated: str, take: frozenset[str] = frozenset()
) -> tuple[str, list[Change]]:
    """`current` brought up to `generated` section by section, and what was
    done. `generated` must carry markers (it is a generator's output)."""
    blocks = parse(current)
    wanted = [b for b in parse(generated) if b.kind == "section"]
    changes: list[Change] = []

    def find(heading: str) -> int | None:
        for i, b in enumerate(blocks):
            if b.kind == "section" and b.heading == heading:
                return i
        return None

    for gi, g in enumerate(wanted):
        i = find(g.heading)
        if i is None:
            pos = None
            for prev in reversed(wanted[:gi]):
                j = find(prev.heading)
                if j is not None:
                    pos = j + 1
                    break
            if pos is None:
                for nxt in wanted[gi + 1 :]:
                    j = find(nxt.heading)
                    if j is not None:
                        pos = j
                        break
            if pos is None:
                pos = len(blocks)
            if pos > 0 and not blocks[pos - 1].raw.endswith("\n\n"):
                blocks[pos - 1].raw = blocks[pos - 1].raw.rstrip("\n") + "\n\n"
            blocks.insert(
                pos,
                Block("section", g.heading, g.raw.rstrip("\n") + "\n\n", g.recorded),
            )
            changes.append(Change(g.heading, "inserted"))
            continue

        c = blocks[i]
        edited = c.recorded is not None and section_hash(c.content) != c.recorded
        if c.recorded is not None and not edited:
            if c.content == g.content and c.recorded == g.recorded:
                continue
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "refreshed"))
        elif c.recorded is None and c.content == g.content:
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "marked"))
        elif c.recorded is None and _written_by_a_release(g.heading, c.content):
            # No marker, but these are bytes a release wrote and nobody has
            # touched: the file predates markers, not the user's attention.
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "refreshed"))
        elif g.heading in take:
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "taken"))
        else:
            action = "kept-edited" if edited else "kept-unknown"
            changes.append(Change(g.heading, action, _diff(c.content, g.content)))

    text = "".join(b.raw for b in blocks)
    if changes and any(ch.action not in KEPT for ch in changes):
        text = text.rstrip("\n") + "\n"
        return text, changes
    return current, changes


def _written_by_a_release(heading: str, content: str) -> bool:
    """Whether `content` is exactly what some tagged release wrote for this
    section — recorded only for sections a release writes identically for
    every project, so this can never mistake one project's rendering for
    another's."""
    from rite_ai.update.section_history import SECTIONS

    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    return digest in SECTIONS.get(heading, frozenset())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_template(
    dest: Path,
    template: Path,
    key: str,
    label: str,
    take: frozenset[str],
    apply: bool,
    root: Path | None = None,
) -> Change | None:
    from rite_ai.update.template_history import RELEASED

    if not dest.exists():
        if apply:
            _write(
                root if root is not None else dest.parent, dest, template.read_bytes()
            )
        return Change(label, "installed")
    if _sha(dest) == _sha(template):
        return None
    if _sha(dest) in RELEASED.get(key, frozenset()):
        action = "refreshed"
    elif label in take or dest.name in take:
        action = "taken"
    else:
        return Change(
            label,
            "kept-edited",
            _diff(
                dest.read_text(errors="replace"), template.read_text(errors="replace")
            ),
        )
    if apply:
        _write(root if root is not None else dest.parent, dest, template.read_bytes())
    return Change(label, action)


def refresh_ci_workflow(root: Path, take: frozenset[str], apply: bool) -> Change | None:
    """The generated CI workflow — rendered, not copied, so "pristine" means
    "what rite would have written at some release".

    Refreshing it also moves the install pin: a workflow written by v0.2.0
    keeps installing v0.2.0 in CI forever, so the gate a project relies on is
    the gate it was initialised with (SPEC §11.5.1 calls CI the load-bearing
    layer). An absent workflow is REPORTED, never installed: `rite init`
    deliberately never overwrites one, and a file someone deleted is a choice.
    """
    from rite_ai.cli.init.scaffold import CI_WORKFLOW_REL_PATH, render_ci_workflow
    from rite_ai.update.template_history import RELEASED

    path = root / CI_WORKFLOW_REL_PATH
    label = CI_WORKFLOW_REL_PATH
    if not (root / ".git").exists():
        # `write_ci_workflow` writes nothing outside a git repository, so
        # there is nothing here to be missing.
        return None
    if not path.exists():
        return Change(label, "absent")
    current = path.read_text(errors="replace")
    rendered = render_ci_workflow()
    if current == rendered:
        return None
    digest = hashlib.sha256(current.encode()).hexdigest()
    if digest in RELEASED.get("ci/publish-gate.yml", frozenset()):
        action = "refreshed"
    elif label in take or path.name in take:
        action = "taken"
    else:
        return Change(label, "kept-edited", _diff(current, rendered))
    if apply:
        _write(root, path, rendered)
    return Change(label, action)


def _refresh_claude_md(
    root: Path,
    path: Path,
    generated: str,
    marker: str,
    take: frozenset[str],
    apply: bool,
) -> FileResult:
    result = FileResult(str(path.relative_to(root)))
    if not path.is_file():
        result.note = "not found"
        return result
    text = path.read_text()
    if marker not in text:
        result.note = "not generated by rite — left untouched"
        return result
    new, result.changes = refresh_text(text, generated, take)
    if apply and new != text:
        _write(root, path, new)
        result.written = True
    return result


def refresh_project(
    root: Path, take: frozenset[str] = frozenset(), apply: bool = True
) -> list[FileResult]:
    from rite_ai.cli.init.claude_gen import (
        _AGENT_FILES,
        _COMMAND_FILES,
        GENERATED_MARKER,
        generate_claude_md,
    )
    from rite_ai.cli.init.paths import templates_dir
    from rite_ai.config.parse import ParseError, load_project, parse_worker
    from rite_ai.project_spec import WORKER_GENERATED_MARKER
    from rite_ai.workspace.manage import (
        _module_commands_section,
        render_worker_claude_md,
    )

    project = load_project(root)
    if isinstance(project, list):
        return [
            FileResult(
                "CLAUDE.md",
                note="not refreshed — .rite/ config does not parse: "
                + "; ".join(e.message for e in project),
            )
        ]
    src = templates_dir()
    results: list[FileResult] = []

    generated = generate_claude_md(
        project.brief.role, project.brief, project.modules, project.config, root
    )
    results.append(
        _refresh_claude_md(
            root, root / "CLAUDE.md", generated, GENERATED_MARKER, take, apply
        )
    )
    files = FileResult(".claude/")
    for fname in _AGENT_FILES:
        ch = refresh_template(
            root / ".claude" / "agents" / fname,
            src / "agents" / fname,
            f"agents/{fname}",
            f".claude/agents/{fname}",
            take,
            apply,
            root,
        )
        if ch:
            files.changes.append(ch)
    for fname in _COMMAND_FILES:
        ch = refresh_template(
            root / ".claude" / "commands" / fname,
            src / "commands" / fname,
            f"commands/{fname}",
            f".claude/commands/{fname}",
            take,
            apply,
            root,
        )
        if ch:
            files.changes.append(ch)
    checklist = refresh_template(
        root / ".rite" / "review-checklist.md",
        src / "review-checklist.md",
        "review-checklist.md",
        ".rite/review-checklist.md",
        take,
        apply,
        root,
    )
    if checklist:
        files.changes.append(checklist)
    ci = refresh_ci_workflow(root, take, apply)
    if ci:
        files.changes.append(ci)
    results.append(files)

    workers = root / "workers"
    for worker_dir in (
        sorted(p for p in workers.iterdir() if p.is_dir()) if workers.is_dir() else []
    ):
        manifest = parse_worker(worker_dir / "worker.yml")
        rel = f"workers/{worker_dir.name}"
        if isinstance(manifest, ParseError):
            results.append(
                FileResult(
                    f"{rel}/CLAUDE.md", note=f"not refreshed — {manifest.message}"
                )
            )
            continue
        mods = [m for m in project.modules if m.name in manifest.modules]
        wgen = render_worker_claude_md(
            manifest, project.config.spec, _module_commands_section(root, mods)
        )
        results.append(
            _refresh_claude_md(
                root,
                worker_dir / "CLAUDE.md",
                wgen,
                WORKER_GENERATED_MARKER,
                take,
                apply,
            )
        )
        wfiles = FileResult(f"{rel}/.claude/")
        for fname in _AGENT_FILES:
            ch = refresh_template(
                worker_dir / ".claude" / "agents" / fname,
                src / "agents" / fname,
                f"agents/{fname}",
                f"{rel}/.claude/agents/{fname}",
                take,
                apply,
                root,
            )
            if ch:
                wfiles.changes.append(ch)
        ch = refresh_template(
            worker_dir / ".claude" / "commands" / "review.md",
            src / "commands" / "review.md",
            "commands/review.md",
            f"{rel}/.claude/commands/review.md",
            take,
            apply,
            root,
        )
        if ch:
            wfiles.changes.append(ch)
        results.append(wfiles)
    return results


def report(results: list[FileResult], dry_run: bool, take: frozenset[str]) -> list[str]:
    lines: list[str] = []
    verb = {
        "refreshed": "would refresh" if dry_run else "refreshed",
        "inserted": "would add" if dry_run else "added",
        "marked": "would mark (text unchanged)"
        if dry_run
        else "marked (text unchanged)",
        "taken": "would replace with rite's version"
        if dry_run
        else "replaced with rite's version",
        "installed": "would install" if dry_run else "installed",
        "absent": "not there — `rite publish install-ci` writes one",
    }
    kept = []
    matched = set()
    for r in results:
        if r.note:
            lines.append(f"{r.path}: {r.note}")
        for ch in r.changes:
            # A target with a path stands on its own; a bare heading belongs
            # to the file it was found in.
            where = ch.target if "/" in ch.target else f"{r.path} § {ch.target}"
            if ch.action in KEPT:
                kept.append((where, ch))
            else:
                if ch.action == "taken":
                    matched.add(ch.target)
                lines.append(f"{where}: {verb.get(ch.action, ch.action)}")
    for where, ch in kept:
        why = (
            "you edited it since rite wrote it"
            if ch.action == "kept-edited"
            else "it differs from what this version writes — an older rite "
            "wrote it, or you edited it"
        )
        lines.append(f"{where}: left as it is — {why}")
        if dry_run and ch.diff:
            lines.extend("    " + d for d in ch.diff.splitlines())
    for name in sorted(take - matched):
        if not any(
            ch.target == name or ch.target.endswith("/" + name) for _, ch in kept
        ):
            lines.append(f"--take-rite {name!r}: matched nothing that was left alone")
    if kept:
        lines.append(
            "To see each difference: rite update --files-only --dry-run. To take "
            "rite's version of one: rite update --files-only --take-rite "
            '"<section or file>".'
        )
    if not lines:
        lines.append("generated files already current")
    return lines
