"""Which mechanism confines a Manager on this machine (C28).

⚠ **WHAT THIS REPLACES.** The launch path called `enclosure.write_profile`
and `enclosure.wrap` unconditionally, so every Manager on every platform was
launched with `sandbox-exec`. On Linux that binary does not exist, and the
failure arrived as a Manager that started and vanished: four of CI's nine
failures were exactly that, reporting `sandbox-exec -f … is not running`
rather than "this platform has no seatbelt". A boundary chosen by the
platform is what makes the README's Linux claim true.

**Shaped after `engines.Spelling`**, which is this codebase's precedent for
"one seam, declared once" rather than `if platform ==` scattered through the
callers: a frozen dataclass of what a mechanism can do, a registry keyed by
the thing that decides, and a resolver that **raises** rather than defaults
when it does not recognise the key.

⚠ **THE RESOLVER RAISES, AND THAT IS THE DESIGN.** `spelling_for` refuses a
`local:*` engine with no agent instead of falling back to Claude's flags,
because a silent fallback is the shape of a caller that dropped an argument.
The same reasoning is louder here: a default boundary on an unrecognised
platform would either be a `sandbox-exec` that is not there, or — worse —
nothing at all, and a Manager running unconfined while every report says it
is confined. So an unknown platform is a refusal with a reason.

⚠ **THE TWO IMPLEMENTATIONS ARE NOT SHARED CODE.** `enclosure.py` composes a
seatbelt profile; `landlock.py` builds a Landlock ruleset. They agree on the
INTENT — the project readable and writable, system and tool paths readable,
`$HOME` and other projects denied — and each expresses it in the only
vocabulary its kernel has. Extracting a common policy would mean rewriting
the seatbelt profile, whose behaviour is pinned by
`test_the_manager_profile_denies_what_it_should.py`; the macOS path comes
through this change byte-for-byte unchanged.

⚠ **AND THEY DO NOT BUY THE SAME THING.** Both close the signal escape —
seatbelt with `(allow signal (target same-sandbox))`, Landlock with
`LANDLOCK_SCOPE_SIGNAL` where the kernel is new enough. Only seatbelt closes
the socket escape: Landlock does not govern `connect(2)`, so a unix socket in
a denied directory stays reachable on Linux. Each backend's own
`limitations()` says what IT does not do, which is why that is a field here
rather than one shared paragraph.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import enclosure, landlock

SEATBELT = "seatbelt"
LANDLOCK = "landlock"


class UnsupportedPlatform(RuntimeError):
    """This machine has no Manager boundary rite knows how to build.

    Raised at launch, deliberately, rather than letting a Manager start
    unconfined or start with a command that is not there.
    """


@dataclass(frozen=True)
class Boundary:
    """One mechanism for confining one Manager.

    Every field is a thing the launch path already needed; nothing here
    decides WHETHER to confine a Manager, which is Robert's decision and
    unconditional (B9).
    """

    name: str
    """What to call it in `rite doctor` and in a refusal. Matches the
    `--backend` vocabulary already in use for Workers: "seatbelt" is rite's
    name for macOS's `sandbox-exec` (D-30), not the binary's name."""

    mechanism: str
    """One line for a human reading `rite doctor`, naming the OS facility so
    that somebody can look it up."""

    write_profile: Callable[..., Path]
    """`(root, manager, home=None) -> Path`. Writes the policy for one
    Manager and returns where it went. Rewritten every run in both backends,
    so widening or narrowing the surface in a later release reaches existing
    projects."""

    wrap: Callable[[str, Path], str]
    """`(command, profile) -> str`. PREFIXES rather than rebuilds: the command
    already carries the engine's vocabulary and, for Claude, a stdin
    redirection the surrounding shell applies. Both backends promise that."""

    limitations: Callable[[], tuple[str, ...]]
    """What this boundary does NOT do, PRINTED every run rather than left in
    a design note. ⚠ Per-backend because the answers differ, and Landlock's
    is computed rather than constant — signal scoping needs a kernel 6.12, so
    on an older one the signal escape is open and the list has to say so."""

    refusal_looks_like_ours: Callable[[str], bool]
    """`(pane_text) -> bool`. Whether a failure in the pane might be this
    boundary refusing something, so the supervisor can explain it instead of
    reading as a broken rite."""

    why_it_was_refused: Callable[[Path, str], str]
    """`(root, manager) -> str`. What to tell that user, pointing at the
    policy file rather than leaving them to guess."""

    engine_tmp: Callable[[Path, str], Path]
    """`(root, manager) -> Path`. The `TMPDIR` the engine gets, inside the
    boundary. Shared between the backends today — the reason it exists is the
    engine's, not the mechanism's — but reached through here so a backend that
    needs a different answer can give one."""

    available: Callable[[], bool] = lambda: True
    """Whether the mechanism can actually be used on THIS machine, as opposed
    to being the right one for this platform. Landlock answers no on a kernel
    older than 5.13; seatbelt is part of macOS."""

    unavailable_reason: Callable[[], str] = lambda: ""
    """Why not, when `available()` is False. Printed at launch."""


def _landlock_available() -> bool:
    return landlock.abi() > 0


def _landlock_unavailable_reason() -> str:
    return (
        "this kernel has no Landlock, which is how rite confines a Manager on "
        "Linux. Landlock arrives in Linux 5.13; signal isolation needs 6.12"
    )


SEATBELT_BOUNDARY = Boundary(
    name=SEATBELT,
    mechanism="macOS sandbox-exec (seatbelt), a deny-by-default profile",
    write_profile=enclosure.write_profile,
    wrap=enclosure.wrap,
    limitations=enclosure.limitations,
    refusal_looks_like_ours=enclosure.refusal_looks_like_ours,
    why_it_was_refused=enclosure.why_it_was_refused,
    engine_tmp=enclosure.engine_tmp,
)

LANDLOCK_BOUNDARY = Boundary(
    name=LANDLOCK,
    mechanism="Linux Landlock, an unprivileged LSM ruleset applied before exec",
    write_profile=landlock.write_profile,
    wrap=landlock.wrap,
    limitations=landlock.limitations,
    refusal_looks_like_ours=landlock.refusal_looks_like_ours,
    why_it_was_refused=landlock.why_it_was_refused,
    engine_tmp=landlock.engine_tmp,
    available=_landlock_available,
    unavailable_reason=_landlock_unavailable_reason,
)

# Keyed by `platform.system()`, which is the thing that decides.
_BY_SYSTEM: dict[str, Boundary] = {
    "Darwin": SEATBELT_BOUNDARY,
    "Linux": LANDLOCK_BOUNDARY,
}


def boundary_for(system: str = "") -> Boundary:
    """Which mechanism confines a Manager here.

    ⚠ **Raises rather than defaults**, both for a platform with no backend and
    for a backend whose mechanism this machine does not have. The caller is
    the launch path, and the alternative to raising is a Manager that appears
    to start: on Linux it was `sandbox-exec` not found, reported as "the
    session started and exited immediately", which is how a whole class of CI
    failure looked for a day.
    """
    key = system or platform.system()
    boundary = _BY_SYSTEM.get(key)
    if boundary is None:
        known = ", ".join(sorted(_BY_SYSTEM))
        raise UnsupportedPlatform(
            f"rite has no Manager boundary for {key!r} — it knows {known}. "
            "A Manager is always confined (B9), so rite will not start one "
            "here rather than start it unconfined"
        )
    if not boundary.available():
        raise UnsupportedPlatform(
            f"rite would confine this Manager with {boundary.name}, but "
            f"{boundary.unavailable_reason()}. A Manager is always confined "
            "(B9), so rite will not start one here rather than start it "
            "unconfined"
        )
    return boundary


def describe() -> str:
    """One line for `rite doctor`: which backend, or why there is none."""
    try:
        boundary = boundary_for()
    except UnsupportedPlatform as exc:
        return f"UNAVAILABLE — {exc}"
    return f"{boundary.name} ({boundary.mechanism})"
