"""The guard that every test passes in some CI job, against the cases it is for.

`tools/every_test_passes_somewhere.py` reads the jobs' junit reports. The shape
it must catch is the one this repository shipped: DF3's mail test skipped on
Linux and was collected by no macOS job, so it passed nowhere while every
check was green. The headline case below is that shape.

The cases are built as junit a real pytest writes — xunit2 carries `classname`
and `name` and no `file` attribute, which is why the tool has to rebuild the
node id and why `test_node_ids_match_what_pytest_prints` pins the rebuilding.

⚠ **The second half reads `ci.yml`**, because a guard the workflow never calls,
or calls with fewer reports than there are jobs, or calls over a narrowed run,
is this same defect one level up: a check that cannot see what it claims to.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "every_test_passes_somewhere.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
RUNS_NOWHERE = REPO / "tests" / "ci_runs_nowhere.toml"
GUARD = "every-test-passes-somewhere"

MAIL = "tests.test_no_manager_reads_another_managers_mail"
MAIL_ID = (
    "tests/test_no_manager_reads_another_managers_mail.py::test_nor_a_siblings_outbox"
)
MAIL_SKIPPED = (MAIL, "test_nor_a_siblings_outbox", "skipped", "seatbelt is macOS only")
MAIL_PASSED = (MAIL, "test_nor_a_siblings_outbox", "passed", "")
# Every report needs at least one case, or the tool says CANNOT TELL before it
# gets as far as judging anything — which is its own test, further down.
OTHER = ("tests.test_other", "test_ok", "passed", "")
REASON = '"needs a real claude binary, which no runner has"'  # already TOML


def _tool():
    spec = spec_from_file_location("every_test_passes_somewhere", TOOL)
    module = module_from_spec(spec)
    # Registered before execution: a dataclass looks its own module up in
    # `sys.modules` while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _tool()


def _junit(path: Path, cases: list[tuple[str, str, str, str]]) -> Path:
    """(classname, name, status, message) → a junit file shaped like pytest's."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
    ]
    for classname, name, status, message in cases:
        said = quoteattr(message)
        inner = {
            "passed": "",
            "skipped": f'<skipped type="pytest.skip" message={said}/>',
            "failed": f"<failure message={said}>trace</failure>",
            "error": f"<error message={said}>trace</error>",
        }[status]
        attrs = f'classname={quoteattr(classname)} name={quoteattr(name)} time="0.1"'
        lines.append(f"<testcase {attrs}>{inner}</testcase>")
    lines.append("</testsuite></testsuites>")
    path.write_text("".join(lines))
    return path


def _run(tmp_path, reports: dict[str, list], runs_nowhere: str = "", capsys=None):
    listed = tmp_path / "runs_nowhere.toml"
    listed.write_text("[runs_nowhere]\n" + runs_nowhere)
    args = ["--runs-nowhere", str(listed)]
    for label, cases in reports.items():
        args.append(f"{label}={_junit(tmp_path / f'{label}.xml', cases)}")
    code = tool.main(args)
    return code, capsys.readouterr().out if capsys else ""


# ---- the shape that shipped ------------------------------------------------


def test_the_df3_shape_fails_and_names_the_test_and_every_job(tmp_path, capsys):
    """Skipped on Linux, collected by no macOS job: what was green for weeks."""
    code, out = _run(
        tmp_path,
        {"linux": [MAIL_SKIPPED, OTHER], "macos": [OTHER]},
        capsys=capsys,
    )
    assert code == 1
    assert f"PASSED IN NO JOB: {MAIL_ID}" in out
    assert "linux: skipped (seatbelt is macOS only)" in out
    assert "macos: not collected" in out


def test_the_same_test_passing_on_macos_is_enough(tmp_path, capsys):
    """The control for the case above: one passing job and it is covered."""
    code, out = _run(
        tmp_path,
        {"linux": [MAIL_SKIPPED, OTHER], "macos": [MAIL_PASSED, OTHER]},
        capsys=capsys,
    )
    assert code == 0, out


