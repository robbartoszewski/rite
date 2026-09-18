"""Regenerate `src/rite_ai/update/section_history.py` from every `v*` tag.

Run after tagging a release, like `tools/template_history.py`.

`rite update --files-only` can prove a MARKED section is untouched (its hash is
in the file). A section written before markers existed has no such proof, so
the refresh reports it rather than replacing it — which is right, but it means
a project initialised on an older release never receives improvements to those
sections automatically.

This closes that for the sections a release writes identically for every
project. Each tag's own generator is run — its source is in git — against two
deliberately different projects: different name, role, modules, spec, ticket
backend and expertise. A section whose text is the same in both does not depend
on the project, so its bytes ARE that release's constant, and a file carrying
them was written by that release and not edited.

A section that varies only in part — fixed guidance around a paragraph that
names the project's ticket backend, say — is recorded as a PATTERN instead: the
lines both renders share, with the differing runs left as gaps. A section
matching one of those, gaps and all, is that release's text with this project's
details filled in. Patterns are only kept when the shared lines dominate
(`_MIN_LITERAL_SHARE`), so a section that is mostly project-specific stays
unattributable rather than being guessed at, which is the one thing this whole
mechanism exists to avoid.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "rite_ai" / "update" / "section_history.py"

PROBE = r"""
import json, tempfile
from pathlib import Path
from rite_ai.cli.init.claude_gen import generate_claude_md
from rite_ai.config import models

def build(kind, role):
    brief_fields = {f.name for f in models.ProjectBrief.__dataclass_fields__.values()}
    brief_args = {"name": "acme" if kind == "a" else "zeta", "role": role}
    # Every field a project can differ in has to differ between the two, or a
    # section that depends on it looks constant and gets recorded as one.
    varied = {
        "root_branch": ("main", "trunk"),
        "kind": ("full-stack", "library"),
        "description": ("", "Invoicing for clinics."),
        "technology": ("", "python, postgres"),
        "platform": ("macos", "linux"),
        "architecture": ("", "event sourcing"),
    }
    for field, (first, second) in varied.items():
        if field in brief_fields:
            brief_args[field] = first if kind == "a" else second
    brief = models.ProjectBrief(**brief_args)
    config = models.ProjectConfig()
    if kind == "b":
        config.ticket_backend = models.TicketBackendConfig(type="github", repo="o/r")
        if hasattr(config, "spec"):
            config.spec = models.SpecConfig(paths=["SPEC.md"], convention="D-<n>")
        config.expertise = [models.ExpertiseEntry(name="ana", tags=["api"])]
    modules = [] if kind == "a" else [models.Module(name="web", path="web/")]
    return generate_claude_md(role, brief, modules, config, Path(tempfile.mkdtemp()))


def worker(kind):
    import inspect

    from rite_ai.workspace import manage

    d = Path(tempfile.mkdtemp()) / "w"
    d.mkdir()
    fields = {f.name for f in models.WorkerManifest.__dataclass_fields__.values()}
    args = {"name": "alpha" if kind == "a" else "beta"}
    if "manager" in fields:
        args["manager"] = "" if kind == "a" else "m1"
    if "modules" in fields:
        args["modules"] = [] if kind == "a" else ["web"]
    if "claude_instructions" in fields:
        args["claude_instructions"] = "" if kind == "a" else "be brief"
    manifest = models.WorkerManifest(**args)
    sig = inspect.signature(manage._write_worker_claude_config)
    kwargs = {}
    if "spec" in sig.parameters:
        kwargs["spec"] = (
            models.SpecConfig(paths=["SPEC.md"]) if kind == "b" else models.SpecConfig()
        )
    if "module_commands" in sig.parameters:
        kwargs["module_commands"] = "" if kind == "a" else "Test: pytest"
    manage._write_worker_claude_config(d, manifest, **kwargs)
    return (d / "CLAUDE.md").read_text()


