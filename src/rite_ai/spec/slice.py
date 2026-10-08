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


def render(graph, parsed, computed: Slice) -> str:
    """A slice as the text a reader gets: each unit, labelled, de-duplicated.

    ⚠ **Extracted from `rite spec slice`'s own loop so there is ONE of it.**
    Three callers need a unit's text now — the command, plan-time cite
    validation (RL-63) and the Worker's slice — and a second renderer would be
    a second answer to "what does this unit say".

    A section's range covers the subsections inside it and a slice can hold
    both, so each source line is printed once: a reader seeing one paragraph
    twice under two headings has no way to tell it is one paragraph.
    """
    out: list[str] = []
    shown: set[tuple[str, int]] = set()
    for unit_id in tuple(computed.units) + tuple(computed.pinned):
        found = graph.units.get(unit_id)
        if found is None:
            continue
        lines = parsed.lines.get(found.source, [])
        wanted = [
            n
            for n in range(found.start, found.end + 1)
            if (found.source, n) not in shown
        ]
        if not wanted:
            continue
        shown.update((found.source, n) for n in wanted)
        runs: list[list[int]] = []
        for n in wanted:
            if runs and n == runs[-1][-1] + 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        spans = ", ".join(f"{r[0]}" if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)
        out.append(f"# {unit_id} ({found.source}:{spans})")
        for i, run in enumerate(runs):
            if i:
                out.append("# … part of this unit is printed elsewhere …")
            out.append("\n".join(lines[n - 1] for n in run).rstrip())
        if wanted[0] != found.start or wanted[-1] != found.end or len(runs) > 1:
            out.append(
                f"# (this is {unit_id}, lines {found.start}-{found.end}; the "
                "rest of it is printed elsewhere in this slice)"
            )
        out.append("")
    return "\n".join(out)


def unit_text(root, cite: str) -> tuple[str, str]:
    """(this unit's spec text, or why rite cannot produce it) — SCRUM-92.

    🔴 **Two ways to resolve a cite, and RL-63 is satisfied by either.** The
    DERIVED text under `.rite/spec/units/`, or a SLICE computed from the spec
    source. It used to be the derived file alone, and that made a cite
    unresolvable on any project whose spec is too small to digest — because
    `rite spec index` REFUSES to derive one ("this spec does not decompose …
    reading the whole file costs less"). So the plan validator refused every
    cited subtask, refused every uncited one too, and the remedy its own
    message named was a command that declines to help. Measured 2026-10-08: a
    valid two-subtask plan refused on both subtasks for a unit that was in the
    spec and readable the whole time.

    ⚠ Size- and provider-agnostic: nothing here looks at how big a spec is or
    what engine asked. A unit either resolves to text or it does not.

    ⚠ Never raises. Anything unreadable is a reason, because the callers are a
    validator and a slice builder and neither may die on a malformed spec.
    """
    from pathlib import Path

    root = Path(root)
    try:
        from rite_ai.spec.digest_files import unit_filename, units_dir

        derived = units_dir(root) / unit_filename(cite)
        if derived.exists():
            return derived.read_text(encoding="utf-8", errors="replace"), ""
    except OSError as e:
        return "", f"the derived text for {cite} could not be read ({e})"

    try:
        from rite_ai.config.parse import parse_config
        from rite_ai.spec.graph import build_graph, classify
        from rite_ai.spec.units import parse_paths

        config = parse_config(root / ".rite" / "config.yaml")
        paths = list(getattr(config.spec, "paths", ()) or ())
        if not paths:
            return "", (
                f"{cite} is not derived and this project registers no spec, so "
                "there is nothing to slice it from"
            )
        parsed = parse_paths(root, paths, getattr(config.spec, "extra_units", ()) or ())
        graph = build_graph(parsed)
        kinds = classify(graph, pin_count=config.spec.pin_count)
        computed = compute_slice(
            graph, kinds, cite, parsed.total_lines, depth=config.spec.slice_depth
        )
        text = render(graph, parsed, computed)
        if not text.strip():
            return "", f"a slice for {cite} came out empty"
        return text, ""
    except NotATarget as e:
        return "", f"{cite} is not a unit this project's spec defines ({e})"
    except Exception as e:  # noqa: BLE001 - see the docstring
        return "", (
            f"rite could not produce the spec text for {cite} ({type(e).__name__}: {e})"
        )
