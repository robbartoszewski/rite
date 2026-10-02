#!/usr/bin/env python3
"""Every test passes in at least one CI job, or says why it runs nowhere.

🔴 **WHY THIS EXISTS.** CI's coverage was a list of filenames typed into the
macOS job, and a list does not grow when a test is added. Measured on this
repository's `main`: five test files skip everything behind
`sys.platform != "darwin"` and are not named in that list — among them
`tests/test_no_manager_reads_another_managers_mail.py`, the eleven tests that
no Manager can read another Manager's inbox, outbox or state (DF3). The Linux
job skipped them, the macOS job never collected them, and both jobs were
green. The property was enforced nowhere, and nothing in CI could say so.

A guard over that list would have caught those five files and missed the
tests that skip INSIDE a file the list does name — a `skipif` on something
the runner happens not to have. So this does not check the list. It checks
the property: **did this test pass in at least one job?** — read from what
the jobs actually did (their junit reports), not from what the workflow says
they do.

    python3 tools/every_test_passes_somewhere.py \\
        --runs-nowhere tests/ci_runs_nowhere.toml \\
        lint-and-test-3.12=reports/junit-lint-and-test-3.12/junit.xml \\
        boundary-and-credentials-on-macos=reports/junit-...-macos/junit.xml

Each `label=path` is a report this run REQUIRES. A missing, unreadable or
empty report exits 2 and says CANNOT TELL — never "no tests": a zero counted
from a file that is not there is this repository's signature defect
(`DEFECT_CLASSES.md` class 1), and a job that never reached pytest is not a
job whose tests all passed.

**The rules, each a failure (exit 1):**
- a test that passed in no report and is not in the runs-nowhere list;
- a runs-nowhere entry for a test that passed somewhere — it runs now, and
  leaving the entry would hide it the day it stops;
- a runs-nowhere entry for a test that FAILED: the entry claims CI cannot run
  it, and CI ran it;
- a runs-nowhere entry no report collected at all: deleted, renamed, or never
  spelled the way the list spells it;
- a runs-nowhere reason too short to be a reason.

A test that failed everywhere is named apart from one that only skipped: its
own job is already red and the cause is different. A test that fails in one
job and passes in another is NOT reported here — that job's red is the
signal, and this guard is about coverage.

**It needs no list of macOS-only tests.** Such a test skips on Linux, so if
the macOS job is ever narrowed — a `-k`, a file list, a job that does not
start — the test passes nowhere and this goes red by itself.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PASSED, SKIPPED, FAILED = "passed", "skipped", "failed"

# Worst wins when one test id reaches junit more than once: a passing call
# and an erroring teardown are two `<testcase>` elements with one id, and the
# error is what the run means.
SEVERITY = {PASSED: 0, SKIPPED: 1, FAILED: 2}

# A reason says what the test needs and why CI cannot give it. Four words is a
# floor against "todo" and "flaky", not a measure of quality.
MIN_REASON_WORDS = 4


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str = ""


class CannotTell(Exception):
    """A report this run requires is missing, unreadable or empty."""


def node_id(classname: str, name: str) -> str:
    """pytest's node id, rebuilt from junit's `classname` and `name`.

    xunit2 — pytest's default family — carries no `file` attribute, so the
    path has to come back out of the dotted `classname`. `tests/` is flat
    (`test_the_guard_reads_a_flat_tests_directory` fails if it stops being),
    so `tests.test_x.TestA` is `tests/test_x.py::TestA`. Anything else is
    left as junit wrote it: it still matches itself across reports, which is
    all this tool compares, rather than being guessed into a wrong path.
    """
    parts = classname.split(".")
    if len(parts) >= 2 and parts[0] == "tests":
        return "::".join([f"tests/{parts[1]}.py", *parts[2:], name])
    return f"{classname}::{name}" if classname else name


def read_report(path: Path) -> dict[str, Outcome]:
    """One job's junit report as id → outcome, or CannotTell."""
    if not path.is_file():
        raise CannotTell(f"{path}: no such report")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise CannotTell(f"{path}: not readable as junit XML ({e})") from e

    outcomes: dict[str, Outcome] = {}
    for case in root.iter("testcase"):
        test = node_id(case.get("classname", ""), case.get("name", ""))
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        skipped = case.find("skipped")
        if problem is not None:
            found = Outcome(FAILED, (problem.get("message") or "")[:200])
        elif skipped is not None:
            # An xfail also arrives as `skipped`, with its own type. Reading
            # it as a skip is deliberate: a test expected to fail has not
            # passed anywhere either.
            found = Outcome(SKIPPED, (skipped.get("message") or "")[:200])
        else:
            found = Outcome(PASSED)
        seen = outcomes.get(test)
        if seen is None or SEVERITY[found.status] > SEVERITY[seen.status]:
            outcomes[test] = found
    if not outcomes:
        raise CannotTell(f"{path}: holds no test cases")
    return outcomes


