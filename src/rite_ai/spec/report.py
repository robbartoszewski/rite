"""Whether a spec is worth splitting, decided before anything is spent on it.

Computed from the parse and the reference graph alone — no model, no tokens —
so rite can tell a project that digesting its spec will not help *before* a
session spends a digest on it. That verdict is a supported outcome, not a
failure: the project keeps pointing Workers at the whole spec, which is what
the spec pointer is for.

The rule: **refuse when the p90 slice a Worker would load, pinned hubs
included, is more than a quarter of the spec.** Two very different specs refuse
under it, for the same practical reason. A densely interlinked spec does,
because its units are not independently useful. So does a small one — a
freshly written `/spec` spec runs to a few dozen lines, and a slice of it is
most of it; reading the whole file costs less than maintaining a digest of it.
A spec with no headings refuses outright: splitting it would mean inventing
structure.

Measured when this was written: rite's own spec, 4,027 lines, loads 5.6% at
the median and 9.3% at p90 and decomposes; a spec in `/spec`'s skeleton with six
decisions, 32 lines, loads 59% at p90 and does not.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from rite_ai.spec.graph import (
    DEFAULT_HUB_MIN_IN,
    DEFAULT_INDEX_MIN_OUT,
    DEFAULT_PIN_COUNT,
    HUB,
    INDEX,
    build_graph,
    classify,
)
from rite_ai.spec.slice import compute_slice
from rite_ai.spec.units import DECISION, PREAMBLE_ID, SECTION, Parsed

DEFAULT_REFUSE_ABOVE = 0.25


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


@dataclass(frozen=True)
class Report:
    files: tuple[str, ...]
    total_lines: int
    sections: int
    decisions: int
    median_section_lines: float
    hubs: tuple[str, ...]
    preamble_lines: int
    indexes: tuple[str, ...]
    p50: float | None
    p90: float | None
    refuse_above: float
    decomposes: bool
    reason: str
    problems: tuple[str, ...] = field(default=())


def decomposition_report(
    parsed: Parsed,
    *,
    refuse_above: float = DEFAULT_REFUSE_ABOVE,
    pin_count: int = DEFAULT_PIN_COUNT,
    hub_min_in: int = DEFAULT_HUB_MIN_IN,
    index_min_out: int = DEFAULT_INDEX_MIN_OUT,
) -> Report:
    sections = [u for u in parsed.units if u.kind == SECTION]
    decisions = [u for u in parsed.units if u.kind == DECISION]
    graph = build_graph(parsed)
    kinds = classify(
        graph, pin_count=pin_count, hub_min_in=hub_min_in, index_min_out=index_min_out
    )
    hubs = tuple(u for u in graph.units if kinds[u] == HUB)
    indexes = tuple(u for u in graph.units if kinds[u] == INDEX)
    targets = [u for u in graph.units if kinds[u] != INDEX]

    def build(p50, p90, decomposes, reason) -> Report:
        return Report(
            files=tuple(parsed.files),
            total_lines=parsed.total_lines,
            sections=len(sections),
            decisions=len(decisions),
            median_section_lines=(
                statistics.median(u.lines for u in sections) if sections else 0
            ),
            hubs=hubs,
            preamble_lines=sum(graph.units[h].lines for h in hubs),
            indexes=indexes,
            p50=p50,
            p90=p90,
            refuse_above=refuse_above,
            decomposes=decomposes,
            reason=reason,
            problems=tuple(parsed.problems),
        )

    # Text before the first heading is a unit too, so a document with no
    # headings at all still has one; it is not a heading.
    if not [u for u in sections if u.id != PREAMBLE_ID]:
        return build(
            None,
            None,
            False,
            "it has no headings, so splitting it would mean inventing structure",
        )
    if not targets:
        return build(
            None, None, False, "every unit is an index; there is nothing to slice"
        )

    ratios = [compute_slice(graph, kinds, t, parsed.total_lines).ratio for t in targets]
    p50, p90 = statistics.median(ratios), _p(ratios, 0.9)
    if p90 > refuse_above:
        return build(
            p50,
            p90,
            False,
            f"a Worker would still load {p90:.0%} of it at p90, above the "
            f"{refuse_above:.0%} limit — reading the whole file costs less",
        )
    return build(p50, p90, True, f"a Worker loads {p90:.1%} of it at p90")


def render(report: Report) -> str:
    lines = [
        f"{report.sections} sections, {report.decisions} decisions, "
        f"{report.total_lines} lines "
        f"(median section {report.median_section_lines:g} lines)",
    ]
    share = report.preamble_lines / report.total_lines if report.total_lines else 0
    lines.append(
        f"{len(report.hubs)} hubs pinned ({share:.1%} preamble), "
        f"{len(report.indexes)} index sections excluded"
    )
    if report.p50 is not None and report.p90 is not None:
        lines.append(f"projected slice: p50 {report.p50:.1%}, p90 {report.p90:.1%}")
    if report.decomposes:
        lines.append(f"✓ this spec decomposes — {report.reason}")
    else:
        lines.append(
            f"✗ this spec does not decompose — {report.reason}. Point Workers at the "
            "whole spec instead; that is what `rite spec add` already does."
        )
    lines.extend(f"note: {problem}" for problem in report.problems)
    return "\n".join(lines)