def test_a_test_that_skips_inside_a_file_every_job_collected_is_caught(
    tmp_path, capsys
):
    """What a guard over the macOS job's FILE LIST could not see: the file is
    named, collected everywhere, and the test itself skips in each job."""
    case = ("tests.test_profile.TestGh", "test_config_unreadable", "skipped", "no cfg")
    code, out = _run(
        tmp_path, {"linux": [case, OTHER], "macos": [case, OTHER]}, capsys=capsys
    )
    assert code == 1
    assert (
        "PASSED IN NO JOB: tests/test_profile.py::TestGh::test_config_unreadable" in out
    )


# ---- the declared debts ---------------------------------------------------


def test_a_listed_test_with_a_reason_is_accepted(tmp_path, capsys):
    case = ("tests.test_x", "test_needs_claude", "skipped", "no claude")
    code, out = _run(
        tmp_path,
        {"linux": [case, OTHER]},
        f'"tests/test_x.py::test_needs_claude" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 0, out


def test_a_listed_test_that_passes_again_is_stale(tmp_path, capsys):
    """Left standing, the entry would hide the test the day it stopped."""
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "passed", "")]},
        f'"tests/test_x.py::test_y" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 1
    assert "STALE: tests/test_x.py::test_y" in out


def test_a_listed_test_no_report_collected_is_gone(tmp_path, capsys):
    """Renamed or deleted: the entry now excuses nothing that exists."""
    code, out = _run(
        tmp_path,
        {"linux": [OTHER]},
        f'"tests/test_x.py::test_renamed" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 1
    assert "GONE: tests/test_x.py::test_renamed" in out


def test_a_listed_test_that_actually_failed_is_not_excused(tmp_path, capsys):
    """The entry says CI cannot run it. CI ran it, and it failed."""
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "failed", "boom"), OTHER]},
        f'"tests/test_x.py::test_y" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 1
    assert "LISTED AND FAILED: tests/test_x.py::test_y" in out


def test_a_reason_must_say_something(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "skipped", ""), OTHER]},
        '"tests/test_x.py::test_y" = "flaky"\n',
        capsys=capsys,
    )
    assert code == 1
    assert "NO REASON: tests/test_x.py::test_y" in out


# ---- a failure is not a gap, and neither is hidden ------------------------


def test_a_failure_is_named_apart_from_a_skip(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "failed", "boom"), OTHER]},
        capsys=capsys,
    )
    assert code == 1
    assert "FAILED: tests/test_x.py::test_y" in out
    assert "PASSED IN NO JOB" not in out


def test_a_failure_in_one_job_and_a_pass_in_another_is_that_jobs_red(tmp_path, capsys):
    """Coverage is what this guard measures. The failing job is already red,
    and reporting it here would make the guard go red for a second reason."""
    code, out = _run(
        tmp_path,
        {
            "linux": [("tests.test_x", "test_y", "failed", "boom"), OTHER],
            "macos": [("tests.test_x", "test_y", "passed", ""), OTHER],
        },
        capsys=capsys,
    )
    assert code == 0, out


@pytest.mark.parametrize(
    "error_first", [False, True], ids=["pass-then-error", "error-then-pass"]
)
def test_a_teardown_error_is_not_hidden_by_a_pass_for_the_same_id(
    tmp_path, capsys, error_first
):
    """A passing call and an erroring teardown reach junit as two `testcase`
    elements with one id. Whichever order they arrive in, the error stands."""
    cases = [
        ("tests.test_x", "test_y", "passed", ""),
        ("tests.test_x", "test_y", "error", "teardown"),
    ]
    if error_first:
        cases.reverse()
    code, out = _run(tmp_path, {"linux": [*cases, OTHER]}, capsys=capsys)
    assert code == 1
    assert "FAILED: tests/test_x.py::test_y" in out


