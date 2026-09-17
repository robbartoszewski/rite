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

Sections that do vary (Modules, Role, Project spec) are deliberately absent:
nothing here can tell an older release's rendering of those from a user's edit,
and guessing is the one thing this whole mechanism exists to avoid.
"""

from __future__ import annotations

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

def build(kind):
    brief_fields = {f.name for f in models.ProjectBrief.__dataclass_fields__.values()}
    brief_args = {
        "name": "acme" if kind == "a" else "zeta",
        "role": "owner" if kind == "a" else "manager",
    }
    if "root_branch" in brief_fields:
        brief_args["root_branch"] = "main" if kind == "a" else "trunk"
    brief = models.ProjectBrief(**brief_args)
    config = models.ProjectConfig()
    if kind == "b":
        config.ticket_backend = models.TicketBackendConfig(type="github", repo="o/r")
        if hasattr(config, "spec"):
            config.spec = models.SpecConfig(paths=["SPEC.md"], convention="D-<n>")
        config.expertise = [models.ExpertiseEntry(name="ana", tags=["api"])]
    modules = [] if kind == "a" else [models.Module(name="web", path="web/")]
    role = "owner" if kind == "a" else "manager"
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
            "a": build("a"),
            "b": build("b"),
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


def main() -> None:
    tags = sorted(t for t in _git("tag", "-l", "v*").decode().split() if t)
    sections: dict[str, set[str]] = {}
    covered: list[str] = []
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
            for first, second in (("a", "b"), ("worker_a", "worker_b")):
                a, b = _sections(data[first]), _sections(data[second])
                static = [h for h in a if h in b and a[h] == b[h]]
                for heading in static:
                    digest = hashlib.sha256(a[heading].encode()).hexdigest()
                    sections.setdefault(heading, set()).add(digest)
                static_count += len(static)
                total += len(a)
            covered.append(f"{tag} ({static_count}/{total} static)")

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
    OUT.write_text("\n".join(lines) + "\n")
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(sections)} heading(s) "
        f"from {len(covered)} tag(s)"
    )


if __name__ == "__main__":
    main()
