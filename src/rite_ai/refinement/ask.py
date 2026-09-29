"""What a refinement round may say, checked before anything is sent (TR2).

`rite refine ask <ID> --file <message.md>` is how the Owner asks the User
about a ticket. rite reads the message, **refuses it, saying why, unless it
passes every rule below**, and then writes the message the User sees itself
(the note's part 3.4 step 2). The rules are what keep "nothing invented"
true while a model is doing the asking:

* **At most three questions, numbered**, under `Questions:`. A question mark
  anywhere else is refused: an unnumbered question is one the User can
  miss, and one rite cannot count.
* **From round 2 on, a proposal**: a definition of done the User can accept
  in one word, under `Proposal:`. A lazy User is far more likely to accept a
  proposal than to answer a question, and "you decide" is answered by one
  (TRQ4). Round 1 may propose already, when the Owner has no doubts
  (TRQ11: "if it has any doubts").
* **Every proposed item says where it came from**, in brackets at its end:
  `[ticket: "<quote>"]`, `[answer: "<quote>"]`, or `[proposed]`. **Every
  quote must be an exact substring of the ticket's text, or of an answer
  rite delivered for this ticket.** A quote rite cannot find is refused. So
  the Owner cannot put its own idea in the User's mouth.

**rite writes the parts that carry authority.** The message goes out
through `managers.asking.raise_to_person`, the one way rite puts a question
in front of a person, whose first line (`<ID> · q1a2b · Manager lead is
waiting · reply in this thread`) is what an answer is matched by. The line
under it, `Refinement of <ID>, round k of N`, is rite's too. An item the
Owner tagged `[proposed]` is
rendered as *proposed by <manager>, not from the ticket or your answers*.
The closing line naming the accept words is rite's. The Owner's words are
never a header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_QUESTIONS = 3

_QUESTIONS = re.compile(r"^\s*questions\s*:\s*$", re.IGNORECASE)
_PROPOSAL = re.compile(r"^\s*proposal\s*:\s*$", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(\S.*)$")
_ITEM = re.compile(r"^\s*[-*]\s+(\S.*)$")
_HEADER_LIKE = re.compile(
    r"refinement of \S+, round \d+|reply in this thread|^\s*rite:",
    re.IGNORECASE,
)
"""rite's own first line. The Owner may not write one: a second header in
the body is a line a person could take as rite's."""
_TAG = re.compile(r'\[\s*(ticket|answer)\s*:\s*"([^"]+)"\s*\]|\[\s*(proposed)\s*\]')


@dataclass(frozen=True)
class Item:
    text: str
    """What the item says, tags removed."""
    quotes: tuple[tuple[str, str], ...] = ()
    """(source, quote) pairs: `ticket` or `answer`."""
    proposed: bool = False


@dataclass
class Ask:
    """A message that passed: what rite will send."""

    intro: str = ""
    questions: list[str] = field(default_factory=list)
    proposal: list[Item] = field(default_factory=list)
    outro: str = ""


@dataclass
class Checked:
    ask: Ask | None
    problems: list[str]

    @property
    def ok(self) -> bool:
        return self.ask is not None and not self.problems


def _parse_item(line: str) -> Item:
    tags = list(_TAG.finditer(line))
    quotes = tuple((m.group(1), m.group(2)) for m in tags if m.group(1))
    proposed = any(m.group(3) for m in tags)
    text = _TAG.sub("", line).strip().rstrip(",;").strip()
    return Item(text=text, quotes=quotes, proposed=proposed)


def _quoted_in(quote: str, text: str) -> bool:
    """`quote` appears in `text` as whole words: "ok" is not in "look"."""
    pattern = (r"\b" if quote[:1].isalnum() else "") + re.escape(quote)
    pattern += r"\b" if quote[-1:].isalnum() else ""
    return re.search(pattern, text) is not None


def _carries_meaning(quote: str, accept_words) -> bool:
    """A quote says where an item came from only if it holds a real word:
    one of at least three letters that is not an accept word. Found live
    (TR2, 2026-09-29): an item the Owner invented was tagged
    `[answer: "ok"]`, which is in his reply and says nothing about the item."""
    accepting = {w.casefold() for w in accept_words}
    return any(
        len(word) >= 3 and word.casefold() not in accepting
        for word in re.findall(r"[A-Za-z]+", quote)
    )


