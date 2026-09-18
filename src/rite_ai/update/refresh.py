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
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath

from rite_ai.generated_sections import Block, parse, section_hash
from rite_ai.workspace.manage import (
    WORKER_MODULES_HEADING,
    WORKER_MODULES_HEADING_LEGACY,
)

KEPT = ("kept-edited", "kept-unknown")

# Sections a release RENAMED, new heading -> the headings it replaced, most
# recent first. A section is matched to the one in your file by its heading, so
# without this a rename matches nothing: the new section is INSERTED and the
# old one, being a section this version does not generate, is left alone. The
# file comes back carrying both — measured on a Worker `CLAUDE.md` when
# `## Your modules` became `## Modules checked out in your workspace`, which
# put the possessive framing the rename removed directly above the paragraph
# explaining that Workers do not own their modules.
#
# Matching an old heading only decides WHICH section is this one. What may be
# done to it is unchanged: marked and unedited it is rewritten under the new
# heading, otherwise it is kept and reported like any other section whose bytes
# rite cannot account for. Spelt from the constants so a rename that forgets to
# add its entry here fails a test instead of shipping the duplicate.
SUPERSEDED_HEADINGS: dict[str, tuple[str, ...]] = {
    WORKER_MODULES_HEADING.removeprefix("## "): (
        WORKER_MODULES_HEADING_LEGACY.removeprefix("## "),
    ),
}

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
    ".gitignore",
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
    renamed_from: str = ""
    """The heading this section carries in the user's file, when a release
    renamed it. Reported, so `§ <new name>` is findable in a file that does
    not contain that name anywhere."""


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
        for name in (heading, *SUPERSEDED_HEADINGS.get(heading, ())):
            for i, b in enumerate(blocks):
                if b.kind == "section" and b.heading == name:
                    return i
        return None

    for gi, g in enumerate(wanted):
        i = find(g.heading)
        renamed = (
            "" if i is None or blocks[i].heading == g.heading else blocks[i].heading
        )
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
            changes.append(Change(g.heading, "refreshed", renamed_from=renamed))
        elif c.recorded is None and c.content == g.content:
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "marked", renamed_from=renamed))
        elif c.recorded is None and _written_by_a_release(g.heading, c.content):
            # No marker, but these are bytes a release wrote and nobody has
            # touched: the file predates markers, not the user's attention.
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "refreshed", renamed_from=renamed))
        elif g.heading in take:
            blocks[i] = _swap(c, g.raw, g)
            changes.append(Change(g.heading, "taken", renamed_from=renamed))
        else:
            action = "kept-edited" if edited else "kept-unknown"
            changes.append(
                Change(g.heading, action, _diff(c.content, g.content), renamed)
            )

    text = "".join(b.raw for b in blocks)
    if changes and any(ch.action not in KEPT for ch in changes):
        text = text.rstrip("\n") + "\n"
        return text, changes
    return current, changes


def _written_by_a_release(heading: str, content: str) -> bool:
    """Whether `content` is what some tagged release wrote for this section.

    Two ways to know. A section a release writes identically for every project
    is recorded by hash, so a match is exact. A section that is fixed guidance
    around a line or two of the project's own details is recorded as a pattern:
    the lines both probe projects shared, with each differing run recorded as
    the number of lines it stood for. Matching one means every word of that
    release's guidance is present verbatim and only the project-specific runs
    differ — which is what a file written before markers existed cannot
    otherwise prove about itself. A renamed section was recorded under its old
    heading, so those are asked about too.

    The cost, stated plainly because it is the only place this mechanism
    infers rather than proves: an edit made inside one of those
    project-specific runs is not distinguishable from rite's own rendering of
    it, and the refresh will write that run again from the project's config.
    """
    from rite_ai.update.section_history import PATTERNS, SECTIONS

    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
    names = (heading, *SUPERSEDED_HEADINGS.get(heading, ()))
    if any(digest in SECTIONS.get(name, frozenset()) for name in names):
        return True
    return any(
        _matches_pattern(pattern, content.strip())
        for name in names
        for pattern in PATTERNS.get(name, ())
    )


@cache
def _compiled(pattern: tuple[str | int, ...]) -> re.Pattern[str]:
    parts = [
        re.escape(item) + "\n"
        if isinstance(item, str)
        else rf"(?:[^\n]*\n){{0,{item}}}"
        for item in pattern
    ]
    return re.compile("".join(parts))


def _matches_pattern(pattern: tuple[str | int, ...], content: str) -> bool:
    return _compiled(pattern).fullmatch(content + "\n") is not None


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