@pytest.mark.parametrize(
    "error_first", [False, True], ids=["skip-then-error", "error-then-skip"]
)
def test_a_teardown_error_is_not_hidden_by_a_skip_either(tmp_path, capsys, error_first):
    cases = [
        ("tests.test_x", "test_y", "skipped", "not here"),
        ("tests.test_x", "test_y", "error", "teardown"),
    ]
    if error_first:
        cases.reverse()
    code, out = _run(tmp_path, {"linux": [*cases, OTHER]}, capsys=capsys)
    assert code == 1
    assert "FAILED: tests/test_x.py::test_y" in out


# ---- a number read from a file that is not there -------------------------


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda p: None, id="missing"),
        pytest.param(lambda p: p.write_text("<testsuites><testsuite>"), id="truncated"),
        pytest.param(
            lambda p: p.write_text("<testsuites><testsuite/></testsuites>"), id="empty"
        ),
    ],
)
def test_a_report_that_cannot_be_read_is_cannot_tell_not_zero(tmp_path, capsys, make):
    """A job that never reached pytest leaves no report. That is not a job
    whose tests all passed, and not a job with no tests."""
    good = _junit(tmp_path / "good.xml", [OTHER])
    bad = tmp_path / "bad.xml"
    make(bad)
    listed = tmp_path / "n.toml"
    listed.write_text("[runs_nowhere]\n")
    assert (
        tool.main(["--runs-nowhere", str(listed), f"linux={good}", f"macos={bad}"]) == 2
    )
    assert "CANNOT TELL" in capsys.readouterr().out


def test_a_malformed_runs_nowhere_list_is_cannot_tell(tmp_path, capsys):
    listed = tmp_path / "n.toml"
    listed.write_text('[runs_nowhere]\n"tests/test_x.py::test_y" = unquoted words\n')
    good = _junit(tmp_path / "good.xml", [OTHER])
    assert tool.main(["--runs-nowhere", str(listed), f"linux={good}"]) == 2
    assert "CANNOT TELL" in capsys.readouterr().out


@pytest.mark.parametrize("spec", ["reports/junit.xml", "=reports/junit.xml", "linux="])
def test_a_report_argument_that_is_not_label_equals_path_is_cannot_tell(
    tmp_path, capsys, spec
):
    """The labels are typed into the workflow by hand; a typo must stop the
    job rather than quietly leaving a job's report out of the comparison."""
    listed = tmp_path / "n.toml"
    listed.write_text("[runs_nowhere]\n")
    assert tool.main(["--runs-nowhere", str(listed), spec]) == 2
    assert "CANNOT TELL" in capsys.readouterr().out


def test_one_label_naming_two_reports_is_cannot_tell(tmp_path, capsys):
    """Otherwise the second silently replaces the first, and a job that was
    required to report is no longer in the comparison at all."""
    good = _junit(tmp_path / "good.xml", [OTHER])
    listed = tmp_path / "n.toml"
    listed.write_text("[runs_nowhere]\n")
    code = tool.main(["--runs-nowhere", str(listed), f"linux={good}", f"linux={good}"])
    assert code == 2
    assert "named twice" in capsys.readouterr().out


# ---- the node ids this all turns on --------------------------------------


def test_node_ids_match_what_pytest_prints():
    assert tool.node_id(MAIL, "test_nor_a_siblings_outbox") == MAIL_ID
    assert (
        tool.node_id(
            f"{MAIL}.TestABoxUnderRiteHomeIsMovedOut",
            "test_everything_moves_and_nothing_is_left_readable",
        )
        == "tests/test_no_manager_reads_another_managers_mail.py::"
        "TestABoxUnderRiteHomeIsMovedOut::"
        "test_everything_moves_and_nothing_is_left_readable"
    )
    # A parameter id carrying a dot must not be read as a package boundary.
    assert tool.node_id("tests.test_x", "test_y[a.b]") == "tests/test_x.py::test_y[a.b]"


