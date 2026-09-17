"""A slice: what a Worker loads for one unit of the spec.

A slice is the unit, the units it references directly, and the pinned hubs.
**Not its transitive dependencies, and that is deliberate.** Following every
reference on rite's own spec pulled in 70.5% of the document at the median —
the monolith rebuilt with extra steps. Depth-1 with hubs pinned came to 2.3% at
the median and 9.3% at p90, plus an 8.4% preamble of pinned hubs.

So a depth-2 dependency exists and is not loaded. That is the trade, not an
oversight, and the obvious "improvement" to transitive closure is the one that
defeats the purpose. `tests/test_spec_slice.py` pins it against transitive on
rite's own spec so the reasoning survives after the design document stops being
read. Whether depth-1 is enough is measured, not argued: a Worker that has to
read the whole spec records a fallback, and the insufficiency rate
(`telemetry`) is the evidence for going deeper.

An **index** is not a retrieval target — nobody's ticket is "implement the
revision history" — and is never traversed into. A **hub** is always loaded
and never traversed into. Lines are counted once each, so a decision row inside
a section it is sliced with is not counted twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.spec.graph import HUB, INDEX, Graph


@dataclass(frozen=True)
class Slice:
    target: str
    units: tuple[str, ...]  # the target and what it references, in spec order
    pinned: tuple[str, ...]  # hubs, loaded whatever the target
    lines: int  # distinct source lines across units and pinned
    unpinned_lines: int  # distinct source lines across units alone
    total_lines: int

    @property
    def ratio(self) -> float:
        """What the Worker loads, as a share of the spec."""
        return self.lines / self.total_lines if self.total_lines else 0.0

    @property
    def unpinned_ratio(self) -> float:
        return self.unpinned_lines / self.total_lines if self.total_lines else 0.0


class NotATarget(ValueError):
    """The unit cannot be sliced: it does not exist, or it is an index."""


def _lines(graph: Graph, ids: set[str]) -> int:
    covered: set[tuple[str, int]] = set()
    for uid in ids:
        unit = graph.units[uid]
        covered.update((unit.source, n) for n in range(unit.start, unit.end + 1))
    return len(covered)


def _in_spec_order(graph: Graph, ids: set[str]) -> tuple[str, ...]:
    return tuple(
        sorted(ids, key=lambda u: (graph.units[u].source, graph.units[u].start, u))
    )


def closure(
    graph: Graph,
    kinds: dict[str, str],
    target: str,
    depth: int | None,
    *,
    respect_kinds: bool = True,
) -> set[str]:
    """Everything reachable from `target` within `depth` hops (None: no limit).

    With `respect_kinds`, hubs and indexes are never traversed into. Transitive
    closure exists here to be measured against, not to be used.
    """
    seen, frontier, hops = {target}, {target}, 0
    while frontier and (depth is None or hops < depth):
        reached: set[str] = set()
        for uid in frontier:
            if respect_kinds and uid != target and kinds.get(uid) in (HUB, INDEX):
                continue
            reached |= graph.edges.get(uid, frozenset())
        if respect_kinds:
            reached = {u for u in reached if kinds.get(u) != INDEX}
        reached -= seen
        if not reached:
            break
        seen |= reached
        frontier = reached
        hops += 1
    return seen


ALLOWED_DEPTHS = (1, 2)


def compute_slice(
    graph: Graph,
    kinds: dict[str, str],
    target: str,
    total_lines: int,
    depth: int = 1,
) -> Slice:
    """`depth` is `spec.slice_depth`: 1 unless the insufficiency rate says
    otherwise, and never more than 2 — see the module docstring."""
    if type(depth) is not int or depth not in ALLOWED_DEPTHS:
        raise ValueError(
            f"slice depth {depth} — only {ALLOWED_DEPTHS} are allowed; following "
            "every reference rebuilds most of the spec"
        )
    if target not in graph.units:
        raise NotATarget(f"no unit '{target}' in the spec")
    if kinds.get(target) == INDEX:
        raise NotATarget(
            f"'{target}' is an index — it points at other units rather than "
            "saying anything itself; slice one of the units it lists"
        )
    pinned = {uid for uid, kind in kinds.items() if kind == HUB}
    units = closure(graph, kinds, target, depth)
    return Slice(
        target=target,
        units=_in_spec_order(graph, units),
        pinned=_in_spec_order(graph, pinned - units),
        lines=_lines(graph, units | pinned),
        unpinned_lines=_lines(graph, units),
        total_lines=total_lines,
    )