def refresh_gitignore(root: Path, apply: bool) -> Change | None:
    """Add the ignore lines this rite ships that the project does not have.

    Append-only, through the same function `rite init` uses: rite's block
    grows between releases — `.rite/**/*.lock` arrived after 0.1.0 — and a
    project missing a line tracks runtime state it should not, which is what
    `doctor` reports as "git tracks runtime state" and nothing delivered.
    Nothing is rewritten, reordered or removed, so a user's own entries cannot
    be harmed; a rite line someone deleted on purpose does come back, which is
    the one cost of an append-only rule and the reason it is stated here.

    The knowledge-base answer is read off the file rather than guessed: a
    project that committed its KB carries `!.rite/kb/`, and writing the
    opposite would flip a choice its owner made at init.
    """
    from rite_ai.cli.init.scaffold import gitignore_lines, update_gitignore

    if not (root / ".git").exists():
        return None
    gitignore = root / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    kb_commit = "!.rite/kb/" in existing
    missing = [line for line in gitignore_lines(kb_commit) if line not in existing]
    if not missing:
        return None
    if apply:
        if not may_refresh(root, gitignore):
            raise RefusedWrite(f"{gitignore} is not a file rite generates")
        update_gitignore(root, kb_commit)
    return Change(".gitignore", "extended", "\n".join(f"+{line}" for line in missing))


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
        # Absence is a gap, not a choice. A project or Worker with no
        # CLAUDE.md has no instructions at all, and — unlike the CI workflow,
        # which `rite publish install-ci` owns — nothing else writes one. It
        # is generated content, so it is delivered like any other missing
        # generated file.
        if apply:
            _write(root, path, generated)
        result.changes.append(Change(str(path.relative_to(root)), "installed"))
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


