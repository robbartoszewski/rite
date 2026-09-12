"""`rite review` merges the project-wide and repo-level checklists, is
tested, and — until these tests — was called by nothing.

Both templates that drive a review told the session to open
`.rite/review-checklist.md` itself and merge the repo-level one in by hand.
So the merge logic had a test suite and no caller, and every real review ran
a hand-merge that drifts the moment either file changes: the built-and-
uncalled defect this project shipped twice before (`rite prepare`, `rite
status`), a third time.

These assert the seam in both directions — that the templates name the
command, and that the command they name produces what the templates promise.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.review.merge import merge_checklists

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"

# Every template that sends a session at the checklist. Adding a third one
# that hand-merges instead is the regression this list exists to catch.
READERS = (
    TEMPLATES / "commands" / "review.md",
    TEMPLATES / "agents" / "reviewer-round1.md",
)


def _project(
    tmp_path: Path,
    *,
    project_checklist: str | None = None,
    repo_checklist: str | None = None,
) -> Path:
    """A rite project with `backend` registered.

    Its PATH is `repos/backend/`, deliberately not `backend/`. With the two
    the same, deleting the name-to-path lookup entirely left every test in
    this file green — `--module backend` resolved by accident, because the
    name happened to be a valid relative path to the same directory. The
    lookup is the thing under test, so the fixture has to be able to tell
    the two apart.
    """
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "modules.yaml").write_text(
        "modules:\n  backend:\n    path: repos/backend/\n    branch: main\n"
    )
    if project_checklist is not None:
        (project / ".rite" / "review-checklist.md").write_text(project_checklist)
    if repo_checklist is not None:
        (project / "repos" / "backend" / ".rite").mkdir(parents=True)
        (project / "repos" / "backend" / ".rite" / "review-checklist.md").write_text(
            repo_checklist
        )
    return project


def test_every_review_template_calls_the_command():
    for path in READERS:
        text = path.read_text()
        assert "rite review" in text, path
        assert "rite review --module" in text, path


def _flat(path: Path) -> str:
    """Line-wrapped prose, as one whitespace-normalised lowercase line.

    These templates wrap at ~76 columns, so a phrase like "by hand" is as
    likely to be split across two lines as not. Asserting on the raw text
    tests the line-wrapping, which is a proxy for what the sentence says —
    the exact substitution the checklist's proxy line now names."""
    return " ".join(path.read_text().lower().split())


def test_no_review_template_names_the_repo_level_checklist_as_a_thing_to_open():
    """The instruction that made the command unreachable, stated as the one
    property that is actually checkable: `reviewer-round1` used to list
    `<repo>/.rite/review-checklist.md` as item 2 of "what to load first",
    which is a hand-merge however it is worded.

    `rite review --module <name>` is now the only route to that file, so
    naming its path as something to load is the regression.

    An earlier version of this test looked for a negation ("do not", "never")
    in a text window around each mention of the checklist path. Both a
    ±140-character window and a split on "." were too loose — the split
    shredded the path on its own full stops so the loop never ran at all,
    and the window picked up the unrelated "Do not invent checklist items"
    two lines below. Neither was checking what it claimed.
    """
    for path in READERS:
        text = _flat(path)
        assert "<repo>/.rite/review-checklist.md" not in text, path
        assert "appends** to the project one" not in text, path


def test_the_command_comes_before_the_file_it_reads():
    """A template that names `rite review` in a footnote after telling the
    reader to open the file has not changed what anyone does."""
    for path in READERS:
        text = _flat(path)
        assert text.index("rite review") < text.index(".rite/review-checklist.md"), path


def test_the_command_produces_what_the_templates_promise(tmp_path, monkeypatch):
    """ "project-wide first, the repo's own as an addendum" is what both
    templates now tell the reader `rite review --module` does. Asserted
    against the real CLI in a real project, not against the merge function
    the templates never mention."""
    project = _project(
        tmp_path,
        project_checklist="## Project\n\n- [ ] PROJECT_LINE\n",
        repo_checklist="## Backend\n\n- [ ] REPO_LINE\n",
    )

    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["review", "--module", "backend"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "PROJECT_LINE" in out
    assert "REPO_LINE" in out
    assert out.index("PROJECT_LINE") < out.index("REPO_LINE")


def test_the_command_is_the_same_merge_the_module_implements(tmp_path, monkeypatch):
    """Not two implementations that happen to agree today."""
    project = _project(
        tmp_path,
        project_checklist="## P\n\n- [ ] one\n",
        repo_checklist="## B\n\n- [ ] two\n",
    )

    items = merge_checklists(project, "repos/backend/")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["review", "--module", "backend"])

    assert items, "fixture produced no checklist items — test is not testing"
    # Whitespace-normalised: `rite review` wraps each item to terminal width,
    # so a raw `in` passes only while the fixture's items are short enough not
    # to wrap — which these are, and which every real checklist item is not.
    # The assertion would have quietly stopped meaning anything.
    flat = " ".join(result.output.split())
    for item in items:
        assert " ".join(item.text.split()) in flat


