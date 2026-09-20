"""The out-of-scope rule, and the gates that keep it true.

The rule: work found while doing a ticket, that is not that ticket,
becomes a ticket at the moment it is found — and if the original cannot be
finished without it, the original is recorded as blocked by it.

It is a convention plus an existing command, so what needs testing is not
behaviour but that the rule is where it will be read, says the same thing in
every copy, and describes the command as the command actually behaves.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CHECKLISTS = (
    REPO / "templates" / "review-checklist.md",
    REPO / ".rite" / "review-checklist.md",
)
TICKET_COMMAND = REPO / "templates" / "commands" / "ticket.md"


def _flat(path: Path) -> str:
    return " ".join(path.read_text().split())


class TestTheRuleIsWhereItWillBeRead:
    """Two audiences at two moments: the Worker at discovery, and the
    reviewer asking whether anything was found and left untracked. A rule
    in only one of those places covers only one of them."""

    def test_the_worker_gets_it_at_the_moment_of_discovery(self):
        text = _flat(TICKET_COMMAND)
        assert "Would this ticket's definition of done still be met" in text, (
            "the rule is not in /ticket, so a Worker never meets it while working"
        )
        # Placed in the step where the work happens, not in the closing
        # report — a rule that arrives at report time arrives after the
        # thing it was meant to prevent.
        body = TICKET_COMMAND.read_text()
        discovery = body.index("Do the work")
        report = body.index("**Report.**")
        rule = body.index("definition of done still be met")
        assert discovery < rule < report, (
            "the rule is not placed at the moment of discovery"
        )

    def test_the_reviewer_is_asked_whether_anything_was_left_untracked(self):
        for path in CHECKLISTS:
            text = _flat(path)
            assert "found and did not do is a ticket" in text, path.name
            assert "would this ticket's definition of done still be met" in (
                text.lower()
            ), path.name

    def test_both_checklist_copies_say_the_same_thing(self):
        """The template ships to every project `rite init` creates; the
        other is what `rite review` prints here. Fixing one is a fix the
        other audience never gets."""
        template, own = (_flat(p) for p in CHECKLISTS)
        for phrase in (
            "found and did not do is a ticket",
            "rite board link <this> <new>",
            "never something imagined",
        ):
            assert (phrase in template) == (phrase in own), phrase


class TestTheRuleDescribesTheCommandThatExists:
    """The rule tells someone to run `rite board link <this> <new>` and
    relies on that recording "this is blocked by new". If the default
    direction ever changes, every copy of the rule silently starts
    recording the dependency backwards — which is the failure `board
    link` already had once, against a live board."""

    def test_the_default_direction_is_the_one_the_rule_relies_on(self):
        import click

        from rite_ai.cli.main import cli

        link = cli.commands["board"].commands["link"]
        help_text = " ".join((link.help or "").split())
        assert "TICKET_ID is blocked by TARGET_ID" in help_text, (
            "the rule documents `rite board link <this> <new>` as recording "
            "'this is blocked by new'; the command no longer says that"
        )
        assert isinstance(link, click.Command)

    def test_the_rule_does_not_document_a_flag_it_does_not_need(self):
        """`--type Blocks` is valid but unnecessary for this direction, and
        naming an unnecessary flag invites getting the direction wrong."""
        text = _flat(TICKET_COMMAND)
        assert "rite board link <this-ticket> <new-ticket>" in text
        assert "no `--type` needed" in text


class TestTheDefectClassesCountIsCountable:
    """`DEFECT_CLASSES.md` claims a number of classes with members on both
    sides, and that claim is the evidence for the file's central argument
    — that these are classes rather than local accidents. It was wrong
    when written: it said four, then five, and the countable answer was
    three, because class 1's outside-rite members were unattributed and so
    the claim could not be checked at all.

    Prose drifting in the direction that flatters the tool, in the document
    about prose drifting in the direction that flatters the tool."""

    def _document(self) -> str:
        return (REPO / "DEFECT_CLASSES.md").read_text()

    def _classes(self) -> list[tuple[str, str]]:
        parts = re.split(r"(?m)^## (\d+\. .+)$", self._document())
        return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

    def test_the_stated_cross_project_count_is_the_real_one(self):
        both = [
            title.split(".")[0]
            for title, body in self._classes()
            if re.search(r"second codebase", body)
            and re.search(r"\brite\b|`rite", body)
        ]
        stated = re.search(r"(\w+) of the ten have members both", self._document())
        assert stated, "the document no longer states the cross-project count"
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        assert words.get(stated.group(1)) == len(both), (
            f"document says {stated.group(1)}, countable answer is "
            f"{len(both)} (classes {both})"
        )

    def test_the_class_count_matches_the_headings(self):
        # Hardcoded so that adding a class is a deliberate act with a diff
        # somebody reads, not a number that drifts. 10 -> 13 on 2026-09-20:
        # a guard that fails open while something else catches it; a fix that
        # corrects a defect's syntax and leaves its shape; and written,
        # tested, called by nothing.
        assert len(self._classes()) == 16, (
            f"the document says sixteen classes and has {len(self._classes())}"
        )
        assert "sixteen classes" in self._document()

    def test_the_new_class_carries_its_own_third_column(self):
        """Every class owes the question this file exists for: what still
        gets through. A class added without one is a class that looks
        guarded."""
        for title, body in self._classes():
            assert "**What still gets through.**" in body, title