def _withdrawn(
    directory: Path, still_shipped: tuple[str, ...], key: str, label: str
) -> list[Change]:
    """Files a past release wrote here that this version no longer ships.

    Refreshing walks the files THIS version ships, so a command or agent a
    release withdrew is invisible to it: the project keeps offering a Worker
    something nothing maintains, while `rite update` and `rite doctor` both
    call the project current. Reported, never deleted — deleting generated
    files nobody asked about is the one thing a refresh must not do, and the
    file may be one someone still wants.

    A file is only named when its bytes are a release's own, so a command the
    team wrote themselves is never mistaken for rite's leftovers.
    """
    from rite_ai.update.template_history import RELEASED

    if not directory.is_dir():
        return []
    return [
        Change(f"{label}/{path.name}", "withdrawn")
        for path in sorted(directory.glob("*.md"))
        if path.name not in still_shipped
        and _sha(path) in RELEASED.get(f"{key}/{path.name}", frozenset())
    ]


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
    from rite_ai.config.parse import (
        ParseError,
        parse_brief,
        parse_config,
        parse_modules,
        parse_worker,
    )
    from rite_ai.project_spec import WORKER_GENERATED_MARKER
    from rite_ai.workspace.manage import (
        _module_commands_section,
        render_worker_claude_md,
    )

    # Deliberately NOT `load_project`: that aggregates every worker.yml too,
    # so one Worker's typo returned errors for the whole project and stopped
    # the refresh dead — nothing delivered anywhere, under a message blaming
    # `.rite/` config that named a file in `workers/`. The project's own three
    # files are what the project file is rendered from; each Worker is parsed
    # below, on its own, and can fail on its own.
    rite_dir = root / ".rite"
    brief = parse_brief(rite_dir / "brief.yaml")
    modules = parse_modules(rite_dir / "modules.yaml")
    config = parse_config(rite_dir / "config.yaml")
    problems = [p for p in (brief, modules, config) if isinstance(p, ParseError)]
    if problems:
        return [
            FileResult(
                "CLAUDE.md",
                note="not refreshed — .rite/ config does not parse: "
                + "; ".join(e.message for e in problems),
            )
        ]
    assert not isinstance(brief, ParseError)
    assert not isinstance(modules, ParseError)
    assert not isinstance(config, ParseError)
    src = templates_dir()
    results: list[FileResult] = []

    generated = generate_claude_md(brief.role, brief, modules, config, root)
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
    files.changes += _withdrawn(
        root / ".claude" / "agents", _AGENT_FILES, "agents", ".claude/agents"
    )
    files.changes += _withdrawn(
        root / ".claude" / "commands", _COMMAND_FILES, "commands", ".claude/commands"
    )
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
    ignored = refresh_gitignore(root, apply)
    if ignored:
        # Its own file, not one of the `.claude/` ones grouped above.
        results.append(FileResult(".gitignore", changes=[ignored]))

    workers = root / "workers"
    for worker_dir in (
        sorted(p for p in workers.iterdir() if p.is_dir()) if workers.is_dir() else []
    ):
        manifest = parse_worker(worker_dir / "worker.yml")
        rel = f"workers/{worker_dir.name}"
        if isinstance(manifest, ParseError):
            # Only CLAUDE.md is rendered FROM the manifest. The copied
            # commands and agents are byte-for-byte templates, so skipping
            # them here would mean one malformed worker.yml quietly stopping
            # that Worker from receiving any fix at all — the failure this
            # whole command exists to end.
            results.append(
                FileResult(
                    f"{rel}/CLAUDE.md",
                    note=f"not refreshed — {manifest.message}; its copied "
                    "commands and agents still are",
                )
            )
        else:
            mods = [m for m in modules if m.name in manifest.modules]
            wgen = render_worker_claude_md(
                manifest, config.spec, _module_commands_section(root, mods)
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
        wfiles.changes += _withdrawn(
            worker_dir / ".claude" / "agents",
            _AGENT_FILES,
            "agents",
            f"{rel}/.claude/agents",
        )
        wfiles.changes += _withdrawn(
            worker_dir / ".claude" / "commands",
            ("review.md",),
            "commands",
            f"{rel}/.claude/commands",
        )
        results.append(wfiles)
    return results


@dataclass
class Pending:
    """What a refresh would do to a project, without doing any of it."""

    behind: list[Change] = field(default_factory=list)
    """Sections and files this rite would rewrite or add."""
    contested: list[Change] = field(default_factory=list)
    """Sections it would leave alone — edited, or written by an older rite
    and not provably its own. Being behind and being contested are different
    situations with different remedies, so they are counted apart."""
    files: list[str] = field(default_factory=list)
    """The paths `behind` falls in. A file is usually behind in several
    sections at once, so this is the number to say when saying "files":
    `rite start` counted CHANGES and called them files, and a project one
    release out of date announced three files behind when it had one."""

    def __bool__(self) -> bool:
        return bool(self.behind or self.contested)


def pending(root: Path) -> Pending:
    """`refresh_project` with `apply=False`, split the way callers ask about it.

    Three commands ask this question — `rite doctor`, `rite start`, and the
    module commands, which leave the generated module map behind the moment
    they write `.rite/modules.yaml`. It was open-coded twice before there was
    a third, and the two copies had already drifted on what they counted.
    """
    out = Pending()
    for result in refresh_project(root, apply=False):
        for change in result.changes:
            if change.action in KEPT:
                out.contested.append(change)
            else:
                out.behind.append(change)
                if result.path not in out.files:
                    out.files.append(result.path)
    return out


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
        "withdrawn": (
            "written by an older rite that shipped it; this version does not, "
            "so nothing updates it — delete it if you no longer want it"
        ),
        "extended": (
            "would add rite's newer ignore lines"
            if dry_run
            else "added rite's newer ignore lines"
        ),
    }
    kept = []
    matched = set()
    for r in results:
        if r.note:
            lines.append(f"{r.path}: {r.note}")
        for ch in r.changes:
            # A target with a path stands on its own; a bare heading belongs
            # to the file it was found in.
            where = (
                ch.target
                if "/" in ch.target or ch.target == r.path
                else f"{r.path} § {ch.target}"
            )
            if ch.renamed_from:
                # Otherwise the line names a heading the reader cannot find:
                # their file still calls the section something else.
                where += f' (renamed — your file calls it "## {ch.renamed_from}")'
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
        if ch.renamed_from:
            # A rename is not decoration. A release only renames a generated
            # heading when the words were wrong, and the one this mechanism
            # was built on is the reason: `## Your modules` told every Worker
            # it owned a set of modules, and an Owner who read it pre-bound
            # unstarted tickets to named Workers before anyone had started.
            #
            # Such a section is per-project — it lists this project's modules
            # — so no release's bytes are on record for it and a refresh
            # cannot prove it is rite's own. It is kept, correctly. But it is
            # then reported in the same neutral line as every other kept
            # section, and the reader has no way to tell the one carrying a
            # correction from the seven that are merely old. Say which, and
            # give the command, rather than leaving them to infer it from a
            # heading that changed.
            lines.append(
                f"    a later release replaced that heading — its text was "
                f"corrected, not just renamed. To take it:\n      rite update "
                f'--files-only --take-rite "{ch.target}"'
            )
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
