"""What the spec digest does to specs that were NOT written for it.

rite's own SPEC.md has had a citation gate enforcing `§N` and `D-n` references
for months, so its reference graph is dense and its slices are small. That is
the flattered case. This measures the same thing against real design documents
from other projects, written with no such gate, and prints the numbers side by
side.

Run from anywhere:  uv run --project <rite checkout> python measure.py FILE...
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

from rite_ai.spec.graph import HUB, INDEX, build_graph, classify
from rite_ai.spec.report import decomposition_report
from rite_ai.spec.slice import NotATarget, compute_slice
from rite_ai.spec.units import DECISION, SECTION, parse_file


def measure(path: Path) -> dict:
    parsed = parse_file(path, source=path.name)
    graph = build_graph(parsed)
    kinds = classify(graph)
    report = decomposition_report(parsed)

    sections = [u for u in parsed.units if u.kind == SECTION]
    decisions = [u for u in parsed.units if u.kind == DECISION]
    # Citations only — the structural ancestor edges every unit gets for free
    # would make a document with no cross-references at all look connected.
    cites = sum(len(c) for c in graph.citations.values())
    dangling = sum(len(d) for d in graph.dangling.values())

    ratios = []
    for unit in parsed.units:
        if kinds.get(unit.id) == INDEX:
            continue
        try:
            ratios.append(
                compute_slice(graph, kinds, unit.id, parsed.total_lines).ratio
            )
        except NotATarget:
            continue
    return {
        "file": path.name,
        "lines": parsed.total_lines,
        "units": len(parsed.units),
        "sections": len(sections),
        "decisions": len(decisions),
        "cites": cites,
        "cites_per_unit": cites / len(parsed.units) if parsed.units else 0.0,
        "dangling": dangling,
        "hubs": sum(1 for k in kinds.values() if k == HUB),
        "indexes": sum(1 for k in kinds.values() if k == INDEX),
        "p50": statistics.median(ratios) if ratios else None,
        "p90": report.p90,
        "decomposes": report.decomposes,
        "reason": report.reason,
        "problems": len(parsed.problems),
    }


def main() -> None:
    rows = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        try:
            rows.append(measure(path))
        except Exception as e:  # a corpus file that cannot be read is data too
            print(f"{path.name}: FAILED — {type(e).__name__}: {e}")
    rows.sort(key=lambda r: -r["lines"])
    head = (
        f"{'document':<34}{'lines':>7}{'units':>7}{'D':>5}{'cites':>7}"
        f"{'c/unit':>8}{'hubs':>6}{'p50':>8}{'p90':>8}  verdict"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        p50 = f"{r['p50']:.1%}" if r["p50"] is not None else "—"
        p90 = f"{r['p90']:.1%}" if r["p90"] is not None else "—"
        verdict = "decomposes" if r["decomposes"] else "REFUSED"
        print(
            f"{r['file'][:33]:<34}{r['lines']:>7}{r['units']:>7}{r['decisions']:>5}"
            f"{r['cites']:>7}{r['cites_per_unit']:>8.2f}{r['hubs']:>6}"
            f"{p50:>8}{p90:>8}  {verdict}"
        )
    print()
    for r in rows:
        if not r["decomposes"]:
            print(f"{r['file']}: {r['reason']}")
    print()
    for r in rows:
        if r["problems"] or r["dangling"]:
            print(
                f"{r['file']}: {r['problems']} parse problem(s), "
                f"{r['dangling']} dangling citation(s)"
            )


if __name__ == "__main__":
    main()
