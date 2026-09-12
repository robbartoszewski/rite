"""Elapsed time, written the way a person reads it.

Shared because two different surfaces answer "how long?" in text a human
reads unattended — the watchdog's stall reports and the scheduler's gap
between ticks — and `3608s` is the same fact as `1h 0m` only if you are
willing to do the arithmetic at three in the morning.
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """`45s`, `1m 30s`, `4h 12m`.

    Clamped at zero: a clock adjustment can put a recorded time in the
    future, and "-6s ago" reads as a bug in rite rather than as the clock
    change it is.
    """
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if not sec else f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"
