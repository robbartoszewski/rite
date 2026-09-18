"""The standing instruction that makes a session look for the next ticket (L-1).

A session finishes what it was asked and exits, because nothing standing tells
it there is a next thing — so a person types "don't stop after BEN-191" by
hand, every time. SPEC §2.7 puts work-seeking exactly here: "Workers start when
a Manager has safe work to give one, never because a clock crossed a boundary
with nobody watching." The Manager is the loop; this is the text that says so.

The properties worth testing are the ones that make it safe rather than the
ones that make it present:

- it OFFERS and never instructs — a generated file that spends quota when an
  agent reads it is the surprise §9.12 exists to prevent;
- the Worker's copy says the opposite of the Manager's, deliberately: Workers
  do not pick their own next ticket;
- it is a generated section like any other, so an edited one is kept.
"""

from __future__ import annotations

import re

import pytest

from rite_ai.cli.init.claude_gen import generate_claude_md
from rite_ai.config.models import Module, ProjectBrief, ProjectConfig
from rite_ai.generated_sections import MARKER_RE
from rite_ai.workspace.manage import render_worker_claude_md

BRIEF = ProjectBrief(name="acme", role="owner")

HEADING = "## Working the queue"


def _project(role: str = "owner", tmp_path=None) -> str:
    return generate_claude_md(
        role,
        BRIEF,
        [Module(name="engine", path="engine")],
        ProjectConfig(),
        tmp_path,
    )


# --- the Manager side -------------------------------------------------------------


def test_the_project_file_tells_a_session_to_look_for_the_next_ticket(tmp_path):
    text = _project(tmp_path=tmp_path)
    assert HEADING in text
    assert "One ticket is not the job" in text


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_both_roles_get_it(role, tmp_path):
    """An Owner stops after one ticket exactly like a Manager does — that is
    the session this was written for."""
    assert HEADING in _project(role, tmp_path)


def test_it_names_the_commands_that_answer_what_is_next(tmp_path):
    """ "Look for more work" without the command is an instruction to guess."""
    text = _project(tmp_path=tmp_path)
    assert "rite status" in text
    assert "rite board list --label scheduled" in text


def test_it_says_which_of_the_three_reasons_it_stopped(tmp_path):
    """ "Stopped" alone is indistinguishable from "crashed", which is the
    signal a human actually reads."""
    text = _project(tmp_path=tmp_path)
    assert "indistinguishable from" in text


def test_it_never_instructs_a_session_to_start_others(tmp_path):
    """The whole hazard. A generated file an agent reads on startup must not
    contain a line that spends quota — §9.12, and `pool fill`'s own ceiling."""
    section = _section(_project(tmp_path=tmp_path), HEADING)
    for forbidden in ("rite pool fill", "rite sandbox start", "yoloai new"):
        assert forbidden not in section, (
            f"{forbidden!r} in a section an agent reads unprompted — a "
            "generated file must offer, never spend"
        )


def test_it_says_starting_a_session_is_a_decision(tmp_path):
    section = _section(_project(tmp_path=tmp_path), HEADING)
    assert "is a decision, not a step" in section
    assert "never to fill capacity" in section


# --- the Worker side, which says the opposite on purpose --------------------------


def _manifest():
    from rite_ai.config.models import WorkerManifest

    return WorkerManifest(name="alpha", manager="lead", modules=["engine"])


def test_a_worker_is_told_not_to_pick_its_own_next_ticket():
    """The Manager holds the board and the capacity. Two Workers choosing
    their own next ticket is how one path gets claimed twice."""
    text = render_worker_claude_md(_manifest())
    assert "Do not start another ticket on your own" in text


def test_a_worker_is_told_to_say_it_is_stopping(text=None):
    """A Worker that finishes and goes quiet is indistinguishable from one
    that died mid-ticket, and only one of those needs somebody woken up."""
    text = render_worker_claude_md(_manifest())
    assert "rather than going quiet" in text


def test_the_worker_and_the_project_do_not_give_the_same_instruction():
    """If a Worker read the project's section it would start taking tickets.
    The two files are read by different things and must not converge."""
    worker = render_worker_claude_md(_manifest())
    assert "One ticket is not the job" not in worker


# --- it is an ordinary generated section ------------------------------------------


def test_it_carries_a_checksum_marker_like_every_other_section(tmp_path):
    """So `rite update` refreshes it while nobody has edited it, and keeps it
    once somebody has."""
    text = _project(tmp_path=tmp_path)
    before = text.split(HEADING)[0].rstrip().splitlines()[-1]
    assert MARKER_RE.match(before.strip()), before


def test_an_edited_section_is_kept_rather_than_overwritten(tmp_path):
    """The rule for every generated section. Worth asserting here because
    this is the one a project is most likely to want to reword."""
    from rite_ai.update.refresh import refresh_text

    generated = _project(tmp_path=tmp_path)
    edited = generated.replace(
        "One ticket is not the job", "One ticket is absolutely the job"
    )
    _, changes = refresh_text(edited, generated)

    kept = [c for c in changes if HEADING.removeprefix("## ").strip() in c.target]
    assert kept, [c.target for c in changes]
    assert kept[0].action.startswith("kept"), kept[0].action


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest
