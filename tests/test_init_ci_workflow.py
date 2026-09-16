"""The CI half of SPEC §11.5's "belt and braces", end to end.

`templates/ci/publish-gate.yml` was packaged into the wheel with **nothing
referencing it** — no Python, no test, no doc. Meanwhile the README
told the user who hits the `core.hooksPath` warning, in the paragraph they
reach precisely when their local hook failed to install, that "the CI workflow
`rite init` generates runs the same gate on every push and pull request, and
no local git config can switch that off." That user had zero gate coverage:
the hook was refused (correctly) and the workflow was never written.

§11.5.1 is explicit that this is not the backup layer — the hook is disarmable
by the developer's own git configuration with no signal that it happened,
"which makes [CI] the load-bearing one, not the backup."

These tests assert on the FILE `rite init` actually leaves on disk, parsed as
YAML, not on the template's text. The template was already correct-looking and
inert.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from rite_ai.cli.init import run_init
from rite_ai.cli.init.scaffold import (
    CI_WORKFLOW_MARKER,
    CI_WORKFLOW_REL_PATH,
    RITE_REPO_URL,
    render_ci_workflow,
    rite_install_spec,
    write_ci_workflow,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _workflow(root: Path) -> dict:
    text = (root / CI_WORKFLOW_REL_PATH).read_text()
    return yaml.safe_load(text)


def _steps(doc: dict) -> list[dict]:
    return doc["jobs"]["publish-gate"]["steps"]


# --- what `rite init` actually leaves behind --------------------------------


def test_init_writes_a_ci_workflow(tmp_path):
    """The defect itself: `rite init` generated no CI workflow at all."""
    root = _git_repo(tmp_path)
    result = run_init(root, yes=True)
    assert result.status == "created"
    assert (root / CI_WORKFLOW_REL_PATH).is_file()
    assert CI_WORKFLOW_REL_PATH in result.created_files


def test_generated_workflow_is_valid_yaml_with_a_runnable_job(tmp_path):
    """Valid YAML AND shaped like a workflow GitHub will run: a trigger, one
    job with a runner, and steps that each have something to execute.

    `on:` parses as the YAML boolean True — the `yes`/`no`/`on`/`off` legacy
    — so it is fetched under both spellings rather than asserted under one.
    """
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    doc = _workflow(root)

    assert doc["name"]
    triggers = doc.get("on", doc.get(True))
    assert set(triggers) == {"push", "pull_request"}

    job = doc["jobs"]["publish-gate"]
    assert job["runs-on"]
    steps = _steps(doc)
    assert steps
    for step in steps:
        assert "uses" in step or "run" in step, step


def test_generated_workflow_actually_runs_the_gate(tmp_path):
    """A workflow that installs rite and never calls it is the checklist's
    "the check observes the property it claims" line, in CI form."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    runs = " ".join(s.get("run", "") for s in _steps(_workflow(root)))
    assert "rite publish check" in runs


def test_generated_workflow_fetches_full_history(tmp_path):
    """`fetch-depth: 0`. The default shallow checkout gives the gate one
    commit to scan, so a full-history scan would silently become a HEAD
    scan — passing, and testing almost nothing."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    checkout = [s for s in _steps(_workflow(root)) if "checkout" in s.get("uses", "")]
    assert len(checkout) == 1
    assert checkout[0]["with"]["fetch-depth"] == 0


def test_generated_workflow_does_not_install_from_pypi(tmp_path):
    """The shipped template said `pipx install rite-ai`. rite-ai is not on
    PyPI (README, "Install"), so that step could only ever fail — in the one
    job §11.5.1 calls load-bearing."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    runs = " ".join(s.get("run", "") for s in _steps(_workflow(root)))
    assert not re.search(r"pipx install\s+[\"']?rite-ai[\"']?\s*$", runs.strip())
    assert f"git+{RITE_REPO_URL}@" in runs


