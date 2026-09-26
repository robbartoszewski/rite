"""`pool` and `loop` must not read a longer-named session's liveness.

WHY THIS EXISTS. `managers/session.py` was found reporting a session nobody
started as alive; part of that defect was tmux's target resolution, which
tries exact match, then fnmatch, then **PREFIX**. Measured via subprocess:

    only 'leader' exists:
      tmux has-session -t 'lead'   -> rc=0     <- answers about 'leader'
      tmux has-session -t '=lead'  -> rc=1     <- exact, correct

`=` is tmux's exact-match prefix and it is the only thing that makes
`has-session` exact.

That fix landed in `managers/`. There are THREE implementations of "is this
tmux session alive" in this codebase — `managers/session.py`,
`pool/is_tmux_session_alive` and `loop/session.is_alive` — and fixing one
of three is the shape of defect that comes back. The other two ask
`has-session -t <name>` with no `=`.

REACHABILITY, measured rather than assumed, because it differs per surface:

  - `pool.slot_name` is `rite-pool-{slug}-{index}`, so slot **1** is a
    prefix of slot **10**. `max_slots` comes from
    `sandbox.max_concurrent_workers` with no hard ceiling, so a pool of 11
    reaches it. THIS IS THE REACHABLE ONE.

  - `loop.session_name` is `rite-loop-{slug}` where the slug already
    carries a path hash (`rite-82c890`), so two projects do not collide in
    practice: measured, `rite-loop-rite-82c890` is NOT a prefix of
    `rite-loop-rite-dogfood-5c8ebd`. The hash is doing safety work it was
    added for readability and uniqueness, not for this — the same
    "protection resting on naming" that `Liveness`'s own docstring warns
    about. Fixed here anyway, because the property should not depend on a
    hash staying in a name.

What a false ALIVE costs: the pool stops refilling a slot that is dead and
`rite pool status` reports liveness nothing observed — which is verbatim
the failure `_settled_alive` was written to prevent, one layer up.

⚠ `claude` is never a command here. Panes run `sleep`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

from rite_ai.loop.session import is_alive as loop_is_alive
from rite_ai.pool import is_tmux_session_alive as pool_is_alive

_HAS_TMUX = shutil.which("tmux") is not None
_IN_CI = os.environ.get("CI") == "true"

if _IN_CI and not _HAS_TMUX:
    raise RuntimeError(
        "tmux is missing in CI. These tests prove a liveness property against "
        "a real tmux server; skipping them in CI would retire it silently."
    )

pytestmark = pytest.mark.skipif(not _HAS_TMUX, reason="tmux is not installed")


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args], capture_output=True, text=True, errors="replace", timeout=30
    )


@pytest.fixture
def only_the_longer_name():
    """Hold ONE session whose name extends the name under test.

    Yields (absent_name, present_name). Nothing named `absent_name` ever
    exists, so every assertion below is about a session that is not there.
    """
    stem = f"rite-test-pfx-{uuid.uuid4().hex[:8]}"
    longer = f"{stem}0"  # the slot-1 / slot-10 shape, not an invented one
    _tmux("new-session", "-d", "-s", longer, "sleep 600")
    try:
        assert _tmux("has-session", "-t", f"={longer}").returncode == 0, (
            "could not create the longer-named session, so these tests would "
            "pass without exercising anything"
        )
        assert _tmux("has-session", "-t", f"={stem}").returncode != 0, (
            "a session with the short name exists; this fixture would then be "
            "testing a real session and prove nothing"
        )
        yield stem, longer
    finally:
        _tmux("kill-session", "-t", f"={longer}")


def test_pool_does_not_read_slot_10_when_asked_about_slot_1(only_the_longer_name):
    absent, present = only_the_longer_name
    assert not pool_is_alive(absent), (
        f"the pool reported {absent!r} alive because {present!r} exists — "
        "tmux prefix-matched the target. A dead slot then reads healthy, the "
        "pool never refills it, and `rite pool status` reports liveness that "
        "nothing ever observed"
    )


def test_loop_does_not_read_a_longer_session_when_asked_about_a_shorter(
    only_the_longer_name,
):
    absent, present = only_the_longer_name
    assert not loop_is_alive(absent), (
        f"the loop reported {absent!r} alive because {present!r} exists — a "
        "false alive here makes `rite loop start` refuse, saying a loop is "
        "already running, for a session that does not exist"
    )


def test_both_still_see_a_session_that_really_is_there(only_the_longer_name):
    """The other direction, so 'always report dead' cannot pass."""
    _absent, present = only_the_longer_name
    assert pool_is_alive(present), "the pool failed to see a session that exists"
    assert loop_is_alive(present), "the loop failed to see a session that exists"


def test_neither_invents_a_session_when_nothing_matches(only_the_longer_name):
    """A server is up — the state in which an unresolvable target stops
    being an error and starts being an empty answer."""
    nobody = f"rite-test-absent-{uuid.uuid4().hex[:8]}"
    assert not pool_is_alive(nobody)
    assert not loop_is_alive(nobody)


@pytest.mark.parametrize("separator", [":", "."])
def test_session_exists_is_exact_for_a_name_tmux_would_parse_as_a_target(
    separator,
):
    """C12. `=` makes `has-session` exact about PREFIXES, not about grammar.

    ⚠ `has-session -t =eu:west` reads session `eu`, window `west`. tmux will
    create a session literally named `eu:west`, and `session_exists` said it
    was not there — measured on 3.7c before the fix. rite never creates such a
    name (`name_problem` refuses `:`), which is why it cost nothing; the
    function reads as a general predicate, so the next caller would not know.

    The fragments are asserted absent too, so a predicate that answered True
    for everything would not pass.
    """
    from rite_ai.managers.session import session_exists

    head, tail = f"pfx{uuid.uuid4().hex[:6]}", "west"
    name = f"{head}{separator}{tail}"
    made = _tmux("new-session", "-d", "-s", name, "sleep 60")
    assert made.returncode == 0, made.stderr
    try:
        listed = _tmux("list-sessions", "-F", "#{session_name}").stdout.split()
        if name not in listed:
            # ⚠ Measured on tmux 3.4 (Ubuntu 24.04, CI): `new-session -s
            # 'pfx:west'` creates `pfx_west`. That tmux cannot hold a session
            # whose NAME contains the separator, so the misreading this
            # guards against cannot happen on it. Skipped with that reason
            # rather than failed; tmux 3.7c keeps the name, and runs it.
            pytest.skip(
                f"this tmux ({_tmux('-V').stdout.strip()}) renames {separator!r} "
                f"in session names, so no session can carry one"
            )
        assert session_exists(name), f"{name!r} exists and was reported absent"
        assert not session_exists(head)
        assert not session_exists(tail)
    finally:
        # Killed by its `$id`, found by listing: the name as a target would
        # miss it for the same reason `has-session` did.
        ids = _tmux("list-sessions", "-F", "#{session_id} #{session_name}").stdout
        for line in ids.splitlines():
            sid, _, sname = line.partition(" ")
            # By the unique head, so a session tmux RENAMED is killed too —
            # matching the literal name leaked it on tmux 3.4.
            if sname.startswith(head):
                _tmux("kill-session", "-t", sid)