print(
    json.dumps(
        {
            "a": build("a", "owner"),
            "b": build("b", "owner"),
            "manager_a": build("a", "manager"),
            "manager_b": build("b", "manager"),
            "worker_a": worker("a"),
            "worker_b": worker("b"),
        }
    )
)
"""


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=True
    ).stdout


# A pattern has to pin down enough of a section for a match to be evidence.
# Both tests are about the section's GUIDANCE — the prose a release wrote the
# same way for every project — rather than its length: at least this many
# lines of it that are neither blank nor the heading, and that guidance being
# a real share of the whole. Sections that are mostly the project's own data
# (Project spec, What this is) fail the first test and stay unattributable,
# which is right: rite cannot tell its own rendering of those from the user's.
_MIN_GUIDANCE_LINES = 3
_MIN_LITERAL_SHARE = 0.35
# A gap stands for a run of lines only about as long as the one the release
# itself produced there, so a pattern cannot swallow a paragraph the user
# added between two lines of rite's prose.
_GAP_SLACK = 2


def _pattern(a: str, b: str) -> tuple[str | int, ...] | None:
    """Lines `a` and `b` share, each differing run replaced by its line bound."""
    left, right = a.splitlines(), b.splitlines()
    items: list[str | int] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            items.extend(left[i1:i2])
            continue
        bound = max(i2 - i1, j2 - j1) + _GAP_SLACK
        if items and isinstance(items[-1], int):
            items[-1] = max(items[-1], bound)
        else:
            items.append(bound)
    literal = [item for item in items if isinstance(item, str)]
    guidance = [line for line in literal if line.strip() and not line.startswith("## ")]
    if len(guidance) < _MIN_GUIDANCE_LINES or all(
        isinstance(item, str) for item in items
    ):
        return None  # nothing variable: the hash already covers it
    share = sum(len(line) + 1 for line in literal) / max(len(a), 1)
    return tuple(items) if share >= _MIN_LITERAL_SHARE else None


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    heading = None
    body: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if heading is not None:
                out[heading] = "".join(body).strip()
            heading = line.rstrip("\n")[3:].strip()
            body = [line]
        elif heading is not None:
            body.append(line)
    if heading is not None:
        out[heading] = "".join(body).strip()
    return out


def _format(path: Path) -> None:
    """Hand the generated file to ruff.

    The emitter writes valid Python, not FORMATTED Python, so running this
    tool as `docs/releasing.md` step 5 says produced a file that fails
    `ruff format --check` — a red gate on the release commit, for a file
    nobody edits by hand. Measured after tagging v0.4.0.
    """
    subprocess.run(
        ["uv", "run", "ruff", "format", str(path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )


def main() -> None:
    tags = sorted(t for t in _git("tag", "-l", "v*").decode().split() if t)
    sections: dict[str, set[str]] = {}
    patterns: dict[str, set[tuple[str | int, ...]]] = {}
    covered: list[str] = []
    # The tags this run actually read, as tags. `covered` is prose for the
    # docstring; the test that has to catch a release skipping this tool
    # cannot parse English out of one.
    recorded: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for tag in tags:
            src = Path(tmp) / tag
            src.mkdir(parents=True)
            subprocess.run(
                f"git archive {tag} | tar -x -C {src}", shell=True, cwd=ROOT, check=True
            )
            proc = subprocess.run(
                [sys.executable, "-c", PROBE],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": str(src / "src"), "PATH": "/usr/bin:/bin"},
            )
            if proc.returncode != 0:
                print(f"{tag}: skipped — {proc.stderr.strip().splitlines()[-1][:100]}")
                continue
            data = json.loads(proc.stdout)
            static_count = total = 0
            # The project's file and a Worker's, which have different headings
            # and are refreshed by the same rule.
            for first, second in (
                ("a", "b"),
                ("manager_a", "manager_b"),
                ("worker_a", "worker_b"),
            ):
                a, b = _sections(data[first]), _sections(data[second])
                static = [h for h in a if h in b and a[h] == b[h]]
                for heading in static:
                    digest = hashlib.sha256(a[heading].encode()).hexdigest()
                    sections.setdefault(heading, set()).add(digest)
                for heading in a:
                    if heading in static or heading not in b:
                        continue
                    found = _pattern(a[heading], b[heading])
                    if found is not None:
                        patterns.setdefault(heading, set()).add(found)
                static_count += len(static)
                total += len(a)
            covered.append(f"{tag} ({static_count}/{total} static)")
            recorded.append(tag)

    lines = [
        '"""Generated by `tools/section_history.py` — do not edit by hand.',
        "",
        "SHA-256 of each CLAUDE.md section every tagged release wrote identically",
        "for every project, so a file written before markers existed can still be",
        "shown to be rite's own rather than a user's edit.",
        "",
        f"From: {', '.join(covered)}.",
        '"""',
        "",
        "# ruff: noqa: E501 — these lines are the bytes a release wrote.",
        "",
        "# The releases read to build this file. Machine-readable on purpose:",
        "# `tests/test_update_refresh.py` asserts the newest tag is here.",
        f"RELEASES: tuple[str, ...] = {tuple(recorded)!r}",
        "",
        "SECTIONS: dict[str, frozenset[str]] = {",
    ]
    for heading in sorted(sections):
        lines.append(f"    {heading!r}: frozenset(")
        lines.append("        {")
        for digest in sorted(sections[heading]):
            lines.append(f"            {digest!r},")
        lines.append("        }")
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    lines.append(
        "# Lines a release wrote whatever the project, with each run that depends"
    )
    lines.append("# on the project written as the number of lines it may stand for.")
    lines.append("PATTERNS: dict[str, tuple[tuple[str | int, ...], ...]] = {")
    for heading in sorted(patterns):
        lines.append(f"    {heading!r}: (")
        for pattern in sorted(patterns[heading], key=repr):
            lines.append("        (")
            for item in pattern:
                lines.append(f"            {item!r},")
            lines.append("        ),")
        lines.append("    ),")
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n")
    _format(OUT)
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(sections)} heading(s) and "
        f"{len(patterns)} pattern heading(s) from {len(covered)} tag(s)"
    )


if __name__ == "__main__":
    main()
