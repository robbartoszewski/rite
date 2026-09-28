"""The guard that every test passes in some CI job, against the cases it is for.

`tools/every_test_passes_somewhere.py` reads the jobs' junit reports. What
it must catch is the shape found on `e30518b`: DF3's mail test skipped on
Linux and absent from the macOS job, so it passed nowhere while CI was green.
Each case below is built as junit a real pytest writes (xunit2: `classname`
and `name`, no `file`), checked against a run of this suite.

The second half reads `ci.yml`, because a guard the workflow does not call,
or calls with fewer reports than there are jobs, is this defect one level up.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "every_test_passes_somewhere.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
RUNS_NOWHERE = REPO / "tests" / "ci_runs_nowhere.toml"

MAIL = "tests.test_no_manager_reads_another_managers_mail"
MAIL_ID = (
    "tests/test_no_manager_reads_another_managers_mail.py::test_nor_a_siblings_outbox"
)


def _tool():
    spec = spec_from_file_location("every_test_passes_somewhere", TOOL)
    module = module_from_spec(spec)
    # Registered first: a dataclass looks its module up in `sys.modules`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _tool()


def _junit(path: Path, cases: list[tuple[str, str, str, str]]) -> Path:
    """(classname, name, status, message) → a junit file as pytest writes it."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
    ]
    for classname, name, status, message in cases:
        inner = {
            "passed": "",
            "skipped": f'<skipped type="pytest.skip" message="{message}"/>',
            "failed": f'<failure message="{message}">trace</failure>',
            "error": f'<error message="{message}">trace</error>',
        }[status]
        attrs = f'classname="{classname}" name="{name}" time="0.1"'
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


OTHER = ("tests.test_other", "test_ok", "passed", "")
REASON = '"needs a real claude binary, which no runner has"'  # a TOML string


def test_the_df3_shape_fails_and_names_the_test_and_why(tmp_path, capsys):
    """Skipped on Linux, not collected on macOS: what shipped green."""
    code, out = _run(
        tmp_path,
        {
            "linux": [
                (
                    MAIL,
                    "test_nor_a_siblings_outbox",
                    "skipped",
                    "seatbelt is macOS only",
                ),
                OTHER,
            ],
            "macos": [OTHER],
        },
        capsys=capsys,
    )
    assert code == 1
    assert f"PASSED IN NO JOB: {MAIL_ID}" in out
    assert "linux: skipped (seatbelt is macOS only)" in out
    assert "macos: not collected" in out


def test_the_control_passing_on_macos_is_enough(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {
            "linux": [
                (
                    MAIL,
                    "test_nor_a_siblings_outbox",
                    "skipped",
                    "seatbelt is macOS only",
                ),
                OTHER,
            ],
            "macos": [(MAIL, "test_nor_a_siblings_outbox", "passed", ""), OTHER],
        },
        capsys=capsys,
    )
    assert code == 0, out


def test_named_but_skipped_is_caught_too(tmp_path, capsys):
    """F5's shape: the macOS job ran the file and the test skipped there.
    A guard over the file list passes this; this one must not."""
    case = (
        "tests.test_profile.TestGh",
        "test_config_unreadable",
        "skipped",
        "no config.yml",
    )
    code, out = _run(
        tmp_path, {"linux": [case, OTHER], "macos": [case, OTHER]}, capsys=capsys
    )
    assert code == 1
    assert (
        "PASSED IN NO JOB: tests/test_profile.py::TestGh::test_config_unreadable" in out
    )


def test_a_listed_test_with_a_reason_is_accepted(tmp_path, capsys):
    case = ("tests.test_x", "test_needs_claude", "skipped", "no claude")
    code, out = _run(
        tmp_path,
        {"linux": [case, OTHER]},
        f'"tests/test_x.py::test_needs_claude" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 0, out


def test_a_listed_test_that_now_passes_is_stale(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "passed", "")]},
        f'"tests/test_x.py::test_y" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 1
    assert "STALE: tests/test_x.py::test_y" in out


def test_a_listed_test_that_no_report_collected_is_gone(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {"linux": [OTHER]},
        f'"tests/test_x.py::test_renamed" = {REASON}\n',
        capsys=capsys,
    )
    assert code == 1
    assert "GONE: tests/test_x.py::test_renamed" in out


