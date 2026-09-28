"""Every test that knows about macOS is run by the macOS job, or says why not.

**The defect this guards against, found 2026-09-28.** The macOS boundary job in
`.github/workflows/ci.yml` runs a NAMED LIST of test files, for a stated reason
(a hosted runner lacks tools, and a whole-suite run there would report skips as
green). But a list does not grow when a test is added. Three test files were
skipped everywhere except macOS and were not on it:
- DF3's mail fence (`test_no_manager_reads_another_managers_mail.py`);
- CU4's key file (`test_a_cursor_manager_gets_its_key_as_a_file.py`);
- CU8's allowlist (`test_cursor_allowlist_is_ritess_not_the_managers.py`).
The Linux job skipped them, the macOS job never ran them, and both went green.
Adding a test did not mean it ran.

**So the list is checked against the files, not the other way round**
(`DEFECT_CLASSES.md`: when a class recurs, the fix is a guard that works out its
own list). Every test file that names `darwin` or `sandbox-exec` must be either
in the macOS job's list, or in `NOT_IN_THE_MACOS_JOB` below with the reason it
does not need to be. The match is deliberately broad: a file that merely
mentions the platform costs one written sentence, and a file that is gated on
it can no longer be missed silently.

**What this does NOT catch, stated rather than hidden:** a test that is only
meaningful on macOS but names neither `darwin` nor `sandbox-exec` (for example,
one gated on a helper whose name hides the platform). If such a gate is ever
written, name the platform in it, or add the file to the job by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
JOB = "boundary-and-credentials-on-macos"

NOT_IN_THE_MACOS_JOB: dict[str, str] = {
    "tests/test_the_macos_job_runs_every_macos_test.py": (
        "this guard; it names the platform to look for it, and it reads files, "
        "so it runs in the Linux job's full suite"
    ),
    "tests/test_sandbox.py": (
        "asserts that `sandbox-exec` is ABSENT from a Worker's command; nothing "
        "in it is gated on macOS, so the Linux job runs all of it"
    ),
    "tests/test_scheduler.py": (
        "branches on the platform to build the expected unit (launchd or "
        "systemd) rather than skipping, so the Linux job runs every test"
    ),
    "tests/test_init_sandbox_branches.py": (
        "patches `sys.platform` to `darwin` itself, so the macOS branches run "
        "on any host, including the Linux job"
    ),
    "tests/test_landlock_really_confines.py": (
        "the LINUX boundary (Landlock); it mentions seatbelt only in prose, "
        "and it runs in the Linux job"
    ),
}

_MENTIONS_MACOS = re.compile(r"""["']darwin["']|sandbox-exec""")


def _macos_job_files() -> set[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = (workflow.get("jobs") or {}).get(JOB)
    assert job, f"{WORKFLOW.name} has no job {JOB!r}: this guard cannot check it"
    runs = [
        step.get("run", "")
        for step in job.get("steps") or []
        if "pytest" in (step.get("run") or "")
    ]
    assert runs, f"the {JOB} job has no pytest step: this guard cannot check it"
    files = {m for run in runs for m in re.findall(r"tests/[\w/]+\.py", run)}
    assert files, f"the {JOB} job's pytest step names no test file"
    return files


def _test_files() -> list[str]:
    return sorted(str(p.relative_to(REPO)) for p in (REPO / "tests").rglob("test_*.py"))


def _mentions_macos(path: str) -> bool:
    return bool(_MENTIONS_MACOS.search((REPO / path).read_text(errors="replace")))


def test_every_test_that_knows_macos_is_run_there_or_says_why_not():
    listed = _macos_job_files()
    missing = [
        path
        for path in _test_files()
        if _mentions_macos(path)
        and path not in listed
        and path not in NOT_IN_THE_MACOS_JOB
    ]
    assert not missing, (
        f"these test files name darwin or sandbox-exec, and the {JOB} job does "
        f"not run them: {missing}. A test skipped everywhere except macOS and "
        "absent from that job runs NOWHERE in CI, and both jobs go green. Add "
        "each to the job's pytest list in .github/workflows/ci.yml, or to "
        "NOT_IN_THE_MACOS_JOB in this file with the reason it does not need it."
    )


def test_the_job_lists_only_files_that_exist():
    """A renamed or deleted file left in the list makes pytest error, but only
    on macOS, where nobody running the suite locally on Linux would see it."""
    gone = sorted(p for p in _macos_job_files() if not (REPO / p).is_file())
    assert not gone, f"the {JOB} job names files that do not exist: {gone}"


def test_every_exclusion_is_current_and_has_a_reason():
    listed = _macos_job_files()
    for path, reason in NOT_IN_THE_MACOS_JOB.items():
        assert (REPO / path).is_file(), f"{path} is excluded but no longer exists"
        assert path not in listed, f"{path} is both run by the job and excluded"
        assert _mentions_macos(path), (
            f"{path} no longer names darwin or sandbox-exec; drop its exclusion"
        )
        assert len(reason.split()) >= 5, f"{path}'s exclusion gives no real reason"


def test_the_guard_finds_what_it_was_written_for():
    """A guard whose match found nothing would pass everything. These three
    are the files that were missing when this was written."""
    for path in (
        "tests/test_no_manager_reads_another_managers_mail.py",
        "tests/test_a_cursor_manager_gets_its_key_as_a_file.py",
        "tests/test_cursor_allowlist_is_ritess_not_the_managers.py",
    ):
        assert _mentions_macos(path), f"the match no longer sees {path}"
