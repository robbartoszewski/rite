"""`rite init --yes` answers eleven questions; it used to print one line
about one of them.

Every answer was defensible on its own — the friction audit checked each
field against the interactive default and found exactly one divergence —
but a project was configured out of detection and defaults with nothing
said, and `.rite/` is not where someone looks for what they did not know
had been decided. This is the report, not the interactive review screen
the `init` redesign is parked on: it asks nothing and changes no default.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import rite_ai.sandbox as sb
from rite_ai.cli.init import run_init


@pytest.fixture
def a_detectable_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "acme"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "acme"\ndescription = "Invoicing for clinics."\n'
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.chdir(root)
    return root


def _run(root: Path, capsys) -> str:
    assert run_init(root, yes=True).status == "created"
    return capsys.readouterr().out


def test_every_resolved_answer_is_named(a_detectable_project, capsys):
    out = _run(a_detectable_project, capsys)

    assert "Resolved without asking" in out
    for key in (
        "project.role",
        "project.name",
        "project.root_branch",
        "what.kind",
        "what.features",
        "technology.platform",
        "technology.languages",
        "technology.frameworks",
        "technology.architecture",
        "operations.ticket_backend",
        "kb.commit",
    ):
        assert key in out, f"{key} was decided and not mentioned"


def test_it_says_which_answers_came_from_the_project_itself(
    a_detectable_project, capsys
):
    """ "detected" and "default" are different promises: one was read off the
    project, the other was rite choosing for you."""
    out = _run(a_detectable_project, capsys)

    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith("  ")}
    assert "[detected]" in lines["project.name"], lines["project.name"]
    assert "Invoicing for clinics." in lines["what.features"]
    assert "[detected]" in lines["what.features"]
    assert "[--yes default]" in lines["technology.architecture"]


def test_the_one_real_divergence_says_what_it_did_not_take(
    a_detectable_project, capsys
):
    """`--yes` uses `none` where the interactive default is JIRA — the only
    place the two disagree, and the audit's actual finding."""
    out = _run(a_detectable_project, capsys)

    line = next(ln for ln in out.splitlines() if "operations.ticket_backend" in ln)
    assert "none" in line
    assert "not the interactive default (jira)" in line, line


def test_an_empty_answer_is_shown_as_empty_not_omitted(a_detectable_project, capsys):
    out = _run(a_detectable_project, capsys)

    line = next(ln for ln in out.splitlines() if "technology.frameworks" in ln)
    assert "—" in line, "an answer resolved to nothing was left off the list"


def test_the_summary_comes_before_anything_is_written(a_detectable_project, capsys):
    out = _run(a_detectable_project, capsys)

    assert out.index("Resolved without asking") < out.index("Created .rite/"), (
        "the answers were reported after the files were already written"
    )


def test_an_interactive_run_is_not_given_the_report(monkeypatch, tmp_path):
    """Whoever typed the answers does not need them read back, and that screen
    is the parked `init` redesign — this must not pre-empt it.

    Driven at the call, not through the prompts: stubbing eleven ui functions
    to walk the interactive path is a test of the stubs, and a missed one
    blocks on real stdin.
    """
    import rite_ai.cli.init.questionnaire as q

    called: list[str] = []
    monkeypatch.setattr(
        q, "_say_what_was_decided", lambda *a: called.append("reported")
    )
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    root = tmp_path / "proj"
    root.mkdir()

    from rite_ai.cli.init.config_file import Preset
    from rite_ai.cli.init.detect import run_detection

    q.run_questionnaire(root, Preset({}), run_detection(root), yes=True)
    assert called == ["reported"], "a --yes run said nothing"

    source = Path(q.__file__).read_text()
    assert "if not interactive:\n        _say_what_was_decided" in source, (
        "the report is no longer behind the non-interactive guard"
    )


def test_the_answers_carry_where_they_came_from(a_detectable_project):
    """The sources are data, not only a printed line — so `doctor` or a later
    command can say the same thing without re-deriving it."""
    from rite_ai.cli.init.config_file import Preset
    from rite_ai.cli.init.detect import run_detection
    from rite_ai.cli.init.questionnaire import run_questionnaire

    answers = run_questionnaire(
        a_detectable_project,
        Preset({}),
        run_detection(a_detectable_project),
        yes=True,
    )

    assert answers.sources["project.name"] == "detected"
    assert answers.sources["technology.architecture"] == "--yes default"
