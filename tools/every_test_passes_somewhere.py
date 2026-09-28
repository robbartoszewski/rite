#!/usr/bin/env python3
"""Every test passes in at least one CI job, or says why it runs nowhere.

🔴 **WHY THIS EXISTS.** The macOS job ran a named list of six files. DF3's
test, that no Manager can read another Manager's mail, was not on it, and it
skips on Linux. So it ran nowhere, and CI was green on `e30518b` without it.
The same audit found five more tests like it, and two of them were in files
the list DID name: they skipped there. A guard over the file list would have
caught the first and missed those two. The property that matters is not "is
this file named", it is **"did this test pass somewhere"**, and it is read
here from what the jobs actually did (their junit reports), not from what the
workflow says they do.

    python3 tools/every_test_passes_somewhere.py \\
        --runs-nowhere tests/ci_runs_nowhere.toml \\
        linux-3.12=reports/junit-linux-3.12/junit.xml macos=reports/...

Each `label=path` is a report this run REQUIRES. A missing, unreadable or empty
report fails as "cannot tell", never as "no tests": a zero read from a file
that is not there is the class-2 defect this repository keeps meeting.

**The rules, each a failure:**
- a test that passed in no report and is not in the runs-nowhere list;
- a runs-nowhere entry for a test that passed somewhere (stale: it runs now,
  and the entry would hide it if it stopped again);
- a runs-nowhere entry for a test no report collected (gone, or renamed);
- a runs-nowhere reason too short to be one.

A test that FAILED is listed apart from one that only skipped, because its
own job is already red and the cause is different.

**It protects itself against a narrowed job.** A test that only a Mac can run
skips on Linux. If the macOS job stops running it (a `-k`, a file list, a
job that never started), that test passes nowhere, and this fails. Nothing
has to remember which tests are macOS-only.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PASSED, SKIPPED, FAILED = "passed", "skipped", "failed"

# A reason is what the test needs and why CI cannot give it. Four words is a
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

    xunit2 (pytest's default family) carries no `file` attribute. `tests/` is
    flat, so `tests.test_x.TestA.TestB` is `tests/test_x.py::TestA::TestB`.
    Anything else is kept as junit wrote it, so it still matches itself
    across reports rather than being guessed into a wrong path.
    """
    parts = classname.split(".")
    if len(parts) >= 2 and parts[0] == "tests":
        return "::".join([f"tests/{parts[1]}.py", *parts[2:], name])
    return f"{classname}::{name}" if classname else name


def read_report(path: Path) -> dict[str, Outcome]:
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
            found = Outcome(SKIPPED, (skipped.get("message") or "")[:200])
        else:
            found = Outcome(PASSED)
        # A teardown error is a second entry for the same test: the worse
        # outcome is the one that stands.
        if test not in outcomes or outcomes[test].status == PASSED:
            outcomes[test] = found
    if not outcomes:
        raise CannotTell(f"{path}: holds no test cases")
    return outcomes


def read_runs_nowhere(path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise CannotTell(f"{path}: not readable ({e})") from e
    entries = data.get("runs_nowhere", {})
    if not isinstance(entries, dict) or not all(
        isinstance(v, str) for v in entries.values()
    ):
        raise CannotTell(f"{path}: [runs_nowhere] must map a test id to a reason")
    return entries


def judge(
    reports: dict[str, dict[str, Outcome]], runs_nowhere: dict[str, str]
) -> list[str]:
    """Every problem, as one line each. Empty means the property holds."""
    problems: list[str] = []
    seen: dict[str, dict[str, Outcome]] = {}
    for label, outcomes in reports.items():
        for test, outcome in outcomes.items():
            seen.setdefault(test, {})[label] = outcome

    def where(test: str) -> str:
        said = []
        for label in sorted(reports):
            o = seen[test].get(label)
            if o is None:
                said.append(f"{label}: not collected")
            else:
                said.append(
                    f"{label}: {o.status}" + (f" ({o.detail})" if o.detail else "")
                )
        return "; ".join(said)

    for test in sorted(seen):
        statuses = {o.status for o in seen[test].values()}
        if PASSED in statuses:
            if test in runs_nowhere:
                problems.append(
                    f"STALE: {test} is listed as running nowhere, and passed "
                    f"({where(test)}). Remove its entry."
                )
            continue
        if test in runs_nowhere:
            continue
        kind = "FAILED" if FAILED in statuses else "PASSED IN NO JOB"
        problems.append(f"{kind}: {test} — {where(test)}")

    for test, reason in sorted(runs_nowhere.items()):
        if test not in seen:
            problems.append(
                f"GONE: {test} is listed as running nowhere, and no report "
                "collected it. Remove or rename its entry."
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
            "A report is missing when its job did not reach its test step "
            "(lint failed, the job was cancelled, the upload failed). That is "
            "not zero tests."
        )
        return 2

    problems = judge(reports, runs_nowhere)
    total = len({t for o in reports.values() for t in o})
    for label, outcomes in sorted(reports.items()):
        counts = {s: 0 for s in (PASSED, SKIPPED, FAILED)}
        for o in outcomes.values():
            counts[o.status] += 1
        print(
            f"{label}: {len(outcomes)} collected, "
            + ", ".join(f"{n} {s}" for s, n in counts.items())
        )
    print(f"{total} distinct tests; {len(runs_nowhere)} listed as running nowhere.")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for line in problems:
            print(f"  {line}")
        return 1
    print("Every test passed in at least one job, or says why it runs nowhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