def test_generated_workflow_has_no_unsubstituted_placeholders(tmp_path):
    """Generated content states only what was actually filled in. A stray
    `{{...}}` reaching the user's repo is a template leak, not a workflow."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    assert "{{" not in (root / CI_WORKFLOW_REL_PATH).read_text()


# --- the install target -----------------------------------------------------


def test_install_spec_pins_a_release_to_its_tag():
    assert rite_install_spec("0.1.0") == f"git+{RITE_REPO_URL}@v0.1.0"


def test_install_spec_falls_back_to_main_for_an_unreleased_build():
    """`__version__` is `0.0.0-unknown` when it cannot be read. Pinning a
    generated workflow to `v0.0.0-unknown` writes a file that dies at
    checkout; `main` at least runs."""
    assert rite_install_spec("0.0.0-unknown") == f"git+{RITE_REPO_URL}@main"


def test_repo_url_matches_the_one_install_sh_uses():
    """Two files name the install source and they must not drift — the
    README sends humans to `install.sh` and `rite init` sends CI here."""
    install_sh = (REPO_ROOT / "install.sh").read_text()
    match = re.search(r'^REPO="([^"]+)"', install_sh, re.MULTILINE)
    assert match, "install.sh no longer declares REPO= — update this test"
    assert match.group(1) == RITE_REPO_URL


def test_generated_workflow_pins_the_version_this_rite_reports():
    """Not a hardcoded string: whatever `rite --version` says is what the
    workflow it generates fetches."""
    import rite_ai

    rendered = render_ci_workflow()
    if re.match(r"^\d+\.\d+\.\d+$", rite_ai.__version__):
        assert f"@v{rite_ai.__version__}" in rendered
    else:  # pragma: no cover - a dev build without a readable VERSION
        assert "@main" in rendered


# --- what it refuses to do --------------------------------------------------


def test_a_rite_written_workflow_is_never_rewritten(tmp_path):
    """The marker says who AUTHORED the file, not who owns its contents now.

    The first thing anyone does to a generated workflow is edit it — a
    self-hosted runner, an extra branch, a required secret — and the first
    reason to edit this one is anything the generated version got wrong. An
    earlier version of this function rewrote any file carrying the marker,
    which discarded those edits silently while printing a tick.
    """
    root = _git_repo(tmp_path)
    assert write_ci_workflow(root).status == "written"
    path = root / CI_WORKFLOW_REL_PATH

    path.write_text(path.read_text() + "\n# my own edit\n")
    assert write_ci_workflow(root).status == "already_ours"
    assert "# my own edit" in path.read_text()


def test_a_second_run_over_an_identical_file_is_a_no_op(tmp_path):
    root = _git_repo(tmp_path)
    assert write_ci_workflow(root).status == "written"
    assert write_ci_workflow(root).status == "already_ours"


def test_an_already_present_workflow_is_not_reported_as_created(tmp_path, capsys):
    """ "Generated content states only what was actually measured." Nothing
    was created on the second run, so nothing says Created."""
    root = _git_repo(tmp_path)
    write_ci_workflow(root)
    capsys.readouterr()

    result = run_init(root, yes=True)

    assert CI_WORKFLOW_REL_PATH not in result.created_files
    out = capsys.readouterr().out
    assert f"Created {CI_WORKFLOW_REL_PATH}" not in out
    assert "already exists and was written by rite" in out


def test_an_unreadable_workflow_is_not_called_someone_elses(tmp_path, capsys):
    """`except OSError: return "foreign"` made `rite init` tell the user the
    file "was not written by rite" — an authorship claim it never checked.
    Same outcome (leave it alone), different sentence."""
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(f"{CI_WORKFLOW_MARKER}\nname: mine\n")
    # root ignores the mode bits, so the branch under test is simply
    # unreachable there — skipped rather than passing vacuously.
    if os.geteuid() == 0:
        pytest.skip("chmod does not restrict root")
    path.chmod(0o000)
    try:
        assert write_ci_workflow(root).status == "unreadable"
        capsys.readouterr()
        run_init(root, yes=True)
        out = capsys.readouterr().out
        assert "could not be read" in out
        assert "was not written by rite" not in out
    finally:
        path.chmod(0o644)


def test_an_unwritable_path_does_not_abort_the_whole_init(tmp_path, capsys):
    """`.github/workflows` existing as a FILE is an ordinary state, and the
    write sits between the `.rite/` writes and `CLAUDE.md`. An exception
    there left a project initialised enough that re-running is refused and
    incomplete enough to be useless — no CLAUDE.md, no `.claude/`."""
    root = _git_repo(tmp_path)
    (root / ".github").mkdir()
    (root / ".github" / "workflows").write_text("not a directory\n")

    assert write_ci_workflow(root).status == "write_failed"

    result = run_init(root, yes=True)

    assert result.status == "created"
    assert (root / "CLAUDE.md").is_file()
    assert (root / ".claude" / "commands" / "ticket.md").is_file()
    assert "could not be created" in capsys.readouterr().out


def test_a_workflow_rite_did_not_write_is_never_clobbered(tmp_path):
    """Same reasoning as the pre-push installer refusing to overwrite a
    hand-written hook. `.github/workflows/publish-gate.yml` is a fixed,
    well-known path; a file there may well be someone else's.

    Including one that runs the gate itself — `gate_hook_status` counts a
    hand-written hook running the gate as active because reporting is all it
    does, but this function writes, and replacing someone's CI is not a
    report.
    """
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    mine = "name: mine\njobs: {x: {steps: [{run: rite publish check}]}}\n"
    path.write_text(mine)

    assert write_ci_workflow(root).status == "foreign"
    assert path.read_text() == mine


def test_init_reports_a_foreign_workflow_rather_than_counting_it(tmp_path, capsys):
    """ "No CI workflow written" has to be as loud as a created file — the
    same rule the missing pre-push hook already follows. A silent skip here
    leaves the user believing the load-bearing layer is armed."""
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text("name: mine\n")

    result = run_init(root, yes=True)

    assert CI_WORKFLOW_REL_PATH not in result.created_files
    out = capsys.readouterr().out
    assert "already exists and carries no rite marker" in out


def test_no_workflow_outside_a_git_repo_and_init_says_so(tmp_path, capsys):
    """`rite init` works in a fresh directory (README, Quickstart). There is
    nowhere for a workflow to run there, and the user is told rather than
    left to assume CI is covering them."""
    result = run_init(tmp_path, yes=True)

    assert not (tmp_path / ".github").exists()
    assert CI_WORKFLOW_REL_PATH not in result.created_files
    assert "no CI workflow written" in capsys.readouterr().out


# --- the template itself ----------------------------------------------------


def test_template_is_parseable_before_substitution():
    """The placeholder sits inside a quoted shell string, so the shipped
    template stays valid YAML — which is what lets a broken template be
    caught here rather than in the user's CI."""
    text = (REPO_ROOT / "templates" / "ci" / "publish-gate.yml").read_text()
    assert yaml.safe_load(text)["jobs"]["publish-gate"]["steps"]


