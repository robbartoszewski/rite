"""Does text given to `rite reply` read as something the person must act on?

RP1 (Robert, 2026-09-28) files a message by the command that wrote it:
`rite ask` is action, `rite reply` is reading. That is structural, and it has
one cost: a real question sent with `rite reply` would land in the reading
pile, where nobody is asked to answer it. So `rite reply` refuses anything
this finds and says to use `rite ask`.

⚠ **IT ERRS TOWARD FINDING TOO MUCH, BY DESIGN.** The coordinator's words:
prefer redirecting too much to `rite ask` over letting one question through
into the reading pile. A statement caught here costs the Manager one more
command, and the person one extra line in the action pile. A question missed
here costs an answer nobody gives. So any question mark counts, and so do the
words people use to ask, to wait and to be blocked. There is deliberately no
way to override it from `rite reply`: an override is exactly what a Manager
with a question it has mislabelled would reach for.

What it does NOT do is judge meaning. It is a list of surface signs, and it
will miss a question that uses none of them ("tell me the schema" is caught
by "tell me"; "the schema is unclear to me" is not). That gap is why the
class is decided by the command and not by this: this only catches the
common way of getting the command wrong.
"""

from __future__ import annotations

import re

_URL = re.compile(r"\b(?:https?|ftp)://\S+", re.IGNORECASE)
"""Removed first: a query string's `?` is not a question."""

_SIGNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("a question mark", r"\?"),
        ('"should I/we"', r"\b(?:should|shall) (?:i|we)\b"),
        ('"can/could/would/will you"', r"\b(?:can|could|would|will) you\b"),
        ('"do you want/prefer/agree/mind"', r"\bdo you (?:want|prefer|agree|mind)\b"),
        ('"want me to"', r"\bwant me to\b"),
        ('"what/how do you"', r"\b(?:what|how) do you\b"),
        ('"please"', r"\bplease\b"),
        ('"let me know"', r"\blet me know\b"),
        ('"tell me"', r"\btell me\b"),
        ('"which one/option/of"', r"\bwhich (?:one|option|of)\b"),
        ('"any objection/preference"', r"\bany (?:objection|preference)s?\b"),
        ('"decide/decision"', r"\b(?:decide|decision)s?\b"),
        ('"approve/approval"', r"\bapprov(?:e|al)\b"),
        ('"confirm"', r"\bconfirm\b"),
        ('"permission"', r"\bpermission\b"),
        ('"blocked/blocker/blocking"', r"\bblock(?:ed|er|ers|ing)\b"),
        ('"stuck"', r"\bstuck\b"),
        (
            '"cannot proceed/continue"',
            r"\b(?:cannot|can't|can not) (?:proceed|continue)\b",
        ),
        ('"awaiting"', r"\bawaiting\b"),
        (
            '"waiting on/for you"',
            r"\bwaiting (?:on|for) (?:you|your|a decision|an answer|approval)\b",
        ),
        (
            '"need you/your/an answer/…"',
            r"\bneeds? (?:you|your|an answer|a decision|input|approval"
            r"|guidance|help)\b",
        ),
        ('"your call/input/…"', r"\byour (?:call|input|decision|go-ahead|answer)\b"),
        ('"ok to"', r"\b(?:ok|okay) to\b"),
        ('"is it ok/fine"', r"\bis (?:it|that|this) (?:ok|okay|fine|alright)\b"),
        ('"whether"', r"\bwhether\b"),
        ('"not sure/unsure"', r"\b(?:not sure|unsure)\b"),
    )
)


def sign_of_action(text: str) -> str:
    """The first sign that `text` asks the person for something, described
    for a refusal; "" when none of the signs is there."""
    plain = _URL.sub(" ", text)
    for label, pattern in _SIGNS:
        if pattern.search(plain):
            return label
    return ""
