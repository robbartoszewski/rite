"""TR2: the refinement limits are Robert's by default and configurable, and a
mistake in them is refused rather than quietly corrected (the note's part 3.11).

Robert: "the limits sound good. We can make them configurable for advanced
users", and the accept words "also configurable. the current list looks good.
I think I will add "proceed"". Whether refinement is enforced is not
configurable at all (TRQ1), so there is no key for it to be refused as.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.config.models import RefinementConfig
from rite_ai.config.parse import ParseError, parse_config


def _parse(tmp_path: Path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return parse_config(path)


def test_the_defaults_are_roberts(tmp_path):
    config = _parse(tmp_path, "ticket_backend:\n  type: none\n")
    assert config.refinement == RefinementConfig(
        unanswered=3,
        deadline_hours=24,
        open_max=5,
        start_per_session=3,
        accept_words=["ok", "yes", "accept", "lgtm", "proceed"],
        chore_after_minutes=60,
        questions_to="dm",
        channel="",
    )


def test_every_key_can_be_set(tmp_path):
    config = _parse(
        tmp_path,
        "refinement:\n"
        "  unanswered: 4\n"
        "  deadline_hours: 36\n"
        "  open_max: 8\n"
        "  start_per_session: 2\n"
        "  accept_words: [OK, ship]\n"
        "  chore_after_minutes: 15\n"
        "  questions_to: channel\n"
        "  channel: G012AB3CD\n",
    )
    assert config.refinement == RefinementConfig(
        unanswered=4,
        deadline_hours=36,
        open_max=8,
        start_per_session=2,
        accept_words=["ok", "ship"],
        chore_after_minutes=15,
        questions_to="channel",
        channel="G012AB3CD",
    )


@pytest.mark.parametrize(
    "body, says",
    [
        ("unanswered: 0", "refinement.unanswered"),
        ("open_max: -1", "refinement.open_max"),
        ("start_per_session: 0", "refinement.start_per_session"),
        ("chore_after_minutes: 0", "refinement.chore_after_minutes"),
        ("unanswered: true", "refinement.unanswered"),
        ("unanswered: '3'", "refinement.unanswered"),
        ("unanswered: 2.5", "refinement.unanswered"),
        ("open_max: 2\n  start_per_session: 3", "more than refinement.open_max"),
        ("deadline_hours: 0", "refinement.deadline_hours"),
        ("deadline_hours: -2", "refinement.deadline_hours"),
        ("deadline_hours: soon", "refinement.deadline_hours"),
        ("accept_words: []", "at least one word"),
        ("accept_words: ok", "at least one word"),
        ("accept_words: [ok, '']", "empty entry"),
        ("accept_words: [ok, 'go ahead']", "has a space"),
        ("questions_to: email", "refinement.questions_to"),
        ("questions_to: channel", "refinement.channel is empty"),
        ("questions_to: channel\n  channel: general", "not a Slack channel"),
        ("colour: blue", "colour"),
    ],
)
def test_a_mistake_is_refused_and_named(tmp_path, body, says):
    got = _parse(tmp_path, f"refinement:\n  {body}\n")
    assert isinstance(got, ParseError), got
    assert says in got.message, got.message


@pytest.mark.parametrize("word", ["no", "NOT", "stop", "Wait", "cancel", "don't"])
def test_a_word_people_type_to_refuse_can_never_accept(tmp_path, word):
    """A mistake in this list turns a refusal into consent."""
    got = _parse(tmp_path, f'refinement:\n  accept_words: [ok, "{word}"]\n')
    assert isinstance(got, ParseError), got
    assert "turns a reply into consent" in got.message


def test_a_deadline_longer_than_slacks_thread_horizon_is_allowed(tmp_path):
    """Open round roots are pinned and read past the 24-hour horizon (the
    note's part 4, race 7), so a longer deadline is a choice, not a mistake."""
    config = _parse(tmp_path, "refinement:\n  deadline_hours: 72\n")
    assert config.refinement.deadline_hours == 72


def test_enforcement_is_not_a_setting(tmp_path):
    """TRQ1: standard, with no opt-out. A key that looks like one is refused
    as unknown, never read as nothing. So is `rounds`: rounds are not capped
    (Robert's correction to TRQ2), and a cap someone believes they set must
    not be silently ignored."""
    for key in ("enforce", "enabled", "required", "rounds"):
        got = _parse(tmp_path, f"refinement:\n  {key}: false\n")
        assert isinstance(got, ParseError) and key in got.message, got