def test_a_project_with_no_checklist_says_so_rather_than_printing_nothing(
    tmp_path, monkeypatch
):
    """`reviewer-round1` is told to report this rather than review against
    its own instincts and call it a checklist pass — which only works if the
    command actually says it."""
    project = _project(tmp_path)

    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["review"])

    assert result.exit_code == 0
    assert "No checklist items found." in result.output
    round1 = (TEMPLATES / "agents" / "reviewer-round1.md").read_text()
    assert "No checklist items found." in round1


def test_generated_claude_md_points_at_the_command_too(tmp_path):
    """The third place a session is sent at the checklist."""
    from rite_ai.cli.init.claude_gen import generate_claude_md
    from rite_ai.config.models import ProjectBrief, ProjectConfig

    brief = ProjectBrief(
        name="acme",
        role="owner",
        root_branch="main",
        kind="",
        features="",
        notes="",
        platform="",
        languages=[],
        frameworks=[],
        architecture="",
    )
    md = generate_claude_md("owner", brief, [], ProjectConfig(), tmp_path)
    review = md[md.index("## Review convention") :]
    assert "rite review" in review


# --- the refusal both templates now promise --------------------------------


def test_an_unregistered_module_is_refused_not_quietly_ignored(tmp_path, monkeypatch):
    """`rite review --module bakcend` used to print the project-wide list,
    exit 0, and say nothing. A review agent then works a checklist missing
    every repo-level line and reports a clean pass against it.

    Both templates now make this command mandatory, so a quiet wrong answer
    here is a quiet wrong answer in every review — the checklist's own first
    line ("A failure path exits non-zero and says why"), in the command the
    checklist is fetched with.
    """
    project = _project(
        tmp_path,
        project_checklist="## P\n\n- [ ] PROJECT_LINE\n",
        repo_checklist="## B\n\n- [ ] REPO_LINE\n",
    )
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["review", "--module", "bakcend"])

    assert result.exit_code == 1
    assert "no module 'bakcend'" in result.output
    assert "registered: backend" in result.output  # names the real ones
    assert "PROJECT_LINE" not in result.output  # and prints no checklist


def test_a_project_with_no_modules_says_that_rather_than_listing_nothing(
    tmp_path, monkeypatch
):
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["review", "--module", "backend"])

    assert result.exit_code == 1
    assert "none are registered" in result.output


def test_a_module_resolves_by_name_and_by_path(tmp_path, monkeypatch):
    """Both spellings appear in the wild — `modules.yaml` keys are names,
    the module map and every claim use paths — and here the two are
    genuinely different strings (`backend` vs `repos/backend/`), so passing
    the name really does exercise the lookup rather than falling through to
    a directory that happens to share the name."""
    project = _project(tmp_path, repo_checklist="## B\n\n- [ ] REPO_LINE\n")
    monkeypatch.chdir(project)

    for spelling in ("backend", "repos/backend", "repos/backend/"):
        result = CliRunner().invoke(cli, ["review", "--module", spelling])
        assert result.exit_code == 0, spelling
        assert "REPO_LINE" in result.output, spelling

    # ...and the bare directory name is NOT a path here, so it must not
    # resolve as one.
    assert not (project / "backend").exists()


def test_both_templates_promise_the_refusal_the_command_performs():
    """The templates tell a reviewer an unregistered name is refused. That
    sentence is only safe while it is true."""
    for path in READERS:
        text = _flat(path)
        assert "refused" in text, path


def test_a_malformed_modules_file_is_reported_not_treated_as_no_modules(
    tmp_path, monkeypatch
):
    """`parse_modules` returns a `ParseError` for broken YAML. Treating that
    as "no modules registered" would refuse a perfectly valid module name
    with a misleading reason, and hide the real problem."""
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "modules.yaml").write_text("modules: [not, a, mapping]\n")
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["review", "--module", "backend"])

    assert result.exit_code == 1
    assert "modules.yaml" in result.output
    assert "none are registered" not in result.output


# --- what using it in rite's own repo revealed -------------------------------