def test_a_reason_must_say_something(tmp_path, capsys):
    case = ("tests.test_x", "test_y", "skipped", "")
    code, out = _run(
        tmp_path,
        {"linux": [case, OTHER]},
        '"tests/test_x.py::test_y" = "flaky"\n',
        capsys=capsys,
    )
    assert code == 1
    assert "NO REASON: tests/test_x.py::test_y" in out


def test_a_failure_is_named_apart_from_a_skip(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        {"linux": [("tests.test_x", "test_y", "failed", "boom"), OTHER]},
        capsys=capsys,
    )
    assert code == 1
    assert "FAILED: tests/test_x.py::test_y" in out


@pytest.mark.parametrize(
    "error_first", [False, True], ids=["pass-then-error", "error-then-pass"]
)
def test_a_teardown_error_is_not_hidden_by_a_pass_for_the_same_id(
    tmp_path, capsys, error_first
):
    """A passed call and a teardown error can reach junit as two testcase
    elements with one id. Whichever order they come in, the error stands."""
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
    whose tests all passed, and not one with no tests."""
    good = _junit(tmp_path / "good.xml", [OTHER])
    bad = tmp_path / "bad.xml"
    make(bad)
    listed = tmp_path / "n.toml"
    listed.write_text("[runs_nowhere]\n")
    code = tool.main(["--runs-nowhere", str(listed), f"linux={good}", f"macos={bad}"])
    assert code == 2
    assert "CANNOT TELL" in capsys.readouterr().out


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
    assert tool.node_id("tests.test_x", "test_y[a.b]") == "tests/test_x.py::test_y[a.b]"


def test_the_committed_runs_nowhere_list_parses_and_every_reason_says_something():
    entries = tool.read_runs_nowhere(RUNS_NOWHERE)
    for test, reason in entries.items():
        assert test.startswith("tests/test_") and "::" in test, test
        assert len(reason.split()) >= tool.MIN_REASON_WORDS, test


# ---- the workflow calls the guard, with every job's report ----------------


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _pytest_jobs(jobs: dict) -> dict[str, dict]:
    return {
        name: job
        for name, job in jobs.items()
        if any("pytest" in (step.get("run") or "") for step in job.get("steps", []))
    }


GUARD = "every-test-passes-somewhere"


def test_every_job_that_runs_pytest_runs_the_whole_suite_into_a_report():
    """A report from a narrowed run (`-k`, a file list) would still be a
    report. Each pytest job needs one step that runs everything and writes
    junit; labelled extra runs of a few files may sit beside it."""
    jobs = _workflow()["jobs"]
    for name, job in _pytest_jobs(jobs).items():
        full = [
            step
            for step in job["steps"]
            if "--junitxml=" in (step.get("run") or "")
            and not any(
                flag in step["run"] for flag in (" -k ", " -m ", "--deselect", "tests/")
            )
        ]
        assert len(full) == 1, (
            f"{name}: no single whole-suite pytest step writing junit"
        )


def test_the_guard_needs_every_pytest_job_and_runs_even_when_they_fail():
    jobs = _workflow()["jobs"]
    guard = jobs[GUARD]
    needs = guard["needs"] if isinstance(guard["needs"], list) else [guard["needs"]]
    assert set(needs) == set(_pytest_jobs(jobs)), needs
    assert guard.get("if") == "always()", (
        "the guard must report when a test job failed; skipped, it says nothing"
    )


def test_the_guard_requires_one_report_per_job_and_matrix_entry():
    """The labels are typed in the guard's command. This derives what they
    must be from the jobs, so a new matrix entry or job cannot be left out."""
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


def test_a_malformed_runs_nowhere_list_is_cannot_tell(tmp_path, capsys):
    listed = tmp_path / "n.toml"
    listed.write_text('[runs_nowhere]\n"tests/test_x.py::test_y" = unquoted words\n')
    good = _junit(tmp_path / "good.xml", [OTHER])
    assert tool.main(["--runs-nowhere", str(listed), f"linux={good}"]) == 2
    assert "CANNOT TELL" in capsys.readouterr().out
