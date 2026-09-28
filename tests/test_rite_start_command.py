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
    monkeypatch.setattr("rite_ai.loop.lock.acquire", explode)

    result = start(root)

    assert result.ok
    assert not (root / ".rite" / "loop-run.lock").exists()
    assert not (root / ".rite" / "loop-run.gate").exists()
    assert not (root / ".rite" / "loop.log").exists()
    assert "loop: not running" in "\n".join(result.actions)


def _hold_the_loop_in_another_process(root: Path):
    """A real second process holding this project's loop lock until killed,
    which is what a `rite loop run --watch` is to every reader. It prints once
    it holds the lock, so the caller never races its startup."""
    import subprocess
    import sys

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from rite_ai.loop import lock\n"
            "o = lock.acquire(Path(sys.argv[1]))\n"
            "print(type(o).__name__, flush=True)\n"
            "time.sleep(600)\n",
            str(root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "LockAcquired"
    return proc


def test_start_reports_a_running_loop_as_running(tmp_path):
    """The other half. A `start` that always says "not running" is a line
    people learn to ignore, which is how the report stops being read at all.

    The holder is a REAL second process holding the loop lock. The pid-file
    version had to substitute liveness, because the file could name anything:
    once it named pytest itself, and the test asserted the exact false
    positive pid reuse produces. The kernel lock names only its holder.
    """
    from rite_ai.lifecycle import start

    root = project(tmp_path)
    loop = _hold_the_loop_in_another_process(root)
    try:
        result = start(root)
    finally:
        loop.kill()
        loop.wait()

    assert f"loop: running (pid {loop.pid})" in "\n".join(result.actions)


# --- no stale lock: what the kernel lock removed ---------------------------------


def test_a_killed_loop_leaves_nothing_to_clear(tmp_path):
    """THE WEDGE, GONE. A reboot or a SIGKILL used to leave `.rite/loop.lock`
    naming a pid, which the OS could hand to something else, and then no loop
    could start until somebody ran `rm`. The kernel drops the lock when its
    holder dies, however it dies, so there is nothing left to clear."""
    from rite_ai.lifecycle import start
    from rite_ai.loop import lock as loop_lock
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    loop = _hold_the_loop_in_another_process(root)
    loop.kill()
    loop.wait()

    assert loop_lock.holder(root) == loop_lock.Holder(known=True, running=False)
    assert _loop_line(root) == "not running"
    assert "loop: not running" in "\n".join(start(root).actions)


@pytest.mark.parametrize("kind", ["zombie", "unrelated live process"])
def test_a_pid_that_is_not_the_loop_is_not_a_running_loop(tmp_path, kind):
    """A pid that exists is not a pid that is yours. Measured on the pid file
    at `4f7e1d2`: a zombie (`ps` stat Z) or a live `sleep` whose pid was
    recorded read as a running loop and refused every start. Here the same
    pids sit in the new lock file's record, and nothing holds the lock."""
    import os
    import subprocess
    import time

    from rite_ai.loop import lock as loop_lock

    root = project(tmp_path)
    if kind == "zombie":
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        time.sleep(0.2)
        other = None
    else:
        other = subprocess.Popen(["sleep", "30"])
        pid = other.pid
    try:
        loop_lock.lock_path(root).write_text(f'{{"pid": {pid}, "acquired_at": 0}}\n')

        assert loop_lock.holder(root) == loop_lock.Holder(known=True, running=False)
        held = loop_lock.acquire(root)
        assert isinstance(held, loop_lock.LockAcquired)
        loop_lock.release(held)
    finally:
        if other is None:
            os.waitpid(pid, 0)
        else:
            other.kill()
            other.wait()


def test_a_loop_run_outside_tmux_is_named_exactly(tmp_path, monkeypatch):
    """tmux has no session, and a loop holds the lock: a `rite loop run
    --watch` started directly. The pid is exact, so the refusal names it, and
    there is no stale-lock escape to print, because there is no stale lock."""
    import rite_ai.loop.session as loop_session

    root = project(tmp_path)
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)
    monkeypatch.setattr(loop_session, "rite_command", lambda: "/usr/bin/rite")
    loop = _hold_the_loop_in_another_process(root)
    try:
        refused = loop_session.start(root)
        stopped = loop_session.stop(root)
    finally:
        loop.kill()
        loop.wait()

    text = f"{refused.reason} {refused.remedy}"
    assert str(loop.pid) in text
    assert "outside tmux" in text
    assert "rm " not in text
    assert str(loop.pid) in stopped.detail


def test_stop_stays_quiet_when_no_lock_is_held(tmp_path, monkeypatch):
    """A warning printed every time is one nobody reads — and this branch is
    the ordinary "nothing was running" case."""
    import rite_ai.loop.session as loop_session

    root = project(tmp_path)
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)

    result = loop_session.stop(root)

    assert "no loop is running" in result.detail
    assert "outside tmux" not in result.detail
    assert "cannot tell" not in result.detail


# --- one reader for "is the loop up" -----------------------------------------------


def test_the_status_line_and_start_report_the_same_loop(tmp_path):
    """One lock, two commands, one answer, with a real holder."""
    from rite_ai.lifecycle import start
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    assert _loop_line(root) == "not running"
    assert "loop: not running" in "\n".join(start(root).actions)

    loop = _hold_the_loop_in_another_process(root)
    try:
        assert str(loop.pid) in _loop_line(root)
        assert str(loop.pid) in "\n".join(start(root).actions)
    finally:
        loop.kill()
        loop.wait()


def test_a_lock_that_cannot_be_asked_is_not_reported_as_not_running(
    tmp_path, monkeypatch
):
    """ "Could not tell" and "not running" must never render the same: a start
    told "not running" by a reader that could not tell would make two. Here
    `flock` is a no-op, as on some network filesystems."""
    import rite_ai.loop.session as loop_session
    from rite_ai import kernel_lock
    from rite_ai.lifecycle import start
    from rite_ai.loop import lock as loop_lock
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    loop_lock.lock_path(root).write_text("")
    monkeypatch.setattr(kernel_lock.fcntl, "flock", lambda fd, op: None)
    monkeypatch.setattr(loop_session, "is_alive", lambda _name: False)
    monkeypatch.setattr(loop_session, "rite_command", lambda: "/usr/bin/rite")

    assert "cannot tell" in _loop_line(root)
    assert "cannot tell" in "\n".join(start(root).actions)
    refused = loop_session.start(root)
    assert "cannot tell" in refused.reason


@pytest.mark.parametrize(
    "contents", ["not-a-pid\n", "", "   \n", "-1 0\n", "0 0\n", "99999999999 0\n"]
)
def test_a_corrupt_record_with_no_holder_reads_as_not_running(tmp_path, contents):
    """Not as an exception, and not as running: the record is only read when
    the kernel says the lock is held, and here nothing holds it. The pid file
    once raised OverflowError on the last input, from `rite start`, `rite
    status` and `rite loop start` alike."""
    from rite_ai.lifecycle import start
    from rite_ai.loop import lock as loop_lock
    from rite_ai.reporting.status import _loop_line

    root = project(tmp_path)
    loop_lock.lock_path(root).write_text(contents)

    assert loop_lock.holder(root) == loop_lock.Holder(known=True, running=False)
    assert _loop_line(root) == "not running"
    result = start(root)
    assert result.ok
    assert "loop: not running" in "\n".join(result.actions)