def read_runs_nowhere(path: Path) -> dict[str, str]:
    """The declared debts as id → reason, or CannotTell."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise CannotTell(f"{path}: not readable ({e})") from e
    entries = data.get("runs_nowhere", {})
    if not isinstance(entries, dict) or not all(
        isinstance(reason, str) for reason in entries.values()
    ):
        raise CannotTell(f"{path}: [runs_nowhere] must map a test id to a reason")
    return entries


def judge(
    reports: dict[str, dict[str, Outcome]], runs_nowhere: dict[str, str]
) -> list[str]:
    """Every problem, one line each. An empty list means the property holds."""
    problems: list[str] = []
    seen: dict[str, dict[str, Outcome]] = {}
    for label, outcomes in reports.items():
        for test, outcome in outcomes.items():
            seen.setdefault(test, {})[label] = outcome

    def where(test: str) -> str:
        said = []
        for label in sorted(reports):
            outcome = seen[test].get(label)
            if outcome is None:
                said.append(f"{label}: not collected")
            else:
                detail = f" ({outcome.detail})" if outcome.detail else ""
                said.append(f"{label}: {outcome.status}{detail}")
        return "; ".join(said)

    for test in sorted(seen):
        statuses = {outcome.status for outcome in seen[test].values()}
        listed = test in runs_nowhere
        if PASSED in statuses:
            if listed:
                problems.append(
                    f"STALE: {test} is listed as running nowhere, and passed. "
                    f"{where(test)}. Remove its entry."
                )
            continue
        if FAILED in statuses:
            kind = "LISTED AND FAILED" if listed else "FAILED"
            problems.append(f"{kind}: {test} — {where(test)}")
            continue
        if not listed:
            problems.append(f"PASSED IN NO JOB: {test} — {where(test)}")

    for test, reason in sorted(runs_nowhere.items()):
        if test not in seen:
            problems.append(
                f"GONE: {test} is listed as running nowhere, and no report "
                "collected it under that id. Remove or rename its entry."
            )
        if len(reason.split()) < MIN_REASON_WORDS:
            problems.append(f"NO REASON: {test}: {reason!r} is not a reason")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs-nowhere", type=Path, required=True)
    parser.add_argument("reports", nargs="+", metavar="LABEL=PATH")
    args = parser.parse_args(argv)

    try:
        reports: dict[str, dict[str, Outcome]] = {}
        for spec in args.reports:
            label, sep, path = spec.partition("=")
            if not sep or not label or not path:
                raise CannotTell(f"{spec!r}: expected LABEL=PATH")
            if label in reports:
                raise CannotTell(f"{label}: named twice")
            reports[label] = read_report(Path(path))
        runs_nowhere = read_runs_nowhere(args.runs_nowhere)
    except CannotTell as e:
        print(f"CANNOT TELL whether every test ran: {e}")
        print(
            "A report is missing when its job did not reach its test step — "
            "lint failed, the job was cancelled, the upload did not run. That "
            "is not zero tests, and this says so rather than passing."
        )
        return 2

    for label, outcomes in sorted(reports.items()):
        counts = dict.fromkeys(SEVERITY, 0)
        for outcome in outcomes.values():
            counts[outcome.status] += 1
        tally = ", ".join(f"{n} {status}" for status, n in counts.items())
        print(f"{label}: {len(outcomes)} collected, {tally}")
    distinct = len({test for outcomes in reports.values() for test in outcomes})
    print(f"{distinct} distinct tests; {len(runs_nowhere)} listed as running nowhere.")

    problems = judge(reports, runs_nowhere)
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for line in problems:
            print(f"  {line}")
        return 1
    print("Every test passed in at least one job, or says why it runs nowhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
