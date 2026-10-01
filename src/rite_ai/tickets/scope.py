"""A project reads only its own tickets on a board it shares (v0.7.0 dogfood S1).

**What happened.** Three rite projects on one machine read Jira project KAN on
`ritetest`: two pingr projects and the yoloAI one. Refinement lists every
ticket labelled `scheduled` on the board, and distribution every ticket
labelled with a Manager's name, with nothing saying which rite project a ticket
belongs to. Measured before the yoloAI run: KAN-28 and KAN-29 (yoloAI's)
carried no label and would never have been refined, and KAN-6 to KAN-9
(pingr's) carried `scheduled` and would have been refined against yoloAI.

**What this does** (Robert, 2026-09-29, option A):

- `ticket_backend.scope_label`, when set, is ANDed into every list rite makes
  of the board — refinement, loop, scheduler, distribution, status, `rite
  board list` — and stamped on every ticket rite creates, chores included.
  One wrapper around the board (`Scoped`), built in the one place every board
  is built (`create_backend_from_config`), so no caller can forget it and a new
  caller gets it without knowing it exists.
- `rite init` sets it to the project's name, so a new project is scoped.
- Empty is today's behaviour: an existing project reads its whole board, as
  it did.
- `rite start` refuses a Manager, and `rite doctor` reports a problem, when
  another rite project on this machine reads the same board and either of the
  two is unscoped, or both use the same scope label (`sharing_problems`).

**What it does not do.** A raw `rite board query` (JQL or GitHub search) is
passed through as typed: it is the escape hatch, and scoping it would make a
person's own query answer something other than what they asked. A read BY ID
is not scoped either: an id names one ticket, and refusing it because of a
label would turn "which project is this?" into "the ticket does not exist".
The refusal sees only projects on THIS machine (`machine_projects`); a project
on another machine sharing the board is not visible to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from rite_ai.tickets.interface import TicketBackend, TicketFilter

RESERVED = frozenset({"scheduled", "chore", "blocked", "ready-to-work"})
"""rite's own label words (`coordination.ticket_labels`, `managers.chores`,
`refinement.protocol`, `refinement.view`). A scope label equal to one would
put every one of the project's tickets into that state."""

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def label_for(name: str) -> str:
    """The scope label `rite init` gives a project called `name`.

    A Jira label cannot hold a space, and a GitHub one is clearer without, so
    the name is lowercased and anything outside `[a-z0-9_.-]` becomes `-`. A
    name that comes out empty or as one of rite's own words gets `project-`
    in front, rather than silently becoming `scheduled`."""
    label = re.sub(r"[^a-z0-9_.-]+", "-", name.strip().lower()).strip("-.")
    if not label or label in RESERVED:
        label = f"project-{label or 'rite'}"
    return label


def label_problem(label: str) -> str:
    """Why `label` cannot be a scope label, or ""."""
    if not label:
        return ""
    if not _LABEL_RE.match(label):
        return (
            f"ticket_backend.scope_label {label!r} is not a label a board can "
            "hold: letters, digits, `_`, `.` and `-` only, no spaces"
        )
    if label.lower() in RESERVED:
        return (
            f"ticket_backend.scope_label {label!r} is one of rite's own labels "
            f"({', '.join(sorted(RESERVED))}), so every ticket of this project "
            "would be in that state"
        )
    return ""


