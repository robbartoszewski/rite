"""The push script must take its verdicts from exit codes, not from pipes.

`tools/verify-and-push.sh` exists because a hand-typed loop piped `pytest`
to `tail`, making `tail`'s 0 the verdict — a red suite would have been
pushed under a line reading "3404 passed". A script fixes that only while it
keeps doing it, and the regression is one careless edit away and invisible
when it happens: a check that never really ran and a check that passed
produce identical output.

So the property is asserted rather than trusted. Each verification command
must redirect to a log and have its exit code captured on the very next
line — not piped, not grepped, not inferred.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify-and-push.sh"

VERDICTS = ("uv run pytest", "rite publish check", "git push origin")


def _code_lines() -> list[str]:
    """Without comments — the whole file discusses pipes, and a test that
    reads prose as code fails on its own explanation."""
    return [
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]


def test_the_script_is_there():
    assert SCRIPT.is_file(), f"{SCRIPT} is gone — the guard below tests nothing"


def test_every_verification_command_has_its_exit_code_captured():
    lines = _code_lines()
    for verdict in VERDICTS:
        at = [i for i, line in enumerate(lines) if verdict in line]
        assert at, f"{verdict!r} no longer appears in {SCRIPT.name}"
        for i in at:
            assert "|" not in lines[i], (
                f"{verdict!r} is piped, so its exit code is the pipeline's "
                f"last stage and not its own:\n  {lines[i].strip()}"
            )
            following = lines[i] + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            assert re.search(r"rc=\$\?", following), (
                f"{verdict!r} runs without its exit code being captured on "
                f"the same or the next line:\n  {lines[i].strip()}"
            )


def test_each_captured_code_is_actually_tested():
    """Capturing `$?` and never reading it is the same defect one step on."""
    body = "\n".join(_code_lines())
    captures = len(re.findall(r"rc=\$\?", body))
    tests = len(re.findall(r"\$rc -(?:ne|eq) \d", body))
    assert tests >= captures, (
        f"{captures} exit codes captured but only {tests} tested — a captured "
        "code nobody branches on is a code nobody read"
    )


def test_the_script_never_forces_a_push():
    body = "\n".join(_code_lines())
    assert "--force" not in body and "-f origin" not in body, (
        "a force push discards whatever another session landed; this script "
        "rebases and re-verifies instead"
    )


def test_a_rebase_is_followed_by_re_verification():
    """The commits a rebase lands on are not the ones the suite just ran
    against, so pushing straight after one pushes an unverified tree."""
    lines = _code_lines()
    rebase = next(i for i, line in enumerate(lines) if "pull --rebase" in line)
    after = "\n".join(lines[rebase:])
    assert "uv run pytest" not in after, (
        "the rebase must fall at the END of the loop body so the next "
        "iteration re-runs the suite, rather than being followed by a push"
    )
    loop = next(i for i, line in enumerate(lines) if line.startswith("for attempt"))
    assert loop < rebase, "the rebase is outside the retry loop, so nothing re-verifies"


def test_no_line_pipes_anything_except_reading_a_log():
    """The check above names three commands, so a FOURTH one added later and
    piped would pass it. This one needs no list.

    A guard that enumerates what it forbids goes quietly out of date as the
    thing it guards grows — the same shape as the eleventh rule one level up:
    it must fail when its own premise stops holding, not only when the
    behaviour it watches goes wrong. So the property is stated over the whole
    file: a pipe is allowed only where it formats an already-written log, and
    nowhere a command's exit code could be swallowed.
    """
    allowed = "/tmp/rite-verify-"
    for line in _code_lines():
        if "|" not in line or "||" in line:
            continue
        assert allowed in line, (
            "a pipe outside log formatting swallows the exit code of "
            f"whatever runs left of it:\n  {line.strip()}"
        )


def test_the_commands_this_file_names_still_exist_in_the_script():
    """The premise of `test_every_verification_command_has_its_exit_code_captured`.

    If the script is reworded — `python -m pytest`, a different gate command —
    that test starts checking for text that exists nowhere and passes for ever
    while guarding nothing. It already asserts presence; this states the
    dependency as its own named failure so the reason is legible when it goes
    red, rather than arriving as a confusing assertion inside another test.
    """
    body = "\n".join(_code_lines())
    for verdict in VERDICTS:
        assert verdict in body, (
            f"{verdict!r} is gone from {SCRIPT.name}. If the script now runs "
            "something else, update VERDICTS here — until then the exit-code "
            "guard above is checking for a command that is not there"
        )
