"""Which files a unit's generated instructions are written to (SCRUM-49).

🔴 **Goose does not read `CLAUDE.md`, and rite wrote nothing else.** Measured in
`docs/design/spikes/SCRUM-48-one-agents-md-for-both-engines.md`: Goose 1.51.0
honours a rule in `AGENTS.md` and ignores the same rule in `CLAUDE.md`, and
`CLAUDE.md` appears in its binary zero times. So every local Manager and local
Worker rite has ever started received **no standing project instructions at
all** — not a thinner set: none. The per-cycle prompt
(`managers.prompt.for_manager`) always arrived, which is what kept this from
being total silence; what never arrived was the modules' own docs, the duty and
claim conventions, and everything else the generated body carries.

⚠ **This module exists so that ONE place decides the filenames.** Robert's
instruction for SCRUM-49 was DRY: the `AGENTS.md` body must come from wherever
`CLAUDE.md`'s body already comes from, so that SCRUM-48 — the full migration,
post-v0.7.0 — flips a single source rather than hunting two. Every writer asks
`instruction_files` and writes the body it has already generated to each path it
gets back. No writer decides for itself, and no body is generated twice.

**Claude units are untouched.** They get `CLAUDE.md` and nothing else, exactly as
before, because Claude Code 2.1.261 does not read `AGENTS.md` (same spike, §2) —
so writing one for a Claude unit would add a file nothing reads.
"""

from __future__ import annotations

from pathlib import Path

CLAUDE_MD = "CLAUDE.md"
AGENTS_MD = "AGENTS.md"
"""The name Goose reads. Not a rite invention — it is the cross-tool convention
Goose implements, and rite already recognises it in other people's code
(`workspace.manage.MODULE_DOC_NAMES`, used by `follow_module_docs`)."""


def instruction_files(unit_dir: Path, *, local: bool) -> tuple[Path, ...]:
    """Every path this unit's generated instructions are written to.

    `CLAUDE.md` always, so a person opening the project in Claude Code — and
    every Claude unit — keeps exactly what they had. `AGENTS.md` as well for a
    LOCAL unit, because that is the only one of the two Goose reads.

    ⚠ Returns paths, never content. The caller generates the body ONCE and
    writes the same string to each path, which is the whole point: two files
    that are generated separately are two files that drift.
    """
    paths = [unit_dir / CLAUDE_MD]
    if local:
        paths.append(unit_dir / AGENTS_MD)
    return tuple(paths)


def project_runs_a_local_engine(config: object) -> bool:
    """Whether any Manager in this project runs a local engine.

    The question for the PROJECT-ROOT instructions, which are shared: a local
    Manager runs in the project checkout and reads whatever is there, so the
    root needs an `AGENTS.md` as soon as one Manager is local. A Worker asks its
    own manifest instead (`WorkerManifest.is_local`), because a Worker's
    instructions are its own file.

    Takes `config` loosely and answers False when it cannot tell: a project
    whose config will not parse has bigger problems than a missing second copy
    of its instructions, and guessing True would write a file no engine reads.
    """
    coordination = getattr(config, "coordination", None)
    roles = getattr(coordination, "manager_roles", None) or ()
    return any(getattr(role, "is_local", False) for role in roles)
