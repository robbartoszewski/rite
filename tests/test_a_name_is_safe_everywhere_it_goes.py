"""A user-supplied name reaches more than a path, and must survive all of it.

WHY THIS EXISTS. `name_problem` was written to stop a name becoming a path
it should not — `rite remove worker ..` once deleted a whole project. It
validates a safe PATH SEGMENT, and it does that correctly.

⚠ A MANAGER NAME IS ALSO A TMUX TARGET, and nothing enforced that.
Measured, with a Manager named `eu:west`:

    name_problem('eu:west') -> ''            accepted
    start().ok              -> False         "started and exited immediately"
    tmux actually has       -> rite-mgr-...-eu:west   (running)
    liveness()              -> alive=False, known=True
    stop()                  -> ok=True, killed=False, "no session named ..."

`:` is tmux's window separator, so the session is created under a name no
later `-t` lookup resolves. In production that pane runs `claude`: rite
tells the user it failed to start while a real, paid, detached session is
running with no instance record — and `rite manager stop`, the recovery
command built for exactly that orphan, reports success having done
nothing. `known=True` means §9.14.0's fail-closed rule never fires, because
the answer was confident and wrong.

⚠ THE RULE IS POSITIVE, NOT A BLACKLIST, and that is deliberate. The first
fix for a related defect today enumerated the invisible Unicode categories
to reject and missed U+2800 on its first run. A blacklist is a review built
from a list of known examples: it inherits the imagination of whoever wrote
the list and cannot exceed it. `:` would have been on nobody's list either.

So a name must be composed of characters that are safe in every consumer we
have — a path segment, a tmux target, a shell argument: letters, digits,
`.`, `-`, `_`. Everything else is refused, including the ones no consumer
has complained about yet.
"""

from __future__ import annotations

import pytest

from rite_ai.names import name_problem

# Names rite itself uses, and the shapes real projects use. If any of these
# start failing, the rule has been tightened past what the product needs.
STILL_VALID = [
    "lead",
    "planner",
    "w1",
    "eu-west",
    "api_gateway",
    "database.md",  # context FILE names go through this too
    "backend",
    "Manager2",
]

# Each of these breaks at least one consumer. `:` is the measured one; the
# rest are refused because the rule is an allowlist, not because each has
# been individually demonstrated — which is the point of an allowlist.
MUST_BE_REFUSED = [
    ("eu:west", "tmux reads `:` as the window separator"),
    ("lead session", "a space splits a shell argument"),
    ("lead*", "a glob in a tmux target and in a shell"),
    ("lead?", "the same"),
    ("lead[1]", "the same"),
    ("lead$HOME", "shell expansion"),
    ("lead;rm", "a shell command separator"),
    ("lead|tee", "a pipe"),
    ("lead\\x", "a backslash escape"),
    ("lead'q", "an unbalanced quote"),
    ('lead"q', "the same"),
    ("lead`x`", "command substitution"),
    ("lead​x", "a zero-width space, invisible in any report"),
]


@pytest.mark.parametrize("name", STILL_VALID)
def test_the_names_rite_actually_uses_are_still_accepted(name):
    assert name_problem(name, kind="manager name") == "", (
        f"{name!r} is a name this product uses and the rule now refuses it"
    )


def test_a_dot_is_fine_in_a_file_name_and_not_in_a_tmux_target():
    """⚠ ONE RULE FOR EVERY CONSUMER WAS THE WRONG SHAPE, and this is the
    correction to my own fix.

    The allowlist admitted `.` because context FILE names come through the
    same function (`database.md`). But `.` is tmux's PANE separator, so a
    Manager named `v2.0` creates a session nothing can address. Measured:

        tmux new-session -d -s rvw-dot.name   -> created
        tmux has-session -t =rvw-dot.name     -> can't find pane: name
        tmux kill-session -t =rvw-dot.name    -> can't find pane: name

    The session exists and is reachable only by `#{session_id}` — the same
    unstoppable-orphan shape as the `:` defect this file was written for,
    through a character the fix for that one deliberately allowed.

    So the allowed set depends on what the name BECOMES. A context file is
    never a tmux target; a Manager always is.
    """
    assert name_problem("database.md", kind="context file name") == ""
    assert name_problem("v2.0", kind="context file name") == ""
    problem = name_problem("v2.0", kind="manager name", must_be_a_tmux_target=True)
    assert problem, "a Manager name containing '.' was accepted"
    assert "tmux" in problem, f"the refusal does not say why: {problem!r}"


@pytest.mark.parametrize("name,why", MUST_BE_REFUSED)
def test_a_name_that_breaks_a_consumer_is_refused(name, why):
    problem = name_problem(name, kind="manager name")
    assert problem, f"{name!r} was accepted — {why}"


def test_the_refusal_says_what_a_name_may_contain():
    """A refusal that only says "invalid" invites the next guess.

    The measured failure this file is about produced a session nobody could
    reach; the user's next move must not be to try another punctuation mark.
    """
    problem = name_problem("eu:west", kind="manager name")
    assert "letters" in problem and "digits" in problem, (
        f"the refusal does not say what is allowed: {problem!r}"
    )
    assert "eu:west" in problem, "the refusal does not quote the name it refused"


def test_a_manager_name_survives_a_tmux_round_trip():
    """The property, rather than the rule: every accepted name must be a
    target tmux resolves back to the session it created."""
    import shutil
    import subprocess
    import uuid

    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")

    for name in ("lead", "eu-west", "api_gateway"):
        session = f"rite-nm-{uuid.uuid4().hex[:6]}-{name}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "sleep 60"],
            capture_output=True,
        )
        try:
            got = subprocess.run(
                ["tmux", "display-message", "-p", "-t", session, "#{session_name}"],
                capture_output=True,
                text=True,
            )
            assert got.stdout.strip() == session, (
                f"an accepted name produced a session tmux does not resolve "
                f"back: asked for {session!r}, got {got.stdout.strip()!r}"
            )
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", f"={session}"], capture_output=True
            )
