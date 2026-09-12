"""Abstract ticket backend interface (SPEC §6.1).

The interface exposes create/read/update/move/assign/label/list_tickets/
comment/query/link so that JIRA, GitHub Issues, Linear, etc. can be swapped
without touching rite's core logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Ticket:
    id: str
    title: str
    status: str = ""
    assignee: str = ""
    labels: list[str] = field(default_factory=list)
    description: str = ""
    url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TicketFilter:
    status: str | None = None
    assignee: str | None = None
    label: str | None = None
    labels: list[str] | None = None


@dataclass
class BackendError:
    message: str


# Every backend pages its results; none of them can return an unbounded
# board. `PAGE_LIMIT` is that page size, shared so the number a user is
# told matches the number actually requested.
PAGE_LIMIT = 100


class TicketPage(list):
    """The tickets a list/query returned, plus whether the backend had
    more it did not return.

    A plain `list` to every existing caller — but a page of exactly
    `PAGE_LIMIT` rows with no way to tell "that is the whole board" from
    "that is the first hundred of nine hundred" is a number framed to be
    misread, and callers rendering it need to be able to say which it is.
    """

    def __init__(self, tickets=(), truncated: bool = False):
        super().__init__(tickets)
        self.truncated = truncated


def paginate(tickets: list, limit: int = PAGE_LIMIT) -> TicketPage:
    """Trim an over-fetched result (`limit + 1` rows requested) back to
    `limit`, recording whether anything was trimmed. Over-fetching by one
    is how truncation is *known* rather than guessed at from a result
    that happens to be exactly `limit` long."""
    return TicketPage(tickets[:limit], truncated=len(tickets) > limit)


class TicketBackend(ABC):
    @abstractmethod
    def create(
        self,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> Ticket | BackendError: ...

    @abstractmethod
    def read(self, ticket_id: str) -> Ticket | BackendError: ...

    @abstractmethod
    def update(self, ticket_id: str, **fields: str) -> None | BackendError: ...

    @abstractmethod
    def move(self, ticket_id: str, status: str) -> str | None | BackendError:
        """Move the ticket to `status`.

        Returns `None` when the board now holds exactly the status that
        was asked for, and **the status it actually holds instead** when
        it does not — a backend whose columns are not rite's (GitHub has
        only open/closed) or whose transition landed somewhere other than
        its own name. A caller that prints the requested status as the
        outcome states something the board does not say; this return is
        how it can avoid that without a second round-trip.
        """

    @abstractmethod
    def assign(self, ticket_id: str, worker: str) -> None | BackendError: ...

    @abstractmethod
    def label(
        self, ticket_id: str, labels: list[str], remove: list[str] | None = None
    ) -> None | BackendError:
        """Add `labels`, and take `remove` off.

        Removal is part of the primitive because the label IS rite's
        assignment mechanism (SPEC §9.10): a handover that only ADDS
        `scheduled` leaves the departing worker's own label in place, so
        the ticket it just returned to the pool still answers `labels =
        <worker>` and reads as that worker's in-progress work. Removing a
        label the ticket does not carry must not be an error.
        """

    @abstractmethod
    def list_tickets(
        self, filters: TicketFilter | None = None
    ) -> list[Ticket] | BackendError: ...

    @abstractmethod
    def comment(self, ticket_id: str, text: str) -> None | BackendError:
        """Post a comment on the ticket.

        ⚠ No `rite board comment` exists, and that is noted rather than
        fixed: the only caller is the handover (§9.10), which posts one
        comment per transition. A CLI verb with no caller is surface to
        maintain and a shape for a future divergence, so it waits for
        something that needs it."""

    @abstractmethod
    def query(self, raw_query: str) -> list[Ticket] | BackendError:
        """Pass-through for backend-native queries (JQL, GH search qualifiers)."""
        ...

    @abstractmethod
    def link(
        self, ticket_id: str, target_id: str, link_type: str
    ) -> None | BackendError:
        """Cross-ticket relationship, e.g. "blocked by" (SPEC §6.2, D-49).

        A backend with no real link mechanism MUST return a BackendError
        naming the gap — never silently substitute a weaker mechanism (a
        comment, a no-op) that a caller reasoning about dependencies can't
        tell apart from a real link.
        """
        ...
