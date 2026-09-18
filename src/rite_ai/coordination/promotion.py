"""`promotion-request.json` — the graceful-demotion request (P2-0d).

SPEC §2.4 "Graceful demotion" step 1: a returning higher-priority Manager
"publishes a promotion request to the coordination repo". §3.3.1 lists the
file on the state branch and calls it momentary — only the pending request
matters. Neither section gives it a shape; this is the proposal.

**`incumbent` is what makes a request safe to leave lying around.** It names
the Owner the request was addressed to. A request whose `incumbent` is not
the current lease holder was meant for an earlier Owner — the requester came
back, asked, and the role changed hands some other way (a lease expiry, a
second return) before anyone acted. Recognising that needs no clock at all,
which matters on a branch whose timestamps are written by other machines.

**Priority is not carried.** Who outranks whom is the order of
`coordination.managers` (D-60); a request only says *who is asking*. Carrying
a priority here would give a stale file a way to disagree with the list.

Parsing follows the two rules P2-0b's schemas already follow, for the same
reasons: unknown fields round-trip, and unreadable bytes are `None` — "could
not read", never "no request" (D-58).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from rite_ai.coordination.schemas import _emit, _KeepsDrift, _text

_KNOWN = frozenset({"requester", "requested", "incumbent"})


@dataclass(eq=True)
class PromotionRequest(_KeepsDrift):
    _TYPED = frozenset({"requester", "requested", "incumbent"})

    requester: str = ""
    """The returning Manager asking for the Owner role back."""

    requested: str = ""
    """ISO-8601 UTC, when it asked. Audit only: staleness is decided by
    `is_addressed_to`, which needs no clock."""

    incumbent: str = ""
    """The lease holder the request was addressed to."""

    extra: dict = field(default_factory=dict)
    drifted: dict = field(default_factory=dict)

    def is_addressed_to(self, lease_owner: str) -> bool:
        """Whether this request is for the Owner holding the lease now.

        False for a request left over from an earlier Owner, which the
        current one must not act on: handing the role to a Manager that asked
        someone else, possibly long ago, is a seizure by accident. False too
        when either name is empty — a request that cannot say who it was for
        cannot be shown to be for anyone."""
        return (
            bool(self.incumbent) and bool(lease_owner) and self.incumbent == lease_owner
        )


def request_from_json(text: str) -> PromotionRequest | None:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    drifted: dict = {}
    return PromotionRequest(
        requester=_text(raw, "requester", drifted),
        requested=_text(raw, "requested", drifted),
        incumbent=_text(raw, "incumbent", drifted),
        extra={k: v for k, v in raw.items() if k not in _KNOWN},
        drifted=drifted,
    )


def request_to_json(request: PromotionRequest) -> str:
    data = _emit(
        {
            "requester": request.requester,
            "requested": request.requested,
            "incumbent": request.incumbent,
        },
        request.drifted,
        request.extra,
    )
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