def check(
    text: str,
    *,
    k: int,
    ticket_text: str,
    answers: list[str],
    accept_words=("ok", "yes", "accept", "lgtm", "proceed"),
) -> Checked:
    """Parse and lint one round's message. Every problem is listed, so the
    Owner fixes the message once, not one refusal at a time. **There is no
    cap on rounds** (Robert's correction to TRQ2): a complex topic takes the
    rounds it needs while he is answering."""
    problems: list[str] = []
    ask = Ask()
    section = "intro"
    intro: list[str] = []
    outro: list[str] = []
    numbers: list[int] = []
    for n, line in enumerate(text.splitlines(), 1):
        if _HEADER_LIKE.search(line):
            problems.append(
                f"line {n} reads like rite's own first line; rite writes that "
                "line, and the message may not carry another"
            )
            continue
        if _QUESTIONS.match(line):
            section = "questions"
            continue
        if _PROPOSAL.match(line):
            section = "proposal"
            continue
        if not line.strip():
            # A blank line after a section's entries ends it; what follows is
            # the Owner's closing words, unless another heading starts.
            if (section == "questions" and ask.questions) or (
                section == "proposal" and ask.proposal
            ):
                section = "outro"
            continue
        if section == "questions":
            numbered = _NUMBERED.match(line)
            if numbered:
                numbers.append(int(numbered.group(1)))
                # Tags belong on proposal items. In a question they would be
                # shown to him raw (found live), so they are dropped.
                ask.questions.append(_TAG.sub("", numbered.group(2)).strip())
                continue
            if ask.questions and not _ITEM.match(line):
                # A continuation of the question above.
                ask.questions[-1] += " " + _TAG.sub("", line).strip()
                continue
            problems.append(
                f"line {n} is under Questions: and is not a numbered question (`1. …`)"
            )
            continue
        if section == "proposal":
            item = _ITEM.match(line)
            if not item:
                problems.append(
                    f"line {n} is under Proposal: and is not an item (`- …`)"
                )
                continue
            ask.proposal.append(_parse_item(item.group(1)))
            continue
        if "?" in line:
            problems.append(
                f"line {n} asks something outside Questions: — every question "
                "is numbered there, so the User can answer each by number"
            )
        (intro if section == "intro" else outro).append(line.rstrip())
    ask.intro = "\n".join(intro).strip()
    ask.outro = "\n".join(outro).strip()

    if len(ask.questions) > MAX_QUESTIONS:
        problems.append(
            f"{len(ask.questions)} questions; a round asks at most {MAX_QUESTIONS}. "
            "Ask the ones that decide the most, and propose the rest"
        )
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        problems.append(
            f"the questions are numbered {numbers}; number them 1, 2, 3 so "
            "the User can answer by number"
        )
    if not ask.questions and not ask.proposal:
        problems.append(
            "no numbered questions under Questions: and no items under "
            "Proposal: — a round asks or proposes"
        )
    if k >= 2 and not ask.proposal:
        problems.append(
            f"round {k} must carry a proposal under Proposal:, a definition "
            "of done the User can accept in one word. From round 2 on every "
            "round proposes, and a reply that hands you the decision is "
            "answered by your recommendation, as a proposal"
        )
    haystacks = {"ticket": [ticket_text], "answer": list(answers)}
    for i, item in enumerate(ask.proposal, 1):
        if not item.text:
            problems.append(f"proposal item {i} says nothing but its tags")
        if not item.quotes and not item.proposed:
            problems.append(
                f"proposal item {i} does not say where it came from: end it "
                'with [ticket: "…"], [answer: "…"] or [proposed]'
            )
        if item.quotes and item.proposed:
            problems.append(
                f"proposal item {i} is tagged both as quoted and as proposed; "
                "split it, so the User sees which part is yours"
            )
        for source, quote in item.quotes:
            if not _carries_meaning(quote, accept_words):
                problems.append(
                    f'proposal item {i} quotes {source}: "{quote}", which is too '
                    "short to say where the item came from. Quote the words that "
                    "say it, or tag it [proposed]"
                )
                continue
            if not any(_quoted_in(quote, hay) for hay in haystacks[source]):
                where = (
                    "the ticket's title or description"
                    if source == "ticket"
                    else "any answer rite delivered for this ticket"
                )
                problems.append(
                    f'proposal item {i} quotes {source}: "{quote}", which is '
                    f"not in {where}. Quote exactly, or tag it [proposed]"
                )
    return Checked(None if problems else ask, problems)


def first_line(ticket: str, k: int) -> str:
    """rite's line naming the round. The thread label an answer is matched
    by is `asking`'s line above it."""
    return f"Refinement of {ticket}, round {k}"


def normalised_items(items) -> tuple[str, ...]:
    """A proposal as compared across rounds: each item's words, case and
    spacing aside. Rewording counts as a change; that is his to judge."""
    return tuple(" ".join(str(i).casefold().split()) for i in items)


_ORDINAL = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}


def unchanged_line(same: int, since: int) -> str:
    """rite's line when a proposal is the same as the round before it: no
    progress, made visible, never enforced (the coordinator's safeguard for
    uncapped rounds). `same` counts this round too."""
    nth = _ORDINAL.get(same, f"{same}th")
    return (
        f"rite: this is the {nth} round in a row with the same proposal: "
        f"nothing in it has changed since round {since}. Reply `ok` to accept "
        "it as it is, or say what is still wrong with it."
    )


def render(
    ask: Ask,
    *,
    ticket: str,
    k: int,
    manager: str,
    accept_words: list[str],
    unchanged: str = "",
) -> str:
    """The message the User sees. rite's lines are rite's; the Owner's
    `[proposed]` items are said to be the Owner's; `unchanged` is rite's
    line when the proposal has not moved."""
    lines = [first_line(ticket, k)]
    if unchanged:
        lines += ["", unchanged]
    if ask.intro:
        lines += ["", ask.intro]
    if ask.questions:
        lines += [""] + [f"{n}. {q}" for n, q in enumerate(ask.questions, 1)]
    if ask.proposal:
        lines += ["", "Proposed definition of done:"]
        for n, item in enumerate(ask.proposal, 1):
            if item.proposed:
                source = f"proposed by {manager}, not from the ticket or your answers"
            else:
                source = "; ".join(f'from the {s}: "{q}"' for s, q in item.quotes)
            lines.append(f"{n}. {item.text} ({source})")
    if ask.outro:
        lines += ["", ask.outro]
    lines.append("")
    if ask.proposal:
        words = ", ".join(f"`{w}`" for w in accept_words)
        lines.append(
            f"Reply {words} to accept this exactly as written, or correct any line."
            + (" Or answer the questions by number." if ask.questions else "")
        )
    else:
        lines.append("Answer by number. Or reply `you decide` for my recommendation.")
    return "\n".join(lines)
