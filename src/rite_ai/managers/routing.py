"""The Owner routes work to the other Managers in its root (multi-Manager, MM-3).

**Robert's shape for v0.6.0:** one project root, a Claude Manager as Owner and
one or more secondaries (typically local). The Owner is the only Manager that
talks to Slack (`config.managers.routing_owner`), and it hands work to the
others. A secondary's instructions come from the Owner Manager and from this
machine — never from Slack, and never from a sibling.

## Who may write a Manager's inbox — the reason this module is shaped as it is

**An inbox write IS an instruction**, so the writer is what carries authority,
and the content is never trusted to say who wrote it. Since MM-2 a Manager's
sandbox profile refuses writes to every Manager's inbox, its own included
(`enclosure._manager_separation`). So the Owner cannot write a secondary's
inbox either — it ASKS, the way it asks for a Worker (`broker`):

1. inside its boundary the Owner runs `rite route <manager> "<text>"`, which
   writes a request into the Owner's OWN directory;
2. the Owner's supervisor, OUTSIDE the boundary, takes the request and
   decides. **Who is asking comes from the supervisor** — the Manager it is
   supervising — never from the request, and routing happens only if that
   Manager is the one holding `route` in this root;
3. the supervisor writes the target's inbox, under a header rite composed and
   with every line of the Owner's text quoted, so text cannot forge a header.

⚠ **A secondary's route request is inert by construction.** Only the routing
Owner's supervisor honours requests, so a request written by any other Manager
is discarded, and said to be. There is no check here that a clever request
could satisfy, because nothing in the request is consulted about authority.

## What the request may carry

**Two values and no others**, as in the broker: which Manager, and the text.
An unknown key is refused rather than ignored — an ignored field is one
somebody is trying to have an effect with.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rite_ai.managers import manager_dir
from rite_ai.managers.mailbox import INBOX, send
from rite_ai.names import name_problem
from rite_ai.state import write_atomic

ROUTES_DIRNAME = "routes"
ALLOWED_KEYS = frozenset({"to", "text"})
MAX_REQUEST_BYTES = 16 * 1024


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""
    to: str = ""
    text: str = ""


def _routes_dir(root: Path, manager: str) -> Path:
    """Where a Manager writes route requests: its OWN directory, which its
    profile lets it write — unlike any inbox."""
    return manager_dir(root, manager) / ROUTES_DIRNAME


def request(root: Path, manager: str, to: str, text: str) -> Path:
    """Write one route request, as `manager`, into its own directory."""
    where = _routes_dir(root, manager)
    where.mkdir(parents=True, exist_ok=True)
    path = where / f"{time.time_ns()}.json"
    write_atomic(path, json.dumps({"to": to, "text": text}) + "\n")
    return path


def take(root: Path, manager: str) -> list[str]:
    """Every pending request's raw text, oldest first, removed as it is read —
    a request left behind would be delivered twice."""
    where = _routes_dir(root, manager)
    if not where.is_dir():
        return []
    found: list[str] = []
    for path in sorted(where.glob("*.json")):
        try:
            found.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        try:
            path.unlink()
        except OSError:
            pass
    return found


def decide(raw: str, *, owner: str, managers: list[str]) -> Decision:
    """Whether to deliver one request. Every branch that is not a clean,
    declared target refuses."""
    if len(raw.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES:
        return Decision(False, f"refused: larger than {MAX_REQUEST_BYTES} bytes")
    try:
        data = json.loads(raw)
    except ValueError:
        return Decision(False, "refused: not JSON")
    if not isinstance(data, dict):
        return Decision(False, "refused: not a JSON object")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        return Decision(
            False, f"refused: unknown key(s) {sorted(unknown)} — only 'to' and 'text'"
        )
    to, text = data.get("to"), data.get("text")
    if not isinstance(to, str) or name_problem(
        to, kind="manager name", must_be_a_tmux_target=True
    ):
        return Decision(False, f"refused: {to!r} is not a Manager name")
    if to == owner:
        return Decision(False, f"refused: {owner!r} cannot route to itself")
    if to not in managers:
        known = ", ".join(m for m in managers if m != owner) or "none"
        return Decision(
            False, f"refused: no Manager {to!r} in this root (others: {known})"
        )
    if not isinstance(text, str) or not text.strip():
        return Decision(False, f"refused: nothing to route to {to!r}")
    return Decision(True, to=to, text=text)


def _quoted(text: str) -> str:
    """Every line quoted, so routed text cannot forge rite's header — the rule
    the Slack relay uses (`slack._quoted`) for the same reason."""
    return "\n".join(f"> {line}" for line in text.strip().splitlines())


def _routed_message(owner: str, text: str, now: datetime | None = None) -> str:
    """What the secondary receives: rite's header, then the Owner's text."""
    when = (now or datetime.now()).strftime("%a %H:%M")
    return (
        f"[routed by the Owner Manager {owner!r} · sent {when} · INSTRUCTION]\n"
        f"{_quoted(text)}"
    )


def deliver_routes(
    root: Path, manager: str, owner: str, managers: list[str], say
) -> int:
    """Deliver what `manager` asked to route, if it is the Owner. Returns how
    many were delivered.

    `manager` is the Manager this supervisor runs — the identity comes from
    here, never from a request. When it is not the routing Owner (or there is
    none), its requests are discarded and that is said: a secondary asking to
    route is either confused or compromised, and both are worth seeing.
    """
    pending = take(root, manager)
    if not pending:
        return 0
    if manager != owner:
        say(
            f"{manager!r} asked to route {len(pending)} message(s) and is not "
            f"the Manager holding 'route'"
            + (f" (that is {owner!r})" if owner else " (no Manager holds it)")
            + " — discarded, nothing was delivered."
        )
        return 0
    delivered = 0
    for raw in pending:
        verdict = decide(raw, owner=owner, managers=managers)
        if not verdict.ok:
            say(f"route from {owner!r}: {verdict.reason}")
            continue
        send(root, verdict.to, INBOX, _routed_message(owner, verdict.text))
        delivered += 1
        say(f"routed from {owner!r} to {verdict.to!r}")
    return delivered
