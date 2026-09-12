"""Artifacts — not modules — that nothing read.

An earlier sweep closed this project's signature defect at the level of code
modules: a component complete, tested, and called by nothing. Two cases
escaped it (`templates/ci/publish-gate.yml`, the review checklist rite ships
but did not work) because the check was worded for modules and those were
**artifacts** — a template, a workflow, a config key, a generated file. This
file is the artifact-level version, one test per case the re-audit found.

The shared shape, and what each test here is really asserting: the artifact
had a parser, a serialiser, a docs entry and a passing test, and no path from
a real entry point — the CLI table, `rite init`'s writes, what a Worker is
handed — ever reached it. "Only the tests reference it" is the signature, so
none of these assert that a thing *exists*; they assert something a user or a
Worker would actually see changes when the artifact does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from rite_ai.cli.init import scaffold
from rite_ai.config.models import SandboxConfig
from rite_ai.config.parse import parse_worker
from rite_ai.workspace import add_worker

REPO_ROOT = Path(__file__).resolve().parent.parent


def _init_project(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "brief.yaml").write_text("project:\n  name: test\n  role: owner\n")
    return tmp_path


# --- 1. `workers/<name>/.claude/` --------------------------------------------
#
# SPEC §9.6 says `rite add worker` generates "a minimal `CLAUDE.md` and
# `.claude/` directory". It ran `mkdir` and never wrote into it, while the
# CLAUDE.md beside it told the Worker at step 6 to run `/review`.


def test_the_slash_command_a_worker_is_told_to_run_exists_in_its_workspace(tmp_path):
    """Not "a `.claude/` directory was created" — that was true the whole
    time it was empty. The instruction names `/review`, and a slash command
    is read from the `.claude/` of the directory the session starts in."""
    root = _init_project(tmp_path)
    assert add_worker(root, "alpha", manager="alice").ok

    worker_dir = root / "workers" / "alpha"
    instructions = (worker_dir / "CLAUDE.md").read_text()
    assert "/review" in instructions

    assert (worker_dir / ".claude" / "commands" / "review.md").is_file()


def test_the_worker_gets_every_agent_its_review_command_names(tmp_path):
    """`review.md` dispatches to four agents by name. Three of four would
    have been a quieter version of the same defect — a command that reads,
    then names a subagent that is not there."""
    root = _init_project(tmp_path)
    add_worker(root, "alpha")

    agents_dir = root / "workers" / "alpha" / ".claude" / "agents"
    review = (
        root / "workers" / "alpha" / ".claude" / "commands" / "review.md"
    ).read_text()

    installed = sorted(p.stem for p in agents_dir.glob("*.md"))
    assert installed, "worker got no review agents at all"
    for agent in installed:
        assert agent in review, f"{agent} installed but named by nothing"
    for named in ("reviewer-round1", "reviewer-terminating", "reviewer-seam"):
        assert named in installed


def test_the_worker_agents_are_the_shipped_templates_not_a_second_copy(tmp_path):
    """The same `templates/` tree `rite init` copies the project-root pair
    from. Byte-compared: the last time one of these sets existed twice, the
    two wired different installers to divergent content."""
    root = _init_project(tmp_path)
    add_worker(root, "alpha")

    claude_dir = root / "workers" / "alpha" / ".claude"
    for installed in sorted(claude_dir.rglob("*.md")):
        origin = REPO_ROOT / "templates" / installed.relative_to(claude_dir)
        assert origin.is_file(), f"{installed.name} came from no template"
        assert installed.read_bytes() == origin.read_bytes()


# --- 2. `worker.yml`'s `claude_instructions` ---------------------------------
#
# SPEC §8.4's own worker.yml example carries the key. `parse_worker` read it;
# nothing wrote it and nothing rendered it.


def test_per_worker_instructions_survive_the_write_and_reach_the_session(tmp_path):
    """Both halves. A key stored and never rendered is the same orphan one
    file further on; a key rendered from memory and never stored is lost on
    the next read."""
    root = _init_project(tmp_path)
    note = "Never touch the billing schema without Ada."
    assert add_worker(root, "alpha", manager="ada", instructions=note).ok

    worker_dir = root / "workers" / "alpha"

    manifest = parse_worker(worker_dir / "worker.yml")
    assert not isinstance(manifest, Exception)
    assert manifest.claude_instructions.strip() == note

    assert note in (worker_dir / "CLAUDE.md").read_text()


def test_a_worker_with_no_instructions_gets_no_empty_key_or_empty_heading(tmp_path):
    """The documented file for a Worker with nothing extra to say, not one
    carrying `claude_instructions: ''` and a heading with nothing under it."""
    root = _init_project(tmp_path)
    add_worker(root, "alpha")

    worker_dir = root / "workers" / "alpha"
    raw = yaml.safe_load((worker_dir / "worker.yml").read_text())
    assert "claude_instructions" not in raw["worker"]
    assert "From your Manager" not in (worker_dir / "CLAUDE.md").read_text()


# --- 3. `sandbox.token_permissions` ------------------------------------------
#
# Parsed, serialised, round-trip-tested and listed in SPEC §8's config block,
# while `rite add worker` — the command §5.3.4 names as its consumer — printed
# a hardcoded sentence.


def test_the_provisioning_prompt_is_built_from_the_configured_permissions():
    from rite_ai.cli.main import _token_permission_line

    line = _token_permission_line(SandboxConfig().token_permissions)
    assert "Contents (read/write)" in line
    assert "Pull requests (read/write)" in line

    narrowed = _token_permission_line(["contents"])
    assert "Contents (read/write)" in narrowed
    assert "Pull requests" not in narrowed, (
        "the default leaked through a project that narrowed the key"
    )


def test_a_permission_rite_has_no_label_for_is_printed_not_dropped():
    """The key is the user's. A value rite has never heard of is still one
    they meant to ask for, and silently dropping it would understate the
    token they are about to create."""
    line = _token_permission_line_of(["contents", "deployments"])
    assert "deployments" in line


def _token_permission_line_of(permissions: list[str]) -> str:
    from rite_ai.cli.main import _token_permission_line

    return _token_permission_line(permissions)


def test_an_empty_permission_list_does_not_read_as_a_token_that_can_do_nothing():
    """§5.3.3 is a MINIMUM, so `[]` is a config that forgot to say, not a
    request for no access."""
    line = _token_permission_line_of([])
    assert "sandbox.token_permissions" in line
    assert "§5.3.3" in line


# --- 4. `RUNTIME_STATE_IGNORES` ----------------------------------------------
#
# Defined, exported in `__all__`, carrying the whole rationale for ignoring
# the directory rather than enumerating its contents — and read by nothing.
# `gitignore_lines`, the one function it exists for, spelled the same two
# patterns out again three lines below its definition.


def test_the_gitignore_block_is_built_from_the_constant(monkeypatch):
    """Mutate the constant and the generated block must move with it. A
    presence check ("`.rite/*` is in the output") passed the whole time the
    constant was inert, because the literal three lines below said the same
    thing."""
    monkeypatch.setattr(
        scaffold, "RUNTIME_STATE_IGNORES", (".rite/*", "workers/", "scratch/")
    )
    lines = scaffold.gitignore_lines(kb_commit=True)
    assert "scratch/" in lines


def test_the_block_still_re_includes_what_a_project_shares():
    """`.rite/*` ignores the directory's CONTENTS, which is what makes the
    `!` re-includes legal. Extending from the constant must not reorder them
    past the excludes."""
    lines = scaffold.gitignore_lines(kb_commit=True)
    assert lines.index(".rite/*") < lines.index("!.rite/brief.yaml")
    for entry in scaffold.AUTHORED_CONFIG:
        assert f"!{entry}" in lines


# --- 5. rite's own publish-gate workflow --------------------------------------


def test_rites_own_gate_verifies_the_binary_its_template_says_it_must():
    """`templates/ci/publish-gate.yml` tells every generated project that
    gitleaks here "is not a build tool — it IS the security control this job
    exists to run", and checksums it. This repository's own copy downloaded
    it unverified: rite not applying to itself what it ships."""
    own = (REPO_ROOT / ".github" / "workflows" / "publish-gate.yml").read_text()
    template = (REPO_ROOT / "templates" / "ci" / "publish-gate.yml").read_text()

    for required in ("checksums.txt", "sha256sum --check"):
        assert required in template, "template stopped checksumming — test is stale"
        assert required in own, f"rite's own workflow does not {required!r}"


def test_neither_workflow_degrades_the_check_into_ignore_missing():
    """Measured on macOS: `sha256sum --check --ignore-missing` with nothing
    left to match exits 0 silently on one implementation. The grep is what
    makes a renamed asset stop the job.

    Read out of the parsed `run:` blocks, not off the raw text — both files
    name `--ignore-missing` in the comment explaining why they do not use
    it, and a substring search over the whole file cannot tell the warning
    from the mistake."""
    for path in (
        REPO_ROOT / ".github" / "workflows" / "publish-gate.yml",
        REPO_ROOT / "templates" / "ci" / "publish-gate.yml",
    ):
        spec = yaml.safe_load(path.read_text())
        (job,) = spec["jobs"].values()
        script = "\n".join(
            step["run"] for step in job["steps"] if isinstance(step.get("run"), str)
        )
        assert "--ignore-missing" not in script, f"{path.name} runs the no-op form"
        assert "grep ' gitleaks_" in script, f"{path.name} does not pin the asset"


def test_rites_own_workflow_is_valid_yaml_and_runs_the_gate_last():
    """A checksum step spliced into the wrong block is a workflow that
    parses and never scans."""
    spec = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "publish-gate.yml").read_text()
    )
    steps = spec["jobs"]["publish-gate"]["steps"]
    assert "rite_ai.gate check" in steps[-1]["run"]


# --- the inverse: what git actually does with the generated block ------------


def test_the_generated_gitignore_really_ignores_runtime_state(tmp_path):
    """Not what the lines say — what `git check-ignore` does with them.
    Everything else here traces a reader; this one runs the real consumer."""
    root = tmp_path
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    (root / ".gitignore").write_text(
        "\n".join(scaffold.gitignore_lines(kb_commit=True)) + "\n"
    )
    (root / ".rite").mkdir()
    (root / ".rite" / "claims.jsonl").write_text("{}\n")
    (root / ".rite" / "brief.yaml").write_text("project: {}\n")

    def ignored(rel: str) -> bool:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", rel], cwd=root, capture_output=True
            ).returncode
            == 0
        )

    assert ignored(".rite/claims.jsonl"), "runtime state would be committed"
    assert not ignored(".rite/brief.yaml"), "shared config would be ignored"
