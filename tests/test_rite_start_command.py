"""`rite start` in the terminal, `/rite-start` in the Claude app.

The symmetry is the feature: a user should not have to compose a paragraph to
get a session working the board, and the two halves should name each other so
that finding one is finding both.

What is worth testing here is not that the file exists — that is visible — but
the four ways this arrives broken while looking finished:

- the command **never reaches an existing project**, because a generated file
  added in one release is delivered to old projects only if the refresh path
  carries it. v0.4.0 shipped exactly this failure in the other direction;
- `rite start` **starts the loop** as a side effect, which is the one thing
  its whole contract forbids and the one thing the request asked for;
- the command **restates** `CLAUDE.md`'s standing instruction instead of
  pointing at it, so the two drift and the stale copy is the one being read;
- it is named `start.md`, colliding with Claude Code's built-in `/start`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.cli.init.claude_gen import _COMMAND_FILES, install_claude_config
from rite_ai.config.models import ProjectBrief, ProjectConfig

from .test_loop_dry_run import project  # noqa: F401

TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "commands" / "rite-start.md"
)


def _brief() -> ProjectBrief:
    return ProjectBrief(name="acme", role="owner", kind="app", languages=["python"])


def _flat(text: str) -> str:
    """Prose assertions collapse whitespace, so a reflowed paragraph is not a
    test failure. The first draft of this file asserted a sentence that the
    template happened to wrap across two lines — a guard that fires on
    formatting teaches people to edit the test."""
    return " ".join(text.split())


# --- the name ----------------------------------------------------------------------


def test_the_command_is_not_called_start():
    """`/start` is a BUILT-IN Claude Code command, and what a project-level
    file of the same name does to a built-in is undocumented — it may shadow
    it, lose to it, or differ by version. A generated file that silently
    breaks `/start` for every rite user is not a trade worth making for four
    characters."""
    assert "rite-start.md" in _COMMAND_FILES
    assert "start.md" not in _COMMAND_FILES
    assert TEMPLATE.is_file()


def test_the_two_halves_name_each_other():
    """The point of the name. `rite start` prints `/rite-start`, and the
    command's own description says it is the other half — someone who found
    either one has found both."""
    assert "`rite start`" in TEMPLATE.read_text().split("---")[1], (
        "the frontmatter description is what a reader sees in the `/` menu, "
        "so that is where the other half has to be named"
    )


def test_the_generated_claude_md_names_the_command(tmp_path: Path):
    """AN ARTIFACT WITH NO READER. The command file was installed into every
    project and named by nothing the project loads: `rite start` printed it,
    and `CLAUDE.md` — the always-loaded context, whose whole job is telling a
    session what is available here — listed four commands and not this one.

    A session that never runs `rite start` would have had no way to learn the
    command exists, while the command's own text tells that session to follow
    `CLAUDE.md`. The symmetry claimed for this feature held in one direction
    only, which round 2 caught.
    """
    install_claude_config(tmp_path, "owner", _brief(), [], ProjectConfig())
    claude_md = (tmp_path / "CLAUDE.md").read_text()

    assert "/rite-start" in claude_md
    commands = claude_md[claude_md.index("## Commands") :]
    assert "/rite-start" in commands, "it belongs in the list, not in passing"


# --- delivery to projects that already exist ---------------------------------------


def test_a_new_project_gets_the_command(tmp_path: Path):
    """The count is taken from the FILES ON DISK, not from the return value.

    This asserted `counts["commands"] == len(_COMMAND_FILES)` first, and
    `install_claude_config` returns that same `len(_COMMAND_FILES)` as a
    literal — so it was `len(X) == len(X)`, true with the copy loop deleted.
    Round 1 caught it. It is the defect family this release ships a check
    for, written into a test guarding the feature.
    """
    counts = install_claude_config(tmp_path, "owner", _brief(), [], ProjectConfig())

    written = sorted(p.name for p in (tmp_path / ".claude" / "commands").glob("*.md"))
    assert "rite-start.md" in written
    assert written == sorted(_COMMAND_FILES)
    assert counts["commands"] == len(written)


def test_an_existing_project_receives_it_on_refresh(tmp_path: Path):
    """THE FAILURE THIS FILE EXISTS FOR. A command added in 0.5.0 is useless
    to every project created by 0.4.0 unless the refresh path carries it, and
    nothing about the feature looks broken from inside a fresh `rite init`.

    So: build the project, delete the file the way an old release would have
    left it absent, and assert the refresh both PLANS the addition and
    performs it.
    """
    from rite_ai.update.refresh import pending, refresh_project

    # A REAL project root, not a bare directory: `refresh_project` parses
    # `.rite/` first and returns nothing at all when it cannot, so the earlier
    # version of this test passed an empty plan and asserted against it — a
    # delivery test that would have stayed green with delivery broken.
    tmp_path = project(tmp_path)
    install_claude_config(tmp_path, "owner", _brief(), [], ProjectConfig())
    target = tmp_path / ".claude" / "commands" / "rite-start.md"
    target.unlink()

    plan = pending(tmp_path)
    planned = [c for c in plan.behind if c.target.endswith("rite-start.md")]
    assert planned, [c.target for c in plan.behind]
    # `installed`, not `refreshed`: the distinction is what makes this
    # deliverable to a project that predates the file rather than only to one
    # holding an older copy of it.
    assert planned[0].action == "installed"

    refresh_project(tmp_path, apply=True)
    assert target.is_file()
    assert target.read_text() == TEMPLATE.read_text()


# --- it points at the standing text rather than copying it -------------------------


def test_it_does_not_restate_working_the_queue(tmp_path: Path):
    """A command that repeats `CLAUDE.md`'s instruction creates a second copy
    that nobody updates.

    THE LINE THIS DRAWS, because the first draft got it wrong in both
    directions. **Commands may appear in both files** — the trigger has to be
    runnable, and `rite status` is the same two words wherever it is written.
    **Judgement may not.** When to take the next ticket, when to stop, when
    starting a Worker is the right call: those live in `CLAUDE.md` and the
    command points at them. A duplicated command line is a typo waiting to
    happen; a duplicated rule is two rules.
    """
    from rite_ai.cli.init.claude_gen import _working_the_queue_section

    command = _flat(TEMPLATE.read_text())
    standing = _flat(_working_the_queue_section())

    assert "Working the queue" in command, "it must point at the section"

    # WHAT THIS GUARD CANNOT DO, said out loud because the alternative is
    # trusting it further than it goes. It matches substrings, so it catches
    # a COPIED rule and is blind to a REWORDED one — round 1 found exactly
    # that: "Starting a Worker is a decision rather than a step" against the
    # standing text's "Starting other sessions is a decision, not a step."
    # There is no property formulation for "the same judgement in different
    # words", so this is a tripwire, not a proof, and the sentence was cut
    # rather than the guard widened.

    for sentence in (
        "One ticket is not the job",
        "Hand back rather than hold",
        "never to fill capacity",
        "Stop when the queue is empty",
    ):
        assert _flat(sentence) in standing, (
            f"{sentence!r} is no longer `CLAUDE.md`'s text — this guard is "
            "now checking for a sentence that does not exist, which is a "
            "guard that cannot fail"
        )
        if _flat(sentence) in command:
            pytest.fail(
                f"{sentence!r} is `CLAUDE.md`'s rule, copied into the command. "
                "Point at the section instead; two copies drift and the one "
                "being read is the stale one."
            )


def test_it_says_why_both_files_exist():
    """The distinction is the thing a reader gets wrong: `CLAUDE.md` is
    always-loaded context and cannot start anything; the command is the
    trigger. Without this paragraph the obvious next change is deleting one of
    them as a duplicate.

    Asserted as a sentence rather than as two loose words: `"CLAUDE.md" in
    text` and `"trigger" in text` both hold for a file that says the
    opposite, which is what this checked first.
    """
    text = _flat(TEMPLATE.read_text())
    assert "`CLAUDE.md` is context" in text
    assert "It cannot start you" in text
    assert "This is the trigger" in text


def test_it_does_not_promise_to_run_forever():
    """It starts work; the standing text keeps it going while it runs; nothing
    restarts it. A command whose text implies otherwise sets up the silence
    that `## Working the queue` is written to prevent."""
    text = _flat(TEMPLATE.read_text())
    assert "does not make the session permanent" in text
    assert "Nothing restarts you when you stop" in text
    # And it must not claim the loop will pick the session back up, which is
    # the specific overclaim available here: the loop is the visible always-on
    # thing, and it starts nothing.
    assert "starts no session" in text


# --- `rite start` reports the loop and does not start it ---------------------------


def test_start_names_both_commands(tmp_path):
    from rite_ai.lifecycle import start

    root = project(tmp_path)
    result = start(root)

    assert result.ok
    joined = "\n".join(result.actions)
    assert "rite loop start" in joined
    assert "/rite-start" in joined


def test_start_does_not_start_the_loop(tmp_path, monkeypatch):
    """`start`'s contract is: perform what is idempotent, local and free;
    report what is persistent. The loop spends no quota — it starts no
    sessions — so it passes the "free" test and FAILS the "local" one: it is a
    background tmux session that outlives the command.

    `start` is also what a session runs to orient itself, so a `start` that
    spawned a loop would spawn one every time any session came up.

    NOT a blanket "spawns no subprocess" assertion, which is what this test
    said first and was wrong: `start` legitimately shells out to `launchctl`
    to answer whether the scheduler is installed. `collect_status` carries the
    no-subprocess contract; `start` does not, and a guard copied from one to
    the other fails for a reason unrelated to what it is guarding. So this
    asserts the specific thing: the loop starter is never called, and no loop
    state is left behind.
    """
    import rite_ai.loop.session as loop_session
    from rite_ai.lifecycle import start

    root = project(tmp_path)

    def explode(*args, **kwargs):
        raise AssertionError("start started the loop")

    monkeypatch.setattr(loop_session, "start", explode)
    monkeypatch.setattr(loop_session, "hold_lock", explode)

    result = start(root)

    assert result.ok
    assert not (root / ".rite" / "loop.lock").exists()
    assert not (root / ".rite" / "loop.log").exists()
    assert "loop: not running" in "\n".join(result.actions)


def test_start_reports_a_running_loop_as_running(tmp_path, monkeypatch):
    """The other half. A `start` that always says "not running" is a line
    people learn to ignore, which is how the report stops being read at all.

    THE LIVENESS IS SUBSTITUTED, NOT BORROWED. This wrote `os.getpid()` —
    the pytest process — and asserted `start` reports a running loop. pytest
    is not a loop, so the test asserted the exact false positive that pid
    reuse produces and called it the feature. Round 2 caught it, and it is
    the second test in this file to have that shape.

    Now the pid is a fixed number and its aliveness is supplied, so the test
    says "when the recorded process IS the loop, say so" and nothing else.
    """
    import rite_ai.scheduler.lock as lock_module
    from rite_ai.lifecycle import start
    from rite_ai.loop.session import lock_path

    root = project(tmp_path)
    lock_path(root).write_text("4242 0\n")
    monkeypatch.setattr(lock_module, "process_is_running", lambda pid: pid == 4242)

    result = start(root)

    assert "loop: running (pid 4242)" in "\n".join(result.actions)


# --- the stale lock, which `rite start` made visible -------------------------------
#
# These live here rather than in the loop's own test file because this change
# is what surfaced them: `rite start` now reports the loop, so a lock that
# lies had somewhere new to show up. They are about `rite loop`, and belong
# beside it once somebody moves them.


def test_a_held_lock_with_no_tmux_session_names_the_way_out(tmp_path, monkeypatch):
    """THE WEDGE. A reboot leaves `.rite/loop.lock` behind — it is a project
    file, not `/var/run` — and the OS can hand that pid to something else. The
    loop then cannot be started again: tmux has no session, the lock looks
    held, and `rite loop start` refuses.

    Its remedy used to read "`rite loop stop`", which does not touch the lock
    and reports "no loop is running". Two commands, two true-sounding answers,
    no way to reconcile them, and the one command that fixes it printed
    nowhere. Permanent, and silent.

    rite cannot tell a recycled pid from a `rite loop run --watch` somebody
    started directly, so it names both and prints the escape — the same
    report-rather-than-guess line the claims work landed on.
    """
    import rite_ai.loop.session as loop_session
    from rite_ai.loop.session import lock_path

    root = project(tmp_path)
    lock_path(root).write_text("4242 0\n")
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)
    monkeypatch.setattr(loop_session, "rite_command", lambda: "/usr/bin/rite")
    monkeypatch.setattr(
        "rite_ai.scheduler.lock.process_is_running", lambda pid: pid == 4242
    )

    refused = loop_session.start(root)

    text = f"{refused.reason} {refused.remedy}"
    assert "4242" in text
    assert "tmux has no session" in text
    assert str(lock_path(root)) in text, "the escape has to be a command, not advice"
    assert "run --watch" in text, "the legitimate case must be named too"


def test_stop_says_the_lock_is_still_in_the_way(tmp_path, monkeypatch):
    """The other end of the same wedge. Somebody refused by `start` arrives
    here, and this branch used to report "no loop is running" while the lock
    that caused the refusal sat untouched."""
    import rite_ai.loop.session as loop_session
    from rite_ai.loop.session import lock_path

    root = project(tmp_path)
    lock_path(root).write_text("4242 0\n")
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)
    monkeypatch.setattr(
        "rite_ai.scheduler.lock.process_is_running", lambda pid: pid == 4242
    )

    result = loop_session.stop(root)

    assert "4242" in result.detail
    assert str(lock_path(root)) in result.detail


def test_stop_stays_quiet_when_no_lock_is_held(tmp_path, monkeypatch):
    """A warning printed every time is one nobody reads — and this branch is
    the ordinary "nothing was running" case."""
    import rite_ai.loop.session as loop_session

    root = project(tmp_path)
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)

    result = loop_session.stop(root)

    assert "no loop is running" in result.detail
    assert "still held" not in result.detail
    assert "loop.lock" not in result.detail


# --- one reader for "is the loop up" -----------------------------------------------


def test_the_status_line_reads_through_running_pid(tmp_path, monkeypatch):
    """Three callers wanted this answer and two had their own copy of the
    lock-read. Separate copies drift one bug at a time, and the first symptom
    is two commands disagreeing about whether the loop is up.

    SO THIS ASSERTS THE SHARED IMPLEMENTATION, NOT AGREEMENT. The first
    version computed both values and compared them — which is the thing its
    own docstring said not to do, and would pass with two divergent copies
    that happen to agree on these inputs. A test that certifies the bug it
    warns about is worse than no test, because the next reader trusts it.

    Substituting `running_pid` and demanding the status line follow is the
    only version that fails when the copy comes back.
    """
    import rite_ai.loop.session as loop_session
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    monkeypatch.setattr(loop_session, "running_pid", lambda _root: 4242)

    assert "4242" in _loop_line(root)


def test_the_status_line_and_start_report_the_same_loop(tmp_path):
    """The behaviour the shared reader exists to produce, end to end: one
    lock file, two commands, one answer."""
    import os

    from rite_ai.lifecycle import start
    from rite_ai.loop.session import lock_path, running_pid
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    assert running_pid(root) == 0
    assert _loop_line(root) == "not running"
    assert "loop: not running" in "\n".join(start(root).actions)

    lock_path(root).write_text(f"{os.getpid()} 0\n")
    assert running_pid(root) == os.getpid()
    assert str(os.getpid()) in _loop_line(root)
    assert str(os.getpid()) in "\n".join(start(root).actions)


def test_a_dead_pid_in_the_lock_is_not_a_running_loop(tmp_path, monkeypatch):
    """The reason this reads a pid rather than trusting the file's existence:
    a loop killed with SIGKILL leaves its lock behind, and a lock file that
    means "running" would make the queue watcher permanently, invisibly
    unstartable.

    The deadness is SUBSTITUTED rather than assumed. This wrote pid 999999,
    which cannot exist on macOS (pid_max 99998) and can exist on Linux, whose
    default pid_max is 4194304 — a test that passes on the machine you write
    it on and is a coin flip on CI. This repository has lost three defects to
    exactly that asymmetry.
    """
    import rite_ai.scheduler.lock as lock_module
    from rite_ai.loop.session import lock_path, running_pid

    root = project(tmp_path)
    lock_path(root).write_text("4242 0\n")
    monkeypatch.setattr(lock_module, "process_is_running", lambda _pid: False)

    assert running_pid(root) == 0


@pytest.mark.parametrize(
    "contents",
    [
        "not-a-pid\n",
        "",
        "   \n",
        "-1 0\n",
        "0 0\n",
        # THE ONE REVIEW FOUND. Parses as an int, then `os.kill` raises
        # OverflowError — which is an ArithmeticError and was caught by
        # nothing. `rite start`, `rite status` and `rite loop start` all
        # tracebacked instead of answering, on the one input where a command
        # whose job is reporting what is wrong has to keep working.
        "99999999999 0\n",
    ],
)
def test_a_corrupt_lock_reads_as_not_running(tmp_path, contents):
    """Not as an exception, and not as running."""
    from rite_ai.loop.session import lock_path, running_pid

    root = project(tmp_path)
    lock_path(root).write_text(contents)

    assert running_pid(root) == 0


def test_start_survives_a_lock_file_that_cannot_be_parsed(tmp_path):
    """The property that actually matters, asserted through the command
    rather than the helper: orientation is what a session needs most when
    something is wrong, so `rite start` must report rather than raise."""
    from rite_ai.lifecycle import start
    from rite_ai.loop.session import lock_path

    root = project(tmp_path)
    lock_path(root).write_text("99999999999 0\n")

    result = start(root)

    assert result.ok
    assert "loop: not running" in "\n".join(result.actions)