def test_template_carries_the_marker_every_generated_copy_needs():
    text = (REPO_ROOT / "templates" / "ci" / "publish-gate.yml").read_text()
    assert CI_WORKFLOW_MARKER in text


def test_generated_workflow_puts_gitleaks_on_path_not_merely_downloads_it(tmp_path):
    """The gate exits 3 — "could not run at all" — without gitleaks on PATH.

    Asserting `"gitleaks" in runs` is the proxy, not the property: the
    download URL alone satisfies it, and deleting the `mv` that actually
    installs the binary left the suite green. Both the install and the
    `gitleaks version` smoke check are pinned here.
    """
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    runs = " ".join(s.get("run", "") for s in _steps(_workflow(root)))
    assert "/usr/local/bin/gitleaks" in runs  # it lands on PATH
    assert "gitleaks version" in runs  # ...and is proved runnable there


def test_generated_workflow_verifies_the_gitleaks_download(tmp_path):
    """A pinned version is not integrity: a release asset can be replaced
    under an unchanged tag, and this binary IS the security control."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    runs = " ".join(s.get("run", "") for s in _steps(_workflow(root)))
    assert "checksums.txt" in runs
    assert "sha256sum" in runs and "--check" in runs


def test_generated_workflow_is_not_filtered_to_a_branch_called_main(tmp_path):
    """The first draft said `branches: [main]`, and it is wrong twice.

    On a project whose root branch is not `main`, the workflow is committed,
    valid and fires for nothing — §11.5.1's "reports itself installed while
    being entirely inactive", one layer up, in the change written to remove
    exactly that. And even on a `main` project a Worker never pushes to the
    root branch (its own generated instructions forbid it), so every push
    this gate exists to catch lands on a feature branch a filtered trigger
    would skip.

    Asserted on the FILTER, not the event names — the names were all the
    earlier test checked, which is why `branches: [nope]` kept it green.
    """
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    doc = _workflow(root)
    triggers = doc.get("on", doc.get(True))

    # A bare list of event names carries no filter at all, which is the
    # point; a mapping is only acceptable if no filter narrows it.
    if isinstance(triggers, dict):
        for event, config in triggers.items():
            assert not config, f"{event} is filtered: {config}"
    else:
        assert set(triggers) == {"push", "pull_request"}
    # And nowhere in the YAML itself. The header comment explains at length
    # why there is no branch filter, so the raw text is checked with comment
    # lines removed rather than as-is.
    body = "\n".join(
        line
        for line in (root / CI_WORKFLOW_REL_PATH).read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "branches" not in body


def test_generated_workflow_asks_for_no_more_token_than_it_needs(tmp_path):
    """It reads a checkout and writes nothing."""
    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    assert _workflow(root)["permissions"] == {"contents": "read"}


def test_install_sh_pins_the_same_ref_the_workflow_does():
    """The seam the first drift test missed. `install.sh` hardcodes
    `VERSION="${RITE_VERSION:-v0.3.0}"` while `rite_install_spec` derives
    `v{__version__}` — they agree only while VERSION is 0.3.0, and the
    comment in the generated workflow says they are the same tag."""
    import rite_ai

    install_sh = (REPO_ROOT / "install.sh").read_text()
    match = re.search(r'^VERSION="\$\{RITE_VERSION:-([^}]+)\}"', install_sh, re.M)
    assert match, "install.sh no longer declares a default VERSION — update this test"
    assert match.group(1) == rite_install_spec(rite_ai.__version__).rsplit("@", 1)[1]


# --- what the user is told when the local layer fails ------------------------


def test_redirected_hook_warning_says_ci_is_covering_them(tmp_path, capsys):
    """The README tells a `core.hooksPath` user, in the paragraph they reach
    at exactly this moment, that CI has them covered. `rite init` now says
    the same thing at the same moment — and only because the workflow it
    just wrote makes it true."""
    root = _git_repo(tmp_path)
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "config", "--local", "core.hooksPath", str(elsewhere)],
        check=True,
    )

    run_init(root, yes=True)
    out = capsys.readouterr().out

    assert "no pre-push hook installed" in out
    assert "core.hooksPath" in out
    assert "no local git config can switch that off" in out
    assert (root / CI_WORKFLOW_REL_PATH).is_file()


def test_the_ci_backstop_is_never_claimed_when_there_is_no_ci(tmp_path, capsys):
    """`rite init` once printed "✓ Created .git/hooks/pre-push" for a hook
    git would never read. Printing "CI has you covered" with no CI written
    is that sentence again, one layer up — so the no-hook message says the
    opposite instead."""
    run_init(tmp_path, yes=True)  # not a git repo: no hook, no workflow
    out = capsys.readouterr().out

    assert "no pre-push hook installed" in out
    assert "no local git config can switch that off" not in out
    assert "Nothing else is covering you" in out


# --- coverage is read off the file, not off who wrote it --------------------


def test_a_rite_written_workflow_edited_into_a_no_op_is_not_called_coverage(
    tmp_path, capsys
):
    """The origin defect, one layer up.

    `ci_ok` was computed from the marker alone, so a workflow rite wrote and
    the user then edited to run `echo` still earned "so you are not
    uncovered" — printed in the same breath as the redirected-hook warning.
    Hook off, gate off, user told they are covered: `✓ Created
    .git/hooks/pre-push` for a hook git would never read (SPEC §11.5.1),
    wearing a different hat.
    """
    root = _git_repo(tmp_path)
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "config", "--local", "core.hooksPath", str(elsewhere)],
        check=True,
    )
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(f"{CI_WORKFLOW_MARKER}\nname: inert\njobs: {{x: {{steps: []}}}}\n")

    result = write_ci_workflow(root)
    assert result.status == "already_ours"
    assert result.runs_gate is False

    run_init(root, yes=True)
    out = capsys.readouterr().out
    assert "no local git config can switch that off" not in out
    assert "Nothing else is covering you" in out
    assert "does not run in CI" in out


def test_a_foreign_workflow_that_runs_the_gate_does_count_as_coverage(tmp_path, capsys):
    """The other direction, and it matters just as much: a workflow someone
    else wrote that calls `rite publish check` covers this project
    completely. `gate_hook_status` already counts a hand-written hook
    running the gate as active, for the same reason."""
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "name: our own gate\n"
        "on: [push]\n"
        "jobs:\n  g:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: rite publish check\n"
    )

    result = write_ci_workflow(root)
    assert result.status == "foreign"
    assert result.runs_gate is True

    run_init(root, yes=True)
    out = capsys.readouterr().out
    assert "It runs `rite publish check`" in out
    assert "does not run in CI" not in out


def test_an_unreadable_workflow_is_never_counted_as_coverage(tmp_path):
    """Unread is unknown, and unknown is not coverage."""
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(f"{CI_WORKFLOW_MARKER}\n- run: rite publish check\n")
    # root ignores the mode bits, so the branch under test is simply
    # unreachable there — skipped rather than passing vacuously.
    if os.geteuid() == 0:
        pytest.skip("chmod does not restrict root")
    path.chmod(0o000)
    try:
        result = write_ci_workflow(root)
        assert result.status == "unreadable"
        assert result.runs_gate is False
    finally:
        path.chmod(0o644)


def test_write_failed_reports_the_error_it_got_not_a_guess_at_the_cause(tmp_path):
    """The first version asserted "(its directory is a file, or is not
    writable)" — two of the several things an OSError there can be. ENOSPC,
    a read-only filesystem and ELOOP all reached that sentence and were all
    described wrongly by it."""
    root = _git_repo(tmp_path)
    (root / ".github").mkdir()
    (root / ".github" / "workflows").write_text("not a directory\n")

    result = write_ci_workflow(root)

    assert result.status == "write_failed"
    assert result.detail  # the OSError's own words
    assert result.runs_gate is False


# --- the remedy the messages prescribe has to exist -------------------------


def test_install_ci_adds_the_workflow_to_an_already_initialised_project(tmp_path):
    """Every "no CI workflow written" message points at `rite publish
    install-ci`, because `rite init` cannot be the remedy: on an
    initialised project it returns `already_initialized` and offers to wipe
    the config. SPEC §11.5 gives exactly that reasoning for
    `install-hook`; this is the same gap for the layer §11.5.1 calls
    load-bearing.
    """
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _git_repo(tmp_path)
    run_init(root, yes=True)
    (root / CI_WORKFLOW_REL_PATH).unlink()

    # The remedy the README rules out, first — so the test shows why the
    # command is needed rather than asserting it in the abstract.
    assert run_init(root, yes=True).status == "already_initialized"
    assert not (root / CI_WORKFLOW_REL_PATH).exists()

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import os

        os.chdir(root)
        result = runner.invoke(cli, ["publish", "install-ci"])

    assert result.exit_code == 0, result.output
    assert (root / CI_WORKFLOW_REL_PATH).is_file()
    assert "rite publish check" in (root / CI_WORKFLOW_REL_PATH).read_text()


def test_install_ci_refuses_an_existing_workflow_and_says_whether_it_is_armed(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text("name: mine\njobs: {}\n")
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["publish", "install-ci"])
    assert result.exit_code == 1
    assert "carries no rite marker" in result.output
    assert "does NOT run in CI" in result.output
    assert "does not call `rite publish check`" in result.output
    assert path.read_text() == "name: mine\njobs: {}\n"  # untouched

    forced = CliRunner().invoke(cli, ["publish", "install-ci", "--force"])
    assert forced.exit_code == 0
    assert "replaced what was there" in forced.output
    assert "rite publish check" in path.read_text()


def test_the_header_comment_alone_is_not_mistaken_for_a_running_gate(tmp_path, capsys):
    """The first version of the coverage check was `"rite publish check" in
    text`. The template this module generates explains itself in a header
    comment that says `rite publish check` **twice** — so a file made of
    that header plus an empty `jobs:` block, which runs nothing whatsoever,
    reported "the publish gate does run in CI".

    T2's own defect, hiding inside T2's fix.
    """
    root = _git_repo(tmp_path)
    elsewhere = tmp_path / "shared-hooks"
    elsewhere.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "config", "--local", "core.hooksPath", str(elsewhere)],
        check=True,
    )
    header = "\n".join(
        line
        for line in render_ci_workflow().splitlines()
        if line.startswith("#") or not line.strip()
    )
    assert header.count("rite publish check") >= 2, "template no longer baits this"

    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(f"{header}\nname: Publish gate\non: [push]\njobs: {{}}\n")

    assert write_ci_workflow(root).runs_gate is False

    run_init(root, yes=True)
    out = capsys.readouterr().out
    assert "no local git config can switch that off" not in out
    assert "Nothing else is covering you" in out


def test_a_commented_out_gate_step_is_not_coverage(tmp_path):
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "name: x\non: [push]\njobs:\n  g:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      # - run: rite publish check\n      - run: echo hi\n"
    )
    assert write_ci_workflow(root).runs_gate is False


def test_an_unparseable_workflow_still_ignores_its_comments(tmp_path):
    """The fallback path for YAML that will not parse. Still never the raw
    text — that is the whole finding."""
    root = _git_repo(tmp_path)
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True)
    path.write_text("# rite publish check\n\tthis: [is not( valid yaml\n")
    assert write_ci_workflow(root).runs_gate is False


def test_force_only_claims_a_replacement_when_there_was_one(tmp_path, monkeypatch):
    """`--force` wrote unconditionally and always printed "(replaced what
    was there)", including on a repo with no `.github/` at all."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _git_repo(tmp_path)
    monkeypatch.chdir(root)

    first = CliRunner().invoke(cli, ["publish", "install-ci", "--force"])
    assert first.exit_code == 0
    assert "replaced" not in first.output

    again = CliRunner().invoke(cli, ["publish", "install-ci", "--force"])
    assert again.exit_code == 0
    assert "replaced what was there" in again.output


def test_no_generated_artifact_asserts_the_pre_push_hook_is_installed(tmp_path):
    """`rite init` writes the same files whether or not the hook installed,
    so any sentence in them saying it did is a fact nobody checked — and on
    a machine with a global `core.hooksPath` it is simply false.

    It shipped in three places: the workflow header, the generated
    `CLAUDE.md`, and `/ticket` step 8. This walks everything `rite init`
    leaves behind rather than naming those three, because the next one will
    be somewhere else.
    """
    root = _git_repo(tmp_path)
    run_init(root, yes=True)

    banned = (
        "via the installed pre-push hook",
        "the installed pre-push hook",
        "`rite init` also installs a pre-push hook",
    )
    generated = [root / "CLAUDE.md", root / CI_WORKFLOW_REL_PATH]
    generated += sorted((root / ".claude").rglob("*.md"))
    assert len(generated) > 5, "nothing was generated — test is not testing"

    for path in generated:
        text = path.read_text()
        for phrase in banned:
            assert phrase not in text, f"{path.name} asserts the hook is installed"