def test_the_guard_reads_a_flat_tests_directory():
    """`node_id` turns `tests.test_x` into `tests/test_x.py`, which is only
    right while no test module lives in a subdirectory of `tests/`. This is
    the assumption, asserted rather than assumed: a `tests/unit/test_x.py`
    would arrive as `tests.unit.test_x` and be rebuilt as
    `tests/unit.py::test_x`, an id nothing else would ever print."""
    nested = [
        path
        for path in (REPO / "tests").rglob("test_*.py")
        if path.parent != REPO / "tests"
    ]
    assert nested == [], nested


def test_the_committed_list_parses_and_every_reason_says_something():
    """Read with the tool's own reader, so the committed file is held to the
    same shape CI holds it to."""
    for test, reason in tool.read_runs_nowhere(RUNS_NOWHERE).items():
        assert test.startswith("tests/test_") and "::" in test, test
        assert len(reason.split()) >= tool.MIN_REASON_WORDS, test


# ---- the workflow calls the guard, over every job, un-narrowed ------------


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _pytest_jobs(jobs: dict) -> dict[str, dict]:
    return {
        name: job
        for name, job in jobs.items()
        if any("pytest" in (step.get("run") or "") for step in job.get("steps", []))
    }


def test_every_job_that_runs_pytest_runs_the_whole_suite_into_a_report():
    """A report from a narrowed run is still a report, and the tool cannot
    tell one from a whole run. So each pytest job needs exactly one step that
    runs everything and writes junit; labelled runs of a few files may sit
    beside it, as the Landlock probes step does."""
    jobs = _workflow()["jobs"]
    assert _pytest_jobs(jobs), "no job runs pytest at all"
    for name, job in _pytest_jobs(jobs).items():
        whole = [
            step
            for step in job["steps"]
            if "--junitxml=" in (step.get("run") or "")
            and not any(
                narrowing in step["run"]
                for narrowing in (" -k ", " -m ", "--deselect", "tests/")
            )
        ]
        assert len(whole) == 1, f"{name}: not exactly one whole-suite pytest step"


def test_the_guard_needs_every_pytest_job_and_runs_even_when_they_fail():
    jobs = _workflow()["jobs"]
    guard = jobs[GUARD]
    needs = guard["needs"] if isinstance(guard["needs"], list) else [guard["needs"]]
    assert set(needs) == set(_pytest_jobs(jobs)), needs
    assert guard.get("if") == "always()", (
        "a job that runs no tests of its own is skipped when its needs fail, "
        "and a skipped check reports nothing where this one is most needed"
    )


def test_the_guard_requires_one_report_per_job_and_matrix_entry():
    """The labels are typed into the command by hand. This derives what they
    have to be from the jobs themselves, so a new job or Python version
    cannot be added and left out of the comparison."""
    jobs = _workflow()["jobs"]
    expected = set()
    for name, job in _pytest_jobs(jobs).items():
        versions = job.get("strategy", {}).get("matrix", {}).get("python-version")
        expected |= {f"{name}-{v}" for v in versions} if versions else {name}
    command = " ".join(step.get("run") or "" for step in jobs[GUARD]["steps"])
    assert "tools/every_test_passes_somewhere.py" in command
    assert "--runs-nowhere tests/ci_runs_nowhere.toml" in command
    labels = {
        word.split("=", 1)[0]
        for word in command.replace("\\", " ").split()
        if "=" in word and word.endswith(".xml")
    }
    assert labels == expected, sorted(labels ^ expected)


def test_every_pytest_job_keeps_its_report_even_when_the_tests_fail():
    """Without `if: always()` on the upload, a red test job leaves no report,
    and the guard's answer becomes CANNOT TELL for a run that did measure
    something — the failure it would hide is the one worth reading."""
    jobs = _workflow()["jobs"]
    for name, job in _pytest_jobs(jobs).items():
        uploads = [
            step
            for step in job["steps"]
            if "upload-artifact" in (step.get("uses") or "")
            and (step.get("with") or {}).get("path") == "junit.xml"
        ]
        assert len(uploads) == 1, f"{name}: not exactly one junit upload"
        assert uploads[0].get("if") == "always()", f"{name}: upload is conditional"