def test_output_is_wrapped_for_the_human_who_has_to_read_it(tmp_path, monkeypatch):
    """`load_checklist` joins each item's continuation lines into one string,
    so the longest line of rite's own checklist came out as over 500 unbroken
    characters. Both review templates now make this command mandatory, so its
    output is the checklist as far as every reviewer is concerned.

    Found by running it in the repo that ships it — which nobody had done,
    for the same reason the checklist did not exist there.
    """
    long_item = (
        "A single checklist item whose authored text runs well past any "
        "terminal width because it explains the defect it was earned by, "
        "which is what every item in the shipped template does and why this "
        "matters at all rather than being a cosmetic preference."
    )
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "review-checklist.md").write_text(
        f"## P\n\n- [ ] {long_item}\n"
    )
    monkeypatch.chdir(project)

    out = CliRunner().invoke(cli, ["review"]).output

    # The property is "no line is long because of WRAPPING". A line can still
    # exceed the width if it holds one unbreakable token — `break_long_words`
    # is off on purpose, so a long URL or path is never split mid-token — so
    # the bound is asserted per line against its own longest word rather than
    # as a flat number. An earlier version hardcoded 90, which happened to
    # hold for this fixture and would mislead whoever adds a long URL.
    from rite_ai.review.checklist import _WRAP_WIDTH

    for line in out.splitlines():
        if len(line) <= _WRAP_WIDTH:
            continue
        longest_token = max((len(w) for w in line.split()), default=0)
        assert longest_token > _WRAP_WIDTH - 2, f"wrapped badly: {line!r}"
    # ...and wrapping must not have lost or mangled the text.
    assert " ".join(long_item.split()) in " ".join(out.split())


def test_the_output_says_which_checklists_it_merged(tmp_path, monkeypatch):
    """`rite review --module x` on a module with no checklist of its own was
    byte-identical to `rite review`, so a reviewer told to work "the project
    checklist plus that repo's own, appended" — what both templates promise —
    could not tell the second half was absent rather than empty."""
    project = _project(
        tmp_path,
        project_checklist="## P\n\n- [ ] one\n",
    )
    monkeypatch.chdir(project)

    bare = CliRunner().invoke(cli, ["review"]).output
    merged = CliRunner().invoke(cli, ["review", "--module", "backend"]).output

    assert bare != merged, "absence of a repo checklist is invisible"
    assert ".rite/review-checklist.md (1 item)" in bare
    assert "repos/backend/.rite/review-checklist.md (absent" in merged

    # ...and when the repo DOES have one, it is counted rather than assumed.
    (project / "repos" / "backend" / ".rite").mkdir(parents=True, exist_ok=True)
    (project / "repos" / "backend" / ".rite" / "review-checklist.md").write_text(
        "## B\n\n- [ ] two\n- [ ] three\n"
    )
    now = CliRunner().invoke(cli, ["review", "--module", "backend"]).output
    assert "repos/backend/.rite/review-checklist.md (2 items)" in now


def test_a_single_item_is_not_reported_as_1_items(tmp_path, monkeypatch):
    """The checklist's own line: a missing or undefined input must not render
    as plausible-looking content. A hardcoded plural is the same class, and
    this one shipped for about four minutes."""
    project = _project(tmp_path, project_checklist="## P\n\n- [ ] only one\n")
    monkeypatch.chdir(project)

    out = CliRunner().invoke(cli, ["review"]).output

    assert "(1 item)" in out
    assert "1 items" not in out


def test_an_absolute_module_path_does_not_traceback(tmp_path, monkeypatch):
    """`_source` called `relative_to(root)` unguarded, and `parse_modules`
    does not forbid an absolute `path:`. The provenance line is a
    convenience; it must not be what turns `rite review` into a traceback."""
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "review-checklist.md").write_text("## P\n\n- [ ] one\n")
    (project / ".rite" / "modules.yaml").write_text(
        "modules:\n  ext:\n    path: /tmp/elsewhere-does-not-exist\n"
    )
    monkeypatch.chdir(project)

    result = CliRunner().invoke(cli, ["review", "--module", "ext"])

    assert result.exit_code == 0, result.output
    assert result.exception is None, result.exception
    assert "one" in result.output


def test_a_file_that_exists_but_parses_to_nothing_is_not_called_absent(
    tmp_path, monkeypatch
):
    """Branching on item COUNT rather than file existence told a user who
    typed `* [ ]` instead of `- [ ]` that their checklist did not exist."""
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "review-checklist.md").write_text(
        "# Checklist\n\nSome prose, and bullets with the wrong marker:\n\n* [ ] one\n"
    )
    monkeypatch.chdir(project)

    out = CliRunner().invoke(cli, ["review"]).output

    assert "(absent)" not in out
    assert "no `- [ ] ` items found" in out


def test_a_genuinely_missing_file_still_says_absent(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    monkeypatch.chdir(project)

    assert "(absent)" in CliRunner().invoke(cli, ["review"]).output


def test_a_module_registered_at_the_project_root_says_so(tmp_path, monkeypatch):
    """`path: .` makes `merge_checklists` load one file twice, so every item
    is listed twice. The count alone would be the honest half of a dishonest
    listing."""
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "review-checklist.md").write_text("## P\n\n- [ ] one\n")
    (project / ".rite" / "modules.yaml").write_text("modules:\n  self:\n    path: .\n")
    monkeypatch.chdir(project)

    out = CliRunner().invoke(cli, ["review", "--module", "self"]).output

    assert "the same file again" in out
    assert out.count("- one") == 2  # the doubling is real; now it is disclosed
