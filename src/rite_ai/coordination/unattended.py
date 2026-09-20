"""May a tick nobody is watching hand work out? (Q9)

Everything else `ManagerMonitor.tick()` does writes to the coordination branch:
rite talking to itself, on a ref excluded from CI, where the worst outcome of a
wrong write is that rite is confused about rite. **Distribution is different.**
It puts and removes labels on a shared board that other people read and other
tools trigger on, and it does it from cron, with nobody at the keyboard. That
is why the scheduler's own docstring says distribution "deserves its own
decision (Q9)" and then passes no backend.

Passing no backend is not a decision, though — it is the absence of one
wearing a decision's clothes. A Manager whose monitor was never given a board
and a Manager with nothing to hand out produce the same tick, so a fleet that
is not distributing at all looks exactly like a fleet with an empty queue. The
switch is made real here so the off position can say it is off.

**The switch, and the three rules that are always on.** Q9's answer is the
middle option — `coordination.assign_unattended`, default false, with
assignment wired behind it — and three unambiguity rules that hold whether it
is on or off, because each closes a failure that is invisible when it happens:

1. never assign a ticket that already carries a Manager's name. Assignment ADDS
   a name and leaves `scheduled` in place, so an assigned ticket still matches
   the backlog query and would be assigned again next tick, to a different
   Manager. Both then hand it to a Worker. Each write looks correct alone;
2. never assign to a Manager that refused this ticket. A refusal costs the
   refuser nothing, so it is still the least loaded and gets the same ticket
   back every five minutes for ever, with a board comment each time. Recorded
   on rite's own message log, which it can read — not in board comments, which
   nothing reads back;
3. never assign past the schedule. Routing by in-flight alone puts work on a
   Manager that is already at its Worker limit, where it sits as an invisible
   backlog on one machine while others idle.

Rules 1-3 are enforced in `scheduler._assign_the_pool` and `refusal.py`; this
module holds the switch.

**On rule 3's capacity, checked rather than assumed.** The heartbeat carries
`in_flight` and no ceiling, and `refusal.py` flags the cost — "'full' cannot be
refused safely until the Owner can see capacity". But the ceiling needs no new
field: `schedule:` lives in the committed `config.yaml`, so every machine reads
the same windows, and `workers_at(schedule, minute, weekday)` gives the Owner exactly
the number the receiving machine will apply to itself. A published capacity
becomes necessary the day machines may carry different schedules, and that is
not a shape this config has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from rite_ai.config.models import RiteProject


def distribution_refusal(project: RiteProject) -> str:
    """Empty when an unattended tick may distribute, otherwise why it may not.

    A reason, never a bare False: the caller's job is to tell a human why an
    overnight run handed nothing out, and "False" does not survive being
    written into a log at three in the morning.
    """
    config = project.config.coordination
    if not config.assign_unattended:
        return (
            "coordination.assign_unattended is false, so this tick may not "
            "put work on the board — set it to true in .rite/config.yaml to let "
            "an unattended tick hand this Manager's tickets to its Workers"
        )

    backend = project.config.ticket_backend
    if backend.type == "none":
        # Distinct from the switch: somebody turned this on and expected work
        # to move. "Nothing happened" is the answer this file exists to stop.
        return (
            "coordination.assign_unattended is true but ticket_backend.type "
            "is 'none' — there is no board to hand work out on"
        )

    if not project.workers:
        return (
            "no workers are registered, so there is nobody to hand work to "
            "(`rite add worker <name>`)"
        )

    return ""