@dataclass
class Scoped(TicketBackend):
    """A board seen through one project's scope label (module doc)."""

    inner: TicketBackend
    scope: str

    def _scoped(self, filters: TicketFilter | None) -> TicketFilter:
        f = filters or TicketFilter()
        return replace(f, labels=[*(f.labels or []), self.scope])

    def list_tickets(self, filters: TicketFilter | None = None):
        return self.inner.list_tickets(self._scoped(filters))

    def matches(self, ticket, filters):
        # ⚠ Scoped too: `ReadsItsOwnWrites` asks this about each ticket rite
        # wrote, and an unscoped answer would put a ticket rite labelled for
        # ANOTHER purpose back into this project's list.
        if self.scope not in (ticket.labels or []):
            return False
        return self.inner.matches(ticket, self._scoped(filters))

    def create(self, title, description="", labels=None):
        stamped = [*(labels or [])]
        if self.scope not in stamped:
            stamped.append(self.scope)
        return self.inner.create(title, description=description, labels=stamped)

    # --- unchanged: by id, writes to an existing ticket, the escape hatch ----

    def read(self, ticket_id):
        return self.inner.read(ticket_id)

    def update(self, ticket_id, **fields):
        return self.inner.update(ticket_id, **fields)

    def move(self, ticket_id, status):
        return self.inner.move(ticket_id, status)

    def assign(self, ticket_id, worker):
        return self.inner.assign(ticket_id, worker)

    def label(self, ticket_id, labels, remove=None):
        return self.inner.label(ticket_id, labels, remove=remove)

    def missing(self, error):
        return self.inner.missing(error)

    def can_create(self):
        return self.inner.can_create()

    def comment(self, ticket_id, text):
        return self.inner.comment(ticket_id, text)

    def read_thread(self, ticket_id):
        return self.inner.read_thread(ticket_id)

    def query(self, raw_query):
        return self.inner.query(raw_query)

    def link(self, ticket_id, target_id, link_type):
        return self.inner.link(ticket_id, target_id, link_type)

    def describe_label(self, name, description, color):
        # Optional (GitHub only): present here only when the board has it.
        describe = getattr(self.inner, "describe_label", None)
        return describe(name, description, color) if callable(describe) else None


def unwrapped(board):
    """The board itself, through every rite wrapper (`ReadsItsOwnWrites`,
    `Scoped`), for a caller that needs to know which kind of board it is."""
    while getattr(board, "inner", None) is not None:
        board = board.inner
    return board


# --- two projects on one board ---------------------------------------------------


def board_keys(tb) -> set[tuple[str, ...]]:
    """What a `TicketBackendConfig` reads, comparably: every Jira project key it
    maps a role to on its site, or its GitHub repository."""
    if tb.type == "jira":
        from rite_ai.tickets.jira import normalise_site

        site = normalise_site(tb.site or "").lower()
        return {("jira", site, key.upper()) for key in tb.projects.values() if key}
    if tb.type == "github" and tb.repo:
        return {("github", tb.repo.strip().lower())}
    return set()


def _shown(key: tuple[str, ...]) -> str:
    return f"Jira {key[2]} on {key[1]}" if key[0] == "jira" else f"GitHub {key[1]}"


def sharing_problems(root: Path, config) -> list[str]:
    """Other rite projects on this machine reading a board this one reads, where
    either is unscoped or both use the same scope label. One line each.

    ⚠ **Checkouts of the SAME project are not a collision.** Worktrees and
    clones share the committed config, scope label included; they are told
    apart from another project by the credential namespace, which is committed
    with it and generated per project by `rite init`."""
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.machine_projects import known_projects

    mine = board_keys(config.ticket_backend)
    if not mine:
        return []
    here = Path(root).resolve()
    ns = config.credentials.namespace
    scope = config.ticket_backend.scope_label
    problems: list[str] = []
    for other in known_projects():
        if other == here:
            continue
        theirs = parse_config(other / ".rite" / "config.yaml")
        if isinstance(theirs, ParseError):
            continue
        if ns and theirs.credentials.namespace == ns:
            continue  # another checkout of this project
        shared = mine & board_keys(theirs.ticket_backend)
        if not shared:
            continue
        other_scope = theirs.ticket_backend.scope_label
        board = ", ".join(_shown(k) for k in sorted(shared))
        if not scope or not other_scope:
            unscoped = [
                p
                for p, s in (("this project", scope), (str(other), other_scope))
                if not s
            ]
            problems.append(
                f"{other} also reads {board}, and {' and '.join(unscoped)} "
                f"{'has' if len(unscoped) == 1 else 'have'} no "
                "ticket_backend.scope_label, so each would take the other's "
                "scheduled tickets as its own. Give each project its own "
                "scope_label (then label its tickets with it)"
            )
        elif scope == other_scope:
            problems.append(
                f"{other} also reads {board} with the same scope_label "
                f"{scope!r}, so each would take the other's tickets as its own. "
                "Give one of them another scope_label"
            )
    return problems


def name_problems(scope: str, names: list[str]) -> list[str]:
    """A scope label that is also a Worker's or Manager's name: those names are
    the assignment labels (§9.10), so every ticket of the project would read as
    that one's work."""
    return [
        f"ticket_backend.scope_label {scope!r} is also the name of {n!r}, and a "
        "name is an assignment label: every ticket of this project would read "
        "as its work. Rename one of them"
        for n in names
        if scope and n == scope
    ]
